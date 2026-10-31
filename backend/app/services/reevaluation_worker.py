"""Historical job reevaluation worker consuming ProfileReevaluationTask records."""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utc_now
from app.database.session import async_session_factory
from app.filtering.engine import JobFilteringEngine
from app.filtering.models import EligibilityStatus
from app.models.job import Job
from app.models.outbox import (
    BacklogStatus,
    NotificationBacklog,
    ProfileReevaluationTask,
    ReevaluationStatus,
)
from app.models.preference import PreferenceProfile

logger = logging.getLogger(__name__)


class ChunkCommitStatus(StrEnum):
    """Result status of a single reevaluation chunk processing iteration."""

    COMMITTED = "COMMITTED"
    NO_MORE_JOBS = "NO_MORE_JOBS"
    SUPERSEDED = "SUPERSEDED"
    STALE_LEASE = "STALE_LEASE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class ClaimedReevaluationTask:
    """Snapshot of a claimed reevaluation task held under an active lease."""

    id: int
    profile_id: int
    profile_revision: int
    max_job_id: int
    last_processed_job_id: int
    lease_token: str
    attempt_count: int


@dataclass
class ReevaluationCandidate:
    """Evaluated matching candidate ready for idempotent backlog insertion."""

    job_id: int
    profile_id: int
    profile_revision: int
    score: int


@dataclass
class ReevaluationRunResult:
    """Statistics collected during a reevaluation worker execution cycle."""

    tasks_claimed: int = 0
    chunks_processed: int = 0
    jobs_evaluated: int = 0
    candidates_created: int = 0
    tasks_completed: int = 0
    tasks_superseded: int = 0
    stale_lease_skips: int = 0
    tasks_failed: int = 0


