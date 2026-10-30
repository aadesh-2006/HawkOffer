"""Scheduler and monitoring orchestrator for career site ingestion."""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import async_session_factory
from app.repositories.company_repo import CompanyRepository
from app.repositories.scan_state_repo import ScanStateRepository
from app.scrapers.exceptions import RateLimitError, ScraperError
from app.scrapers.registry import ScraperRegistry
from app.scrapers.registry import registry as default_registry
from app.services.ingestion_service import IngestionResult, IngestionService

logger = logging.getLogger(__name__)


class MonitoringOrchestrator:
    """Platform-neutral scheduler and orchestrator for automated career-site scans."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        registry: ScraperRegistry | None = None,
        scheduler: AsyncIOScheduler | None = None,
        default_interval_minutes: int = 30,
        stale_threshold_seconds: int = 900,
        backoff_factor: float = 2.0,
        max_backoff_minutes: int = 240,
        jitter_range_seconds: tuple[float, float] = (5.0, 30.0),
        stagger_range_seconds: tuple[float, float] = (5.0, 60.0),
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

        # Per-company in-process lock to prevent overlapping runs within the same instance
        self._company_locks: dict[int, asyncio.Lock] = {}

    def _get_company_lock(self, company_id: int) -> asyncio.Lock:
        """Retrieve or create an asyncio.Lock for a specific company."""
        if company_id not in self._company_locks:
            self._company_locks[company_id] = asyncio.Lock()
        return self._company_locks[company_id]

    @property
    def is_running(self) -> bool:
        """Return True if the underlying scheduler is running."""
        return bool(self.scheduler.running)

    async def start(self) -> None:
        """Start the scheduler, recover stale scans, and synchronize active company jobs."""
        if not self.is_running:
            logger.info("Starting MonitoringOrchestrator scheduler...")
            self.scheduler.start()
        else:
            logger.debug("MonitoringOrchestrator is already running.")

        # 1. Recover any scans left in RUNNING state prior to startup
        recovered_count = await self.recover_stale_scans()
        if recovered_count > 0:
            logger.info("Startup recovery reset %d stale scans.", recovered_count)

        # 2. Synchronize company schedules
        await self.sync_company_schedules()

    async def shutdown(self, wait: bool = True) -> None:
        """Gracefully shut down the scheduler."""
        if self.is_running:
            logger.info("Shutting down MonitoringOrchestrator scheduler (wait=%s)...", wait)
            self.scheduler.shutdown(wait=wait)
            # Yield to event loop to allow AsyncIOScheduler callback to execute
            await asyncio.sleep(0)
        else:
            logger.debug("MonitoringOrchestrator scheduler is not running; shutdown skipped.")

    async def recover_stale_scans(self) -> int:
        """Identify and recover any scans stuck in RUNNING status past the timeout threshold."""
        async with self.session_factory() as session:
            repo = ScanStateRepository(session)
            count = await repo.recover_stale_running(
                stale_threshold_seconds=self.stale_threshold_seconds
            )
            await session.commit()
            return count

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
            # 1st failure uses base interval; 2nd failure doubles, etc.
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

                # Determine initial run time (staggered to prevent thundering herd)
                existing_job = self.scheduler.get_job(job_id)
                if existing_job is None:
                    # Respect future next_scan_at if valid, otherwise stagger
                    if state.next_scan_at is not None:
                        # Make timezone-aware if SQLite returned naive
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
        lock = self._get_company_lock(company_id)

        # In-process concurrency guard: skip if already running on this worker
        if lock.locked():
            logger.warning(
                "Scan for company %d is already running locally in this process. Skipping.",
                company_id,
            )
            return None

        async with lock:
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

                # Guard against concurrent multi-process execution or stale run
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

                # Determine if the run was a success or failure
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
