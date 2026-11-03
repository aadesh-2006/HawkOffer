"""Scheduler and monitoring orchestrator for career site ingestion and M5 background pipeline."""

import asyncio
import logging
import random
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database.session import async_session_factory
from app.models.outbox import SystemDrainState
from app.repositories.company_repo import CompanyRepository
from app.repositories.scan_state_repo import ScanStateRepository
from app.scrapers.exceptions import RateLimitError, ScraperError
from app.scrapers.registry import ScraperRegistry
from app.scrapers.registry import registry as default_registry
from app.services.backlog_drain_worker import BacklogDrainWorker, DrainRunResult
from app.services.discovery_worker import DiscoveryRunResult, JobDiscoveryWorker
from app.services.ingestion_service import IngestionResult, IngestionService
from app.services.lease_service import DistributedLeaseService
from app.services.reevaluation_worker import ProfileReevaluationWorker, ReevaluationRunResult

logger = logging.getLogger(__name__)

# Stable APScheduler Job IDs for M5 background workers
DISCOVERY_JOB_ID = "job_discovery"
REEVALUATION_JOB_ID = "profile_reevaluation"
BACKLOG_DRAIN_JOB_ID = "backlog_drain"


@dataclass
class CentralizedMonitoringResult:
    """Execution statistics for a centralized multi-tenant monitoring cycle."""

    status: str = "completed"
    message: str = "Centralized monitoring cycle completed"
    duration_ms: int = 0
    companies_targeted: int = 0
    companies_scraped: int = 0
    companies_failed: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_existing: int = 0
    discovery_evaluated: int = 0
    candidates_created: int = 0
    notifications_promoted: int = 0
    errors: list[str] = field(default_factory=list)