class ProfileReevaluationWorker:
    """Worker service that processes historical Job reevaluation against updated profiles.

    Architecture:
    - Phase A (Claim): Claim eligible PENDING or expired RUNNING tasks under BEGIN IMMEDIATE.
    - Phase B (Keyset Chunk): Read bounded chunk (last_processed_job_id < Job.id <= max_job_id).
      Evaluate jobs against profile snapshot via thread offload outside write transactions.
    - Phase C (Fenced Commit): Under short BEGIN IMMEDIATE, verify task existence, lease_token,
      profile revision parity, and active profile status. Atomically insert matching candidates
      and advance last_processed_job_id.
    - Phase D (Completion): Transition to COMPLETED once cursor reaches max_job_id or history ends.
    - Heartbeat: Background asyncio task periodically refreshes lease_expires_at safely.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        filtering_engine: JobFilteringEngine | None = None,
        chunk_size: int = 50,
        lease_duration_seconds: int = 300,
        heartbeat_interval_seconds: int = 120,
        max_attempts: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.filtering_engine = filtering_engine or JobFilteringEngine()
        self.chunk_size = chunk_size
        self.lease_duration_seconds = lease_duration_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.max_attempts = max_attempts

    async def run_once(self) -> ReevaluationRunResult:
        """Execute one complete processing cycle for eligible reevaluation tasks."""
        result = ReevaluationRunResult()

        task = await self._claim_task()
        if not task:
            return result

        result.tasks_claimed = 1
        logger.info(
            "Claimed reevaluation task %d for profile %d (rev %d, cursor %d -> %d)",
            task.id,
            task.profile_id,
            task.profile_revision,
            task.last_processed_job_id,
            task.max_job_id,
        )

        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(task.id, task.lease_token, stop_heartbeat)
        )

        try:
            while True:
                # Check for instant completion (e.g. empty history or already at max)
                if task.last_processed_job_id >= task.max_job_id:
                    completed = await self._complete_task(task)
                    if completed:
                        result.tasks_completed += 1
                    else:
                        result.stale_lease_skips += 1
                    break

                status, next_cursor, cand_count, jobs_count = await self._process_chunk(task)
                result.jobs_evaluated += jobs_count

                if status == ChunkCommitStatus.COMMITTED:
                    task.last_processed_job_id = next_cursor
                    result.chunks_processed += 1
                    result.candidates_created += cand_count

                    if next_cursor >= task.max_job_id:
                        completed = await self._complete_task(task)
                        if completed:
                            result.tasks_completed += 1
                        else:
                            result.stale_lease_skips += 1
                        break

                elif status == ChunkCommitStatus.NO_MORE_JOBS:
                    # Gaps in ID sequence reached end of jobs <= max_job_id
                    completed = await self._complete_task(task)
                    if completed:
                        result.tasks_completed += 1
                    else:
                        result.stale_lease_skips += 1
                    break

                elif status == ChunkCommitStatus.SUPERSEDED:
                    result.tasks_superseded += 1
                    break

                elif status == ChunkCommitStatus.STALE_LEASE:
                    result.stale_lease_skips += 1
                    break

                elif status == ChunkCommitStatus.CANCELLED:
                    break

                else:
                    result.tasks_failed += 1
                    break

        finally:
            stop_heartbeat.set()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        return result

    async def _claim_task(self) -> ClaimedReevaluationTask | None:
        """Claim one eligible PENDING or expired RUNNING reevaluation task."""
        now = utc_now()

        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))

                stmt = (
                    select(ProfileReevaluationTask)
                    .where(
                        (ProfileReevaluationTask.status == ReevaluationStatus.PENDING)
                        | (
                            (ProfileReevaluationTask.status == ReevaluationStatus.RUNNING)
                            & (ProfileReevaluationTask.lease_expires_at <= now)
                        )
                    )
                    .order_by(
                        ProfileReevaluationTask.created_at.asc(),
                        ProfileReevaluationTask.id.asc(),
                    )
                    .limit(1)
                )

                res = await session.execute(stmt)
                task_db = res.scalar_one_or_none()
                if not task_db:
                    await session.rollback()
                    return None

                # Check max attempts limit
                if task_db.attempt_count >= self.max_attempts:
                    task_db.status = ReevaluationStatus.FAILED
                    task_db.lease_token = None
                    task_db.lease_expires_at = None
                    task_db.last_error = f"Exceeded maximum attempt limit of {self.max_attempts}"
                    await session.commit()
                    return None

                # Generate unique lease claim
                lease_token = str(uuid.uuid4())
                lease_expiry = now + timedelta(seconds=self.lease_duration_seconds)

                task_db.status = ReevaluationStatus.RUNNING
                task_db.lease_token = lease_token
                task_db.lease_expires_at = lease_expiry
                task_db.attempt_count += 1
                task_db.last_error = None

                claimed = ClaimedReevaluationTask(
                    id=task_db.id,
                    profile_id=task_db.profile_id,
                    profile_revision=task_db.profile_revision,
                    max_job_id=task_db.max_job_id,
                    last_processed_job_id=task_db.last_processed_job_id,
                    lease_token=lease_token,
                    attempt_count=task_db.attempt_count,
                )

                await session.commit()
                return claimed

            except Exception:
                await session.rollback()
                raise

    async def _process_chunk(
        self, task: ClaimedReevaluationTask
    ) -> tuple[ChunkCommitStatus, int, int, int]:
        """Read a keyset chunk, filter outside write lock, and commit atomically."""
        # 1. READ PHASE (outside write lock)
        async with self.session_factory() as session:
            # Load and verify profile
            prof_res = await session.execute(
                select(PreferenceProfile).where(PreferenceProfile.id == task.profile_id)
            )
            profile = prof_res.scalar_one_or_none()

            if not profile or not profile.is_active:
                await self._cancel_task(
                    task.id,
                    task.lease_token,
                    "Target profile missing or deactivated",
                )
                return ChunkCommitStatus.CANCELLED, task.last_processed_job_id, 0, 0

            if profile.revision != task.profile_revision:
                await self._supersede_task(task.id, task.lease_token)
                return ChunkCommitStatus.SUPERSEDED, task.last_processed_job_id, 0, 0

            # Read jobs chunk in ascending order
            jobs_res = await session.execute(
                select(Job)
                .where(
                    Job.id > task.last_processed_job_id,
                    Job.id <= task.max_job_id,
                )
                .order_by(Job.id.asc())
                .limit(self.chunk_size)
            )
            jobs = list(jobs_res.scalars().all())

        if not jobs:
            return ChunkCommitStatus.NO_MORE_JOBS, task.max_job_id, 0, 0

        final_examined_job_id = jobs[-1].id

        # 2. FILTERING PHASE (CPU-bound offloaded to thread)
        candidates = await asyncio.to_thread(self._filter_chunk_sync, jobs, profile)

        # 3. FENCED COMMIT PHASE (short write transaction)
        status = await self._fenced_commit_chunk(task, final_examined_job_id, candidates)
        cand_count = len(candidates) if status == ChunkCommitStatus.COMMITTED else 0
        return status, final_examined_job_id, cand_count, len(jobs)

    def _filter_chunk_sync(
        self, jobs: list[Job], profile: PreferenceProfile
    ) -> list[ReevaluationCandidate]:
        """Synchronously evaluate jobs against the profile snapshot."""
        candidates: list[ReevaluationCandidate] = []
        for job in jobs:
            res = self.filtering_engine.evaluate(job, profile)
            if res.status == EligibilityStatus.ELIGIBLE:
                candidates.append(
                    ReevaluationCandidate(
                        job_id=job.id,
                        profile_id=profile.id,
                        profile_revision=profile.revision,
                        score=int(round(res.score)),
                    )
                )
        return candidates

    async def _fenced_commit_chunk(
        self,
        task: ClaimedReevaluationTask,
        final_job_id: int,
        candidates: list[ReevaluationCandidate],
    ) -> ChunkCommitStatus:
        """Atomically persist backlog candidates and advance cursor under lease fence."""
        now = utc_now()
        new_expiry = now + timedelta(seconds=self.lease_duration_seconds)

        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))

                # 1. Verify task ownership
                res = await session.execute(
                    select(ProfileReevaluationTask).where(ProfileReevaluationTask.id == task.id)
                )
                task_db = res.scalar_one_or_none()
                if not task_db:
                    await session.rollback()
                    return ChunkCommitStatus.STALE_LEASE

                if (
                    task_db.status != ReevaluationStatus.RUNNING
                    or task_db.lease_token != task.lease_token
                ):
                    await session.rollback()
                    return ChunkCommitStatus.STALE_LEASE

                # 2. Verify profile parity
                prof_res = await session.execute(
                    select(
                        PreferenceProfile.id,
                        PreferenceProfile.revision,
                        PreferenceProfile.is_active,
                    ).where(PreferenceProfile.id == task.profile_id)
                )
                prof_row = prof_res.first()
                if not prof_row or not prof_row.is_active:
                    task_db.status = ReevaluationStatus.CANCELLED
                    task_db.lease_token = None
                    task_db.lease_expires_at = None
                    task_db.last_error = "Profile missing or inactive during commit"
                    await session.commit()
                    return ChunkCommitStatus.CANCELLED

                if prof_row.revision != task.profile_revision:
                    task_db.status = ReevaluationStatus.SUPERSEDED
                    task_db.lease_token = None
                    task_db.lease_expires_at = None
                    await session.commit()
                    return ChunkCommitStatus.SUPERSEDED

                # 3. Insert qualifying candidates idempotently
                if candidates:
                    candidate_dicts: list[dict[str, Any]] = [
                        {
                            "job_id": cand.job_id,
                            "profile_id": cand.profile_id,
                            "profile_revision": cand.profile_revision,
                            "score": cand.score,
                            "status": BacklogStatus.QUEUED,
                            "attempt_count": 0,
                            "created_at": now,
                        }
                        for cand in candidates
                    ]

                    stmt = sqlite_insert(NotificationBacklog).values(candidate_dicts)
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["job_id", "profile_id", "profile_revision"]
                    )
                    await session.execute(stmt)

                # 4. Advance cursor and renew lease atomically
                task_db.last_processed_job_id = final_job_id
                task_db.lease_expires_at = new_expiry
                await session.commit()
                return ChunkCommitStatus.COMMITTED

            except Exception as exc:
                await session.rollback()
                logger.error(
                    "Error committing chunk for reevaluation task %d: %s",
                    task.id,
                    exc,
                )
                await self._record_task_failure(task.id, str(exc))
                return ChunkCommitStatus.FAILED

    async def _complete_task(self, task: ClaimedReevaluationTask) -> bool:
        """Mark task COMPLETED fenced by task lease token."""
        now = utc_now()
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                res = await session.execute(
                    select(ProfileReevaluationTask).where(ProfileReevaluationTask.id == task.id)
                )
                task_db = res.scalar_one_or_none()

                if (
                    task_db
                    and task_db.status == ReevaluationStatus.RUNNING
                    and task_db.lease_token == task.lease_token
                ):
                    task_db.status = ReevaluationStatus.COMPLETED
                    task_db.last_processed_job_id = task.max_job_id
                    task_db.completed_at = now
                    task_db.lease_token = None
                    task_db.lease_expires_at = None
                    await session.commit()
                    return True

                await session.rollback()
                return False

            except Exception as exc:
                await session.rollback()
                logger.error("Error completing reevaluation task %d: %s", task.id, exc)
                return False

    async def renew_lease(self, task_id: int, lease_token: str) -> bool:
        """Renew lease for an actively running task if caller owns the valid lease token."""
        now = utc_now()
        new_expiry = now + timedelta(seconds=self.lease_duration_seconds)

        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                res = await session.execute(
                    select(ProfileReevaluationTask).where(ProfileReevaluationTask.id == task_id)
                )
                task_db = res.scalar_one_or_none()

                if (
                    task_db
                    and task_db.status == ReevaluationStatus.RUNNING
                    and task_db.lease_token == lease_token
                ):
                    task_db.lease_expires_at = new_expiry
                    await session.commit()
                    return True

                await session.rollback()
                return False

            except Exception as exc:
                await session.rollback()
                logger.warning("Heartbeat lease renewal failed for task %d: %s", task_id, exc)
                return False

    async def _heartbeat_loop(
        self, task_id: int, lease_token: str, stop_event: asyncio.Event
    ) -> None:
        """Periodically renew lease in background while processing persists."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_interval_seconds)
                break
            except TimeoutError:
                renewed = await self.renew_lease(task_id, lease_token)
                if not renewed:
                    logger.warning(
                        "Task %d lost lease during background heartbeat renewal",
                        task_id,
                    )
                    break

    async def _supersede_task(self, task_id: int, lease_token: str) -> None:
        """Transition task to SUPERSEDED when profile revision changes."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                await session.execute(
                    update(ProfileReevaluationTask)
                    .where(
                        ProfileReevaluationTask.id == task_id,
                        ProfileReevaluationTask.lease_token == lease_token,
                    )
                    .values(
                        status=ReevaluationStatus.SUPERSEDED,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()

    async def _cancel_task(self, task_id: int, lease_token: str, reason: str) -> None:
        """Transition task to CANCELLED when profile is deactivated or missing."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                await session.execute(
                    update(ProfileReevaluationTask)
                    .where(
                        ProfileReevaluationTask.id == task_id,
                        ProfileReevaluationTask.lease_token == lease_token,
                    )
                    .values(
                        status=ReevaluationStatus.CANCELLED,
                        last_error=reason,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()

    async def _record_task_failure(self, task_id: int, error_msg: str) -> None:
        """Record chunk commit failure, resetting to PENDING or transitioning to FAILED."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                res = await session.execute(
                    select(ProfileReevaluationTask.attempt_count).where(
                        ProfileReevaluationTask.id == task_id
                    )
                )
                attempts = res.scalar_one_or_none() or 0

                new_status = (
                    ReevaluationStatus.FAILED
                    if attempts >= self.max_attempts
                    else ReevaluationStatus.PENDING
                )

                await session.execute(
                    update(ProfileReevaluationTask)
                    .where(ProfileReevaluationTask.id == task_id)
                    .values(
                        status=new_status,
                        last_error=error_msg,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.error("Failed to record failure status for task %d: %s", task_id, exc)