class MonitoringOrchestrator:
    """Platform-neutral scheduler and orchestrator for career scans and M5 pipeline workers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        registry: ScraperRegistry | None = None,
        scheduler: AsyncIOScheduler | None = None,
        discovery_worker: JobDiscoveryWorker | None = None,
        reevaluation_worker: ProfileReevaluationWorker | None = None,
        drain_worker: BacklogDrainWorker | None = None,
        default_interval_minutes: int = 30,
        stale_threshold_seconds: int = 900,
        backoff_factor: float = 2.0,
        max_backoff_minutes: int = 240,
        jitter_range_seconds: tuple[float, float] = (5.0, 30.0),
        stagger_range_seconds: tuple[float, float] = (5.0, 60.0),
        discovery_interval_seconds: int | None = None,
        reevaluation_interval_seconds: int | None = None,
        drain_interval_seconds: int | None = None,
        discovery_batch_size: int | None = None,
        reevaluation_chunk_size: int | None = None,
        drain_batch_size: int | None = None,
        scheduler_misfire_grace_time_seconds: int | None = None,
        scheduler_max_instances: int | None = None,
        enable_pipeline_jobs: bool = True,
    ) -> None:
        self.session_factory = session_factory
        self.scraper_registry = registry or default_registry
        self.scheduler: AsyncIOScheduler = scheduler or AsyncIOScheduler(timezone="UTC")
        self.default_interval_minutes = default_interval_minutes
        self.stale_threshold_seconds = stale_threshold_seconds
        self.backoff_factor = backoff_factor
        self.max_backoff_minutes = max_backoff_minutes
        self.jitter_range_seconds = jitter_range_seconds
        self.stagger_range_seconds = stagger_range_seconds

        # M5 pipeline intervals and batch sizing
        self.discovery_interval_seconds = (
            discovery_interval_seconds
            if discovery_interval_seconds is not None
            else settings.discovery_interval_seconds
        )
        self.reevaluation_interval_seconds = (
            reevaluation_interval_seconds
            if reevaluation_interval_seconds is not None
            else settings.reevaluation_interval_seconds
        )
        self.drain_interval_seconds = (
            drain_interval_seconds
            if drain_interval_seconds is not None
            else settings.drain_interval_seconds
        )

        self.discovery_batch_size = (
            discovery_batch_size
            if discovery_batch_size is not None
            else settings.discovery_batch_size
        )
        self.reevaluation_chunk_size = (
            reevaluation_chunk_size
            if reevaluation_chunk_size is not None
            else settings.reevaluation_chunk_size
        )
        self.drain_batch_size = (
            drain_batch_size if drain_batch_size is not None else settings.drain_batch_size
        )

        self.scheduler_misfire_grace_time_seconds = (
            scheduler_misfire_grace_time_seconds
            if scheduler_misfire_grace_time_seconds is not None
            else settings.scheduler_misfire_grace_time_seconds
        )
        self.scheduler_max_instances = (
            scheduler_max_instances
            if scheduler_max_instances is not None
            else settings.scheduler_max_instances
        )
        self.enable_pipeline_jobs = enable_pipeline_jobs

        # Validate configuration upfront
        self._validate_configuration()

        # Worker instances
        self.discovery_worker = discovery_worker or JobDiscoveryWorker(
            session_factory=self.session_factory,
            batch_size=self.discovery_batch_size,
        )
        self.reevaluation_worker = reevaluation_worker or ProfileReevaluationWorker(
            session_factory=self.session_factory,
            chunk_size=self.reevaluation_chunk_size,
        )
        self.drain_worker = drain_worker or BacklogDrainWorker(
            session_factory=self.session_factory,
            batch_size=self.drain_batch_size,
        )

        # Lifecycle gate: when True, all incoming worker executions are rejected
        self._is_shutting_down = False
        self._is_starting = False
        self._shutdown_requested = False
        self._active_shutdowns = 0
        self._generation = 0
        self._startup_lock = asyncio.Lock()

        # Per-company in-process lock to prevent overlapping runs within the same instance
        self._company_locks: dict[int, asyncio.Lock] = {}

        # In-process locks for M5 workers to guarantee single-instance mutual exclusion across
        # both scheduled triggers and manual out-of-band triggers in the event loop.
        self._discovery_lock = asyncio.Lock()
        self._reevaluation_lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()
        self._cron_monitor_lock = asyncio.Lock()

        # Zero-cost distributed lease service for cross-process multi-tenant coordination
        self.lease_service = DistributedLeaseService(self.session_factory)

    def _validate_configuration(self) -> None:
        """Validate that all scheduling parameters and batch sizes are strictly positive."""
        if self.discovery_interval_seconds <= 0:
            raise ValueError(
                f"discovery_interval_seconds must be positive, "
                f"got {self.discovery_interval_seconds}"
            )
        if self.reevaluation_interval_seconds <= 0:
            raise ValueError(
                f"reevaluation_interval_seconds must be positive, "
                f"got {self.reevaluation_interval_seconds}"
            )
        if self.drain_interval_seconds <= 0:
            raise ValueError(
                f"drain_interval_seconds must be positive, got {self.drain_interval_seconds}"
            )
        if self.discovery_batch_size <= 0:
            raise ValueError(
                f"discovery_batch_size must be positive, got {self.discovery_batch_size}"
            )
        if self.reevaluation_chunk_size <= 0:
            raise ValueError(
                f"reevaluation_chunk_size must be positive, got {self.reevaluation_chunk_size}"
            )
        if self.drain_batch_size <= 0:
            raise ValueError(f"drain_batch_size must be positive, got {self.drain_batch_size}")
        if self.scheduler_misfire_grace_time_seconds <= 0:
            raise ValueError(
                f"scheduler_misfire_grace_time_seconds must be positive, "
                f"got {self.scheduler_misfire_grace_time_seconds}"
            )
        if self.scheduler_max_instances <= 0:
            raise ValueError(
                f"scheduler_max_instances must be positive, got {self.scheduler_max_instances}"
            )
        if self.default_interval_minutes <= 0:
            raise ValueError(
                f"default_interval_minutes must be positive, got {self.default_interval_minutes}"
            )
        if self.stale_threshold_seconds <= 0:
            raise ValueError(
                f"stale_threshold_seconds must be positive, got {self.stale_threshold_seconds}"
            )
        if self.backoff_factor < 1.0:
            raise ValueError(f"backoff_factor must be >= 1.0, got {self.backoff_factor}")

    def _get_company_lock(self, company_id: int) -> asyncio.Lock:
        """Retrieve or create an asyncio.Lock for a specific company."""
        if company_id not in self._company_locks:
            self._company_locks[company_id] = asyncio.Lock()
        return self._company_locks[company_id]

    @property
    def is_running(self) -> bool:
        """Return True if the underlying scheduler is running."""
        return bool(self.scheduler.running)

    @property
    def is_lifecycle_gate_open(self) -> bool:
        """Return True if the orchestrator lifecycle gate is open for worker executions."""
        return not self._is_shutting_down

    def _check_shutdown_requested(self, call_generation: int) -> None:
        """Check if shutdown was requested while startup was in progress.

        Raises:
            RuntimeError: If shutdown was requested or is in progress.
        """
        if (
            self._active_shutdowns > 0
            or self._shutdown_requested
            or self._generation != call_generation
        ):
            raise RuntimeError("MonitoringOrchestrator startup aborted: shutdown was requested.")

    async def start(self) -> None:
        """Start scheduler, initialize drain state, recover scans, and register jobs.

        The lifecycle gate is kept closed throughout startup so that neither scheduled
        nor manually triggered worker runs can execute until the scheduler and all its
        prerequisites are fully and successfully initialized.
        If a shutdown request arrives during startup, the startup sequence cleanly aborts
        without starting the scheduler or opening the worker gate.
        If any step fails or is cancelled:
            - The lifecycle gate remains closed.
            - The scheduler is guaranteed stopped.
            - Partially registered jobs are removed.
            - A descriptive exception (or CancelledError) is propagated to the caller.
        """
        if self._active_shutdowns > 0 or self._shutdown_requested:
            logger.warning(
                "MonitoringOrchestrator startup rejected: shutdown is active or pending."
            )
            raise RuntimeError("MonitoringOrchestrator startup aborted: shutdown was requested.")

        if self.is_running:
            logger.debug("MonitoringOrchestrator is already running.")
            return

        call_generation = self._generation

        async with self._startup_lock:
            # If shutdown was requested, active, or finished while queued, abort
            if (
                self._active_shutdowns > 0
                or self._shutdown_requested
                or self._generation != call_generation
            ):
                logger.warning(
                    "MonitoringOrchestrator startup rejected: shutdown is active or completed."
                )
                raise RuntimeError(
                    "MonitoringOrchestrator startup aborted: shutdown was requested."
                )

            # Re-check under lock in case another task completed startup
            if self.is_running:
                logger.debug("MonitoringOrchestrator is already running.")
                return

            self._is_starting = True
            # 1. Keep the lifecycle gate closed throughout startup
            self._is_shutting_down = True

            try:
                # 2. Re-validate configuration before startup
                self._validate_configuration()
                self._check_shutdown_requested(call_generation)

                # 3. Ensure SystemDrainState initialization is valid BEFORE recurring jobs can run
                await self._ensure_system_drain_state()
                self._check_shutdown_requested(call_generation)

                # 4. Recover any scans left in RUNNING state prior to startup
                try:
                    recovered_count = await self.recover_stale_scans()
                    if recovered_count > 0:
                        logger.info("Startup recovery reset %d stale scans.", recovered_count)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Failed to recover stale scans on startup: %s", exc)
                    raise RuntimeError(f"Stale scan startup recovery failed: {exc}") from exc

                self._check_shutdown_requested(call_generation)

                # 5. Register background pipeline worker jobs
                if self.enable_pipeline_jobs:
                    self.register_pipeline_jobs()

                self._check_shutdown_requested(call_generation)

                # 6. Synchronize company schedules
                try:
                    await self.sync_company_schedules()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Failed to synchronize company schedules on startup: %s", exc)
                    raise RuntimeError(
                        f"Company schedule startup synchronization failed: {exc}"
                    ) from exc

                self._check_shutdown_requested(call_generation)

                # 7. Start the underlying scheduler now that all preconditions are met
                logger.info("Starting MonitoringOrchestrator scheduler...")
                self.scheduler.start()

                # 8. Only now open the lifecycle gate to permit worker executions
                self._is_shutting_down = False
                logger.info(
                    "MonitoringOrchestrator startup completed successfully. Lifecycle gate opened."
                )
            except asyncio.CancelledError:
                self._is_shutting_down = True
                if self.is_running:
                    self.scheduler.shutdown(wait=False)
                with suppress(Exception):
                    self.scheduler.remove_all_jobs()
                raise
            except Exception:
                self._is_shutting_down = True
                if self.is_running:
                    self.scheduler.shutdown(wait=False)
                with suppress(Exception):
                    self.scheduler.remove_all_jobs()
                raise
            finally:
                self._is_starting = False

    async def shutdown(self, wait: bool = True) -> None:
        """Gracefully shut down scheduler and wait for in-flight executions.

        The lifecycle gate is closed immediately to reject any new worker executions,
        any in-progress startup is coordinated with and aborted safely without deadlocks,
        and all in-flight pipeline workers (both scheduled and manually triggered) and company
        scans are cleanly waited for when wait=True.
        Startup admission remains closed throughout the entire shutdown operation until all
        active shutdowns finish.
        """
        # 1. Close lifecycle gate, register active shutdown, and signal shutdown immediately
        self._is_shutting_down = True
        self._shutdown_requested = True
        self._active_shutdowns += 1

        try:
            # 2. Coordinate with startup: wait for any in-progress startup to finish or abort
            async with self._startup_lock:
                # 3. Stop scheduler from dispatching new jobs
                if self.is_running:
                    logger.info("Shutting down MonitoringOrchestrator scheduler (wait=%s)...", wait)
                    self.scheduler.shutdown(wait=wait)
                    # Yield to event loop to allow AsyncIOScheduler callback to transition state
                    await asyncio.sleep(0)
                else:
                    logger.debug(
                        "MonitoringOrchestrator scheduler is not running; shutdown skipped."
                    )

                # Ensure any registered jobs are removed from scheduler
                with suppress(Exception):
                    self.scheduler.remove_all_jobs()

            # 4. Handle in-flight worker executions if wait is requested
            if wait:
                # Wait for any active in-flight pipeline worker executions to complete cleanly
                for lock_name, lock in [
                    ("JobDiscoveryWorker", self._discovery_lock),
                    ("ProfileReevaluationWorker", self._reevaluation_lock),
                    ("BacklogDrainWorker", self._drain_lock),
                ]:
                    if lock.locked():
                        logger.info("Waiting for in-flight %s execution to finish...", lock_name)
                        async with lock:
                            pass

                # Wait for any in-flight company scans to complete cleanly
                for company_id, lock in list(self._company_locks.items()):
                    if lock.locked():
                        logger.info(
                            "Waiting for in-flight scan for company %d to finish...",
                            company_id,
                        )
                        async with lock:
                            pass
        except asyncio.CancelledError:
            self._is_shutting_down = True
            if self.is_running:
                self.scheduler.shutdown(wait=False)
            with suppress(Exception):
                self.scheduler.remove_all_jobs()
            raise
        finally:
            self._active_shutdowns -= 1
            if self._active_shutdowns == 0:
                self._generation += 1
                self._shutdown_requested = False

    async def _ensure_system_drain_state(self) -> None:
        """Validate or initialize the singleton SystemDrainState row.

        Raises:
            RuntimeError: If SystemDrainState cannot be retrieved, created, or has an invalid state.
        """
        try:
            async with self.session_factory() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                state = await SystemDrainState.get_or_create(session)
                if state is None or state.id != 1 or state.drain_counter < 0:
                    raise ValueError(f"Invalid SystemDrainState row encountered: {state}")
                await session.commit()
            logger.info(
                "SystemDrainState validated and initialized successfully (drain_counter=%d).",
                state.drain_counter,
            )
        except Exception as exc:
            logger.error("Failed to initialize SystemDrainState on startup: %s", exc)
            raise RuntimeError(f"SystemDrainState startup initialization failed: {exc}") from exc

    async def recover_stale_scans(self) -> int:
        """Identify and recover any scans stuck in RUNNING status past the timeout threshold."""
        async with self.session_factory() as session:
            repo = ScanStateRepository(session)
            count = await repo.recover_stale_running(
                stale_threshold_seconds=self.stale_threshold_seconds
            )
            await session.commit()
            return count

    # =========================================================================
    # M5 PIPELINE JOBS REGISTRATION & EXECUTION
    # =========================================================================

    def register_pipeline_jobs(self) -> None:
        """Register recurring background pipeline worker jobs in APScheduler."""
        # 1. Job Discovery Worker
        self.scheduler.add_job(
            func=self.run_discovery_worker,
            id=DISCOVERY_JOB_ID,
            name="Job Discovery Worker",
            trigger=IntervalTrigger(seconds=self.discovery_interval_seconds, timezone="UTC"),
            replace_existing=True,
            max_instances=self.scheduler_max_instances,
            coalesce=True,
            misfire_grace_time=self.scheduler_misfire_grace_time_seconds,
        )
        logger.info(
            "Registered scheduled job '%s' (%ds interval, max_instances=%d)",
            DISCOVERY_JOB_ID,
            self.discovery_interval_seconds,
            self.scheduler_max_instances,
        )

        # 2. Profile Reevaluation Worker
        self.scheduler.add_job(
            func=self.run_reevaluation_worker,
            id=REEVALUATION_JOB_ID,
            name="Profile Reevaluation Worker",
            trigger=IntervalTrigger(seconds=self.reevaluation_interval_seconds, timezone="UTC"),
            replace_existing=True,
            max_instances=self.scheduler_max_instances,
            coalesce=True,
            misfire_grace_time=self.scheduler_misfire_grace_time_seconds,
        )
        logger.info(
            "Registered scheduled job '%s' (%ds interval, max_instances=%d)",
            REEVALUATION_JOB_ID,
            self.reevaluation_interval_seconds,
            self.scheduler_max_instances,
        )

        # 3. Backlog Drain Worker
        self.scheduler.add_job(
            func=self.run_backlog_drain_worker,
            id=BACKLOG_DRAIN_JOB_ID,
            name="Backlog Drain Worker",
            trigger=IntervalTrigger(seconds=self.drain_interval_seconds, timezone="UTC"),
            replace_existing=True,
            max_instances=self.scheduler_max_instances,
            coalesce=True,
            misfire_grace_time=self.scheduler_misfire_grace_time_seconds,
        )
        logger.info(
            "Registered scheduled job '%s' (%ds interval, max_instances=%d)",
            BACKLOG_DRAIN_JOB_ID,
            self.drain_interval_seconds,
            self.scheduler_max_instances,
        )

    async def run_discovery_worker(self) -> DiscoveryRunResult | None:
        """Execute one cycle of JobDiscoveryWorker with lock isolation and observability."""
        if self._is_shutting_down:
            logger.warning("JobDiscoveryWorker skipped: orchestrator is shutting down.")
            return None

        lock = self._discovery_lock
        if lock.locked():
            logger.warning("JobDiscoveryWorker is already running locally. Skipping execution.")
            return None

        async with lock:
            if self._is_shutting_down:
                logger.warning(
                    "JobDiscoveryWorker aborted before run: orchestrator is shutting down."
                )
                return None

            worker_name = "JobDiscoveryWorker"
            start_time = datetime.now(UTC)
            start_perf = time.perf_counter()
            logger.info("Starting %s at %s", worker_name, start_time.isoformat())
            try:
                result = await self.discovery_worker.run_once()
                duration = time.perf_counter() - start_perf
                logger.info(
                    "Completed %s in %.3fs: claimed=%d, evaluated=%d, matches=%d, "
                    "processed=%d, failed=%d, stale_skips=%d",
                    worker_name,
                    duration,
                    result.claimed_count,
                    result.evaluated_jobs,
                    result.candidate_matches,
                    result.processed_outbox_count,
                    result.failed_outbox_count,
                    result.stale_lease_skips,
                )
                return result
            except Exception as exc:
                duration = time.perf_counter() - start_perf
                logger.exception("Error executing %s after %.3fs: %s", worker_name, duration, exc)
                return None

    async def run_reevaluation_worker(self) -> ReevaluationRunResult | None:
        """Execute one cycle of ProfileReevaluationWorker with lock isolation and observability."""
        if self._is_shutting_down:
            logger.warning("ProfileReevaluationWorker skipped: orchestrator is shutting down.")
            return None

        lock = self._reevaluation_lock
        if lock.locked():
            logger.warning(
                "ProfileReevaluationWorker is already running locally. Skipping execution."
            )
            return None

        async with lock:
            if self._is_shutting_down:
                logger.warning(
                    "ProfileReevaluationWorker aborted before run: orchestrator is shutting down."
                )
                return None

            worker_name = "ProfileReevaluationWorker"
            start_time = datetime.now(UTC)
            start_perf = time.perf_counter()
            logger.info("Starting %s at %s", worker_name, start_time.isoformat())
            try:
                result = await self.reevaluation_worker.run_once()
                duration = time.perf_counter() - start_perf
                logger.info(
                    "Completed %s in %.3fs: tasks_claimed=%d, chunks_processed=%d, "
                    "jobs_evaluated=%d, candidates_created=%d, tasks_completed=%d, "
                    "tasks_failed=%d, tasks_superseded=%d, stale_skips=%d",
                    worker_name,
                    duration,
                    result.tasks_claimed,
                    result.chunks_processed,
                    result.jobs_evaluated,
                    result.candidates_created,
                    result.tasks_completed,
                    result.tasks_failed,
                    result.tasks_superseded,
                    result.stale_lease_skips,
                )
                return result
            except Exception as exc:
                duration = time.perf_counter() - start_perf
                logger.exception("Error executing %s after %.3fs: %s", worker_name, duration, exc)
                return None

    async def run_backlog_drain_worker(self) -> DrainRunResult | None:
        """Execute one cycle of BacklogDrainWorker with lock isolation and observability."""
        if self._is_shutting_down:
            logger.warning("BacklogDrainWorker skipped: orchestrator is shutting down.")
            return None

        lock = self._drain_lock
        if lock.locked():
            logger.warning("BacklogDrainWorker is already running locally. Skipping execution.")
            return None

        async with lock:
            if self._is_shutting_down:
                logger.warning(
                    "BacklogDrainWorker aborted before run: orchestrator is shutting down."
                )
                return None

            worker_name = "BacklogDrainWorker"
            start_time = datetime.now(UTC)
            start_perf = time.perf_counter()
            logger.info("Starting %s at %s", worker_name, start_time.isoformat())
            try:
                result = await self.drain_worker.run_once()
                duration = time.perf_counter() - start_perf
                logger.info(
                    "Completed %s in %.3fs: claimed=%d, promoted=%d, already_promoted=%d, "
                    "stale_revision=%d, dropped=%d, stale_skips=%d, failed=%d, final_counter=%d",
                    worker_name,
                    duration,
                    result.claimed_count,
                    result.promoted_count,
                    result.already_promoted_count,
                    result.stale_revision_count,
                    result.dropped_count,
                    result.stale_lease_skips,
                    result.failed_count,
                    result.final_drain_counter,
                )
                return result
            except Exception as exc:
                duration = time.perf_counter() - start_perf
                logger.exception("Error executing %s after %.3fs: %s", worker_name, duration, exc)
                return None

    async def trigger_discovery_now(self) -> DiscoveryRunResult | None:
        """Trigger an immediate out-of-band discovery run."""
        if self._is_shutting_down:
            logger.warning("Manual discovery trigger rejected: orchestrator is shutting down.")
            return None
        logger.info("Manually triggering immediate JobDiscoveryWorker run")
        return await self.run_discovery_worker()

    async def trigger_reevaluation_now(self) -> ReevaluationRunResult | None:
        """Trigger an immediate out-of-band profile reevaluation run."""
        if self._is_shutting_down:
            logger.warning("Manual reevaluation trigger rejected: orchestrator is shutting down.")
            return None
        logger.info("Manually triggering immediate ProfileReevaluationWorker run")
        return await self.run_reevaluation_worker()

    async def trigger_backlog_drain_now(self) -> DrainRunResult | None:
        """Trigger an immediate out-of-band backlog drain run."""
        if self._is_shutting_down:
            logger.warning("Manual drain trigger rejected: orchestrator is shutting down.")
            return None
        logger.info("Manually triggering immediate BacklogDrainWorker run")
        return await self.run_backlog_drain_worker()

    async def run_centralized_monitoring_cycle(self) -> CentralizedMonitoringResult:
        """Execute one complete centralized multi-tenant monitoring cycle.

        1. Selects distinct active companies that have active monitoring demand from at least
           one subscribed user (or active curated/desktop companies).
        2. Coordinates across multiple processes using a database-backed distributed lease
           ('centralized_monitoring') with an in-process local lock fast-path.
        3. Scrapes each eligible company exactly once with lock isolation, scan state tracking,
           and exponential backoff. Handles partial failures without aborting the batch.
        4. Runs JobDiscoveryWorker to evaluate newly ingested jobs against subscribed users'
           active profiles, inserting matching candidates into NotificationBacklog.
        5. Runs BacklogDrainWorker to fairly promote candidates into user Notification records
           with per-user deduplication.
        6. Safely and deterministically releases the distributed lease in a finally block.
        """
        if self._is_shutting_down:
            logger.warning("Centralized monitoring cycle skipped: orchestrator is shutting down.")
            return CentralizedMonitoringResult(
                status="skipped_shutting_down",
                message="Monitoring orchestrator is shutting down",
            )

        if self._cron_monitor_lock.locked():
            logger.warning(
                "Centralized monitoring cycle is already in progress locally. Skipping execution."
            )
            return CentralizedMonitoringResult(
                status="skipped_overlapping",
                message="A monitoring run is already in progress",
            )

        async with self._cron_monitor_lock:
            # Cross-process coordination via database-backed distributed lease
            lease_token = await self.lease_service.acquire_lease(
                name="centralized_monitoring",
                ttl_seconds=settings.monitoring_lease_ttl_seconds,
            )
            if not lease_token:
                logger.warning(
                    "Distributed lease for 'centralized_monitoring' is currently held. "
                    "Skipping cross-process overlapping execution."
                )
                return CentralizedMonitoringResult(
                    status="skipped_overlapping",
                    message="A monitoring run is currently in progress across processes",
                )

            start_perf = time.perf_counter()
            result = CentralizedMonitoringResult()
            errors: list[str] = []

            try:
                # Step 1: Select distinct companies with active demand
                async with self.session_factory() as session:
                    company_repo = CompanyRepository(session)
                    targets = await company_repo.list_active_monitored_targets()

                result.companies_targeted = len(targets)
                logger.info(
                    "Starting centralized monitoring cycle for %d company targets", len(targets)
                )

                # Step 2: Scrape each eligible company once
                for company in targets:
                    try:
                        scan_res = await self.run_company_scan(company.id)
                        if scan_res is not None:
                            if scan_res.failed_records > 0 and scan_res.total_fetched == 0:
                                result.companies_failed += 1
                                if scan_res.errors:
                                    errors.extend([f"{company.name}: {e}" for e in scan_res.errors])
                            else:
                                result.companies_scraped += 1
                                result.jobs_new += scan_res.newly_inserted
                                result.jobs_updated += scan_res.updated_records
                                result.jobs_existing += scan_res.already_existing
                        else:
                            result.companies_failed += 1
                            errors.append(f"{company.name}: Scan skipped or returned no result")
                    except Exception as exc:
                        logger.exception(
                            "Failed scraping company %d (%s): %s", company.id, company.name, exc
                        )
                        result.companies_failed += 1
                        errors.append(f"{company.name}: {exc}")

                # Step 3: Fan-out discovery worker
                try:
                    discovery_res = await self.run_discovery_worker()
                    if discovery_res is not None:
                        result.discovery_evaluated = discovery_res.evaluated_jobs
                        result.candidates_created = discovery_res.candidate_matches
                    else:
                        errors.append("DiscoveryWorker execution failed or was skipped")
                except Exception as exc:
                    logger.exception(
                        "Error during discovery worker execution in monitoring cycle: %s", exc
                    )
                    errors.append(f"DiscoveryWorker: {exc}")

                # Step 4: Backlog drain worker
                try:
                    drain_res = await self.run_backlog_drain_worker()
                    if drain_res is not None:
                        result.notifications_promoted = drain_res.promoted_count
                    else:
                        errors.append("BacklogDrainWorker execution failed or was skipped")
                except Exception as exc:
                    logger.exception(
                        "Error during backlog drain worker execution in monitoring cycle: %s", exc
                    )
                    errors.append(f"BacklogDrainWorker: {exc}")

            except Exception as exc:
                logger.exception("Unexpected exception in centralized monitoring cycle: %s", exc)
                errors.append(f"CentralizedMonitoringCycle: {exc}")
            finally:
                # Always release distributed lease safely
                await self.lease_service.release_lease("centralized_monitoring", lease_token)

            duration_ms = int((time.perf_counter() - start_perf) * 1000)
            result.duration_ms = duration_ms
            result.errors = errors

            if errors and result.companies_scraped == 0 and result.companies_targeted > 0:
                result.status = "failed"
                result.message = "Centralized monitoring cycle failed"
            elif errors:
                result.status = "partial_failure"
                result.message = "Centralized monitoring cycle completed with partial failures"
            else:
                result.status = "completed"
                result.message = "Centralized monitoring cycle completed successfully"

            return result

    def pause_pipeline_job(self, job_id: str) -> bool:
        """Pause a scheduled pipeline job by its stable ID."""
        job = self.scheduler.get_job(job_id)
        if job:
            job.pause()
            logger.info("Paused scheduled pipeline job %s", job_id)
            return True
        return False

    def resume_pipeline_job(self, job_id: str) -> bool:
        """Resume a paused scheduled pipeline job by its stable ID."""
        job = self.scheduler.get_job(job_id)
        if job:
            job.resume()
            logger.info("Resumed scheduled pipeline job %s", job_id)
            return True
        return False

    def get_pipeline_status(self) -> dict[str, Any]:
        """Retrieve runtime schedule status of all M5 pipeline worker jobs."""
        status: dict[str, Any] = {}
        for job_id, key in [
            (DISCOVERY_JOB_ID, "discovery"),
            (REEVALUATION_JOB_ID, "reevaluation"),
            (BACKLOG_DRAIN_JOB_ID, "backlog_drain"),
        ]:
            job = self.scheduler.get_job(job_id)
            status[key] = {
                "job_id": job_id,
                "scheduled": job is not None,
                "next_run_time": (
                    job.next_run_time.isoformat() if job and job.next_run_time else None
                ),
                "paused": getattr(job, "next_run_time", None) is None if job else None,
            }
        return status

    # =========================================================================
    # EXISTING COMPANY SCAN SCHEDULING (PRESERVED)
    # =========================================================================

    def compute_next_run(
        self,
        base_interval_minutes: int,
        consecutive_failures: int = 0,
        apply_jitter: bool = True,
    ) -> datetime:
        """Calculate the next execution timestamp using exponential backoff and jitter."""
        if consecutive_failures <= 0:
            total_minutes = float(base_interval_minutes)
        else:
            exponent = max(0, consecutive_failures - 1)
            total_minutes = min(
                base_interval_minutes * (self.backoff_factor**exponent),
                float(self.max_backoff_minutes),
            )

        jitter_sec = 0.0
        if apply_jitter and self.jitter_range_seconds[1] > 0:
            jitter_sec = random.uniform(self.jitter_range_seconds[0], self.jitter_range_seconds[1])

        return datetime.now(UTC) + timedelta(minutes=total_minutes, seconds=jitter_sec)

    async def sync_company_schedules(self) -> None:
        """Synchronize APScheduler jobs with active companies in the database."""
        async with self.session_factory() as session:
            company_repo = CompanyRepository(session)
            state_repo = ScanStateRepository(session)
            active_companies = await company_repo.list_active()

            active_ids = {c.id for c in active_companies}
            now = datetime.now(UTC)

            for company in active_companies:
                job_id = f"scan_company_{company.id}"
                state = await state_repo.get_or_create(
                    company.id, scan_interval_minutes=self.default_interval_minutes
                )
                interval = state.scan_interval_minutes

                existing_job = self.scheduler.get_job(job_id)
                if existing_job is None:
                    if state.next_scan_at is not None:
                        next_at = (
                            state.next_scan_at.replace(tzinfo=UTC)
                            if state.next_scan_at.tzinfo is None
                            else state.next_scan_at
                        )
                        if next_at > now:
                            initial_run = next_at
                        else:
                            stagger = random.uniform(
                                self.stagger_range_seconds[0], self.stagger_range_seconds[1]
                            )
                            initial_run = now + timedelta(seconds=stagger)
                    else:
                        stagger = random.uniform(
                            self.stagger_range_seconds[0], self.stagger_range_seconds[1]
                        )
                        initial_run = now + timedelta(seconds=stagger)

                    self.scheduler.add_job(
                        func=self.run_company_scan,
                        args=[company.id],
                        id=job_id,
                        name=f"Scan company {company.name} ({company.id})",
                        trigger=IntervalTrigger(minutes=interval, timezone="UTC"),
                        next_run_time=initial_run,
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True,
                        misfire_grace_time=300,
                    )
                    logger.info(
                        "Registered scheduled job '%s' for '%s' (%dm interval, first: %s)",
                        job_id,
                        company.name,
                        interval,
                        initial_run.isoformat(),
                    )

            # Remove jobs for companies that are no longer active
            current_jobs = self.scheduler.get_jobs()
            for job in current_jobs:
                if job.id.startswith("scan_company_"):
                    try:
                        cid = int(job.id.replace("scan_company_", ""))
                        if cid not in active_ids:
                            logger.info(
                                "Removing scheduled job '%s' for inactive/removed company %d",
                                job.id,
                                cid,
                            )
                            job.remove()
                    except ValueError:
                        continue

            await session.commit()

    async def run_company_scan(self, company_id: int) -> IngestionResult | None:
        """Execute a single scan for a company with lock isolation and state management."""
        if self._is_shutting_down:
            logger.warning(
                "Scan for company %d skipped: orchestrator is shutting down.",
                company_id,
            )
            return None

        lock = self._get_company_lock(company_id)

        if lock.locked():
            logger.warning(
                "Scan for company %d is already running locally in this process. Skipping.",
                company_id,
            )
            return None

        async with lock:
            if self._is_shutting_down:
                logger.warning(
                    "Scan for company %d aborted: orchestrator is shutting down.",
                    company_id,
                )
                return None

            # Phase 1: Verify company and transition scan state to RUNNING
            async with self.session_factory() as session:
                company_repo = CompanyRepository(session)
                state_repo = ScanStateRepository(session)

                company = await company_repo.get_by_id(company_id)
                if not company:
                    logger.warning(
                        "Company %d not found during scan execution; aborting.", company_id
                    )
                    return None
                if not company.is_active:
                    logger.info("Company %d is inactive; aborting scan.", company_id)
                    return None

                state = await state_repo.get_or_create(
                    company_id, scan_interval_minutes=self.default_interval_minutes
                )

                now = datetime.now(UTC)
                if state.status == "RUNNING" and state.last_scan_started_at is not None:
                    started_at = (
                        state.last_scan_started_at.replace(tzinfo=UTC)
                        if state.last_scan_started_at.tzinfo is None
                        else state.last_scan_started_at
                    )
                    elapsed = (now - started_at).total_seconds()
                    if elapsed < self.stale_threshold_seconds:
                        logger.warning(
                            "Company %d is RUNNING in DB (%ds ago). Skipping overlapping run.",
                            company_id,
                            int(elapsed),
                        )
                        return None
                    else:
                        logger.warning(
                            "Company %d has stale RUNNING status (%ds old). Overriding.",
                            company_id,
                            int(elapsed),
                        )

                await state_repo.mark_running(company_id, started_at=now)
                await session.commit()
                scan_interval_minutes = state.scan_interval_minutes
                consecutive_failures = state.consecutive_failures

            # Phase 2: Execute ingestion in a dedicated transaction
            result: IngestionResult | None = None
            error_message: str | None = None
            is_rate_limited: bool = False
            is_failure: bool = False

            try:
                async with self.session_factory() as ing_session:
                    service = IngestionService(ing_session, self.scraper_registry)
                    result = await service.ingest_company(company_id)
                    await ing_session.commit()

                if (
                    result.failed_records > 0
                    and result.total_fetched == 0
                    and len(result.errors) > 0
                ):
                    is_failure = True
                    error_message = "; ".join(result.errors[:3])
                    is_rate_limited = any(
                        "429" in err.lower() or "rate limit" in err.lower() for err in result.errors
                    )
                else:
                    is_failure = False

            except RateLimitError as exc:
                is_failure = True
                is_rate_limited = True
                error_message = f"Rate limited: {exc}"
                logger.warning("Scraper rate limited for company %d: %s", company_id, exc)
            except ScraperError as exc:
                is_failure = True
                error_message = f"Scraper error: {exc}"
                logger.error("Scraper error for company %d: %s", company_id, exc)
            except Exception as exc:
                is_failure = True
                error_message = f"Unexpected error during scan: {exc}"
                logger.exception("Unexpected error scanning company %d: %s", company_id, exc)

            # Phase 3: Update persistent state and reschedule in APScheduler
            completed_at = datetime.now(UTC)
            async with self.session_factory() as session:
                state_repo = ScanStateRepository(session)

                if is_failure:
                    new_failures = consecutive_failures + 1
                    next_run = self.compute_next_run(
                        base_interval_minutes=scan_interval_minutes,
                        consecutive_failures=new_failures,
                        apply_jitter=True,
                    )
                    await state_repo.mark_failure(
                        company_id=company_id,
                        error=error_message or "Unknown failure",
                        completed_at=completed_at,
                        next_scan_at=next_run,
                        is_rate_limited=is_rate_limited,
                    )
                    logger.info(
                        "Company %d scan failed (failures: %d, rate_limited: %s). Next scan: %s",
                        company_id,
                        new_failures,
                        is_rate_limited,
                        next_run.isoformat(),
                    )
                else:
                    next_run = self.compute_next_run(
                        base_interval_minutes=scan_interval_minutes,
                        consecutive_failures=0,
                        apply_jitter=True,
                    )
                    await state_repo.mark_success(
                        company_id=company_id,
                        completed_at=completed_at,
                        next_scan_at=next_run,
                    )
                    logger.info(
                        "Company %d scan completed successfully. Next scan: %s",
                        company_id,
                        next_run.isoformat(),
                    )

                await session.commit()

            # Phase 4: Adjust the next run time in APScheduler to respect backoff/jitter
            job = self.scheduler.get_job(f"scan_company_{company_id}")
            if job is not None and self.is_running:
                try:
                    job.modify(next_run_time=next_run)
                except Exception as mod_err:
                    logger.debug(
                        "Could not adjust APScheduler next_run_time for job %s: %s",
                        job.id,
                        mod_err,
                    )

            return result

    async def trigger_scan_now(self, company_id: int) -> IngestionResult | None:
        """Trigger an immediate out-of-band scan for a company."""
        if self._is_shutting_down:
            logger.warning("Manual scan rejected: orchestrator is shutting down.")
            return None
        logger.info("Manually triggering immediate scan for company %d", company_id)
        return await self.run_company_scan(company_id)

    def pause_company(self, company_id: int) -> bool:
        """Pause scheduled scans for a company."""
        job = self.scheduler.get_job(f"scan_company_{company_id}")
        if job:
            job.pause()
            logger.info("Paused scheduled scans for company %d (job %s)", company_id, job.id)
            return True
        return False

    def resume_company(self, company_id: int) -> bool:
        """Resume paused scheduled scans for a company."""
        job = self.scheduler.get_job(f"scan_company_{company_id}")
        if job:
            job.resume()
            logger.info("Resumed scheduled scans for company %d (job %s)", company_id, job.id)
            return True
        return False

    async def get_schedule_status(self, company_id: int) -> dict[str, Any] | None:
        """Retrieve runtime schedule and persistent state status for a company."""
        async with self.session_factory() as session:
            state_repo = ScanStateRepository(session)
            company_repo = CompanyRepository(session)

            company = await company_repo.get_by_id(company_id)
            if not company:
                return None

            state = await state_repo.get_by_company_id(company_id)

        job = self.scheduler.get_job(f"scan_company_{company_id}")
        job_info: dict[str, Any] = {
            "scheduled": job is not None,
            "job_id": job.id if job else None,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "paused": getattr(job, "next_run_time", None) is None if job else None,
        }

        return {
            "company_id": company.id,
            "company_name": company.name,
            "platform": company.platform,
            "is_active": company.is_active,
            "scan_state": {
                "status": state.status if state else "UNKNOWN",
                "interval_minutes": state.scan_interval_minutes
                if state
                else self.default_interval_minutes,
                "consecutive_failures": state.consecutive_failures if state else 0,
                "total_scans": state.total_scans if state else 0,
                "total_errors": state.total_errors if state else 0,
                "last_error": state.last_error if state else None,
                "last_scan_started_at": state.last_scan_started_at.isoformat()
                if state and state.last_scan_started_at
                else None,
                "last_scan_completed_at": state.last_scan_completed_at.isoformat()
                if state and state.last_scan_completed_at
                else None,
                "next_scan_at": state.next_scan_at.isoformat()
                if state and state.next_scan_at
                else None,
            },
            "job_info": job_info,
        }
