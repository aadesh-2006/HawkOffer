"""Discovery worker consuming JobDiscoveryOutbox records to generate backlog candidates."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utc_now
from app.database.session import async_session_factory
from app.filtering.engine import JobFilteringEngine
from app.filtering.models import EligibilityStatus
from app.models.job import Job
from app.models.outbox import BacklogStatus, JobDiscoveryOutbox, NotificationBacklog, OutboxStatus
from app.models.preference import PreferenceProfile

logger = logging.getLogger(__name__)


@dataclass
class ClaimedOutboxRecord:
    """Snapshot of a claimed outbox record held under an active lease."""

    id: int
    job_id: int
    lease_token: str
    attempt_count: int


@dataclass
class BacklogCandidate:
    """Evaluated matching candidate ready for fenced backlog insertion."""

    outbox_id: int
    job_id: int
    profile_id: int
    profile_revision: int
    score: int


@dataclass
class DiscoveryRunResult:
    """Execution statistics for a discovery worker processing cycle."""

    claimed_count: int = 0
    evaluated_jobs: int = 0
    candidate_matches: int = 0
    processed_outbox_count: int = 0
    failed_outbox_count: int = 0
    stale_lease_skips: int = 0


class JobDiscoveryWorker:
    """Asynchronous worker that consumes JobDiscoveryOutbox events.

    Executes in three strictly decoupled phases:
    - Phase A: Claim eligible records under short SQLite write lock (BEGIN IMMEDIATE).
    - Phase B: Run deterministic CPU-bound filtering outside of any write transaction.
    - Phase C: Atomically verify lease ownership, profile revision, insert qualifying candidates
      into NotificationBacklog, and transition outbox records to PROCESSED.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        filtering_engine: JobFilteringEngine | None = None,
        batch_size: int = 50,
        lease_duration_seconds: int = 300,
        max_attempts: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.filtering_engine = filtering_engine or JobFilteringEngine()
        self.batch_size = batch_size
        self.lease_duration_seconds = lease_duration_seconds
        self.max_attempts = max_attempts

    async def run_once(self) -> DiscoveryRunResult:
        """Execute one complete discovery processing cycle across Phases A, B, and C."""
        result = DiscoveryRunResult()

        # ---------------------------------------------------------------------
        # PHASE A: Claim eligible records under immediate SQLite write lock
        # ---------------------------------------------------------------------
        claimed_records = await self._claim_outbox_batch()
        if not claimed_records:
            return result

        result.claimed_count = len(claimed_records)
        logger.info("Claimed %d outbox records for discovery processing", len(claimed_records))

        # ---------------------------------------------------------------------
        # PHASE B: Read snapshots and filter outside write transactions
        # ---------------------------------------------------------------------
        candidates_by_outbox, valid_job_outbox_ids = await self._evaluate_claimed_records(
            claimed_records
        )
        result.evaluated_jobs = len(valid_job_outbox_ids)

        for candidates in candidates_by_outbox.values():
            result.candidate_matches += len(candidates)

        # ---------------------------------------------------------------------
        # PHASE C: Fenced commit per outbox record
        # ---------------------------------------------------------------------
        for record in claimed_records:
            if record.id not in valid_job_outbox_ids:
                # The associated Job or Outbox record was missing / deleted
                await self._handle_missing_job(record)
                continue

            candidates = candidates_by_outbox.get(record.id, [])
            commit_ok, was_stale = await self._fenced_commit_outbox(record, candidates)
            if commit_ok:
                result.processed_outbox_count += 1
            elif was_stale:
                result.stale_lease_skips += 1
            else:
                result.failed_outbox_count += 1

        return result

    async def _claim_outbox_batch(self) -> list[ClaimedOutboxRecord]:
        """Claim a bounded batch of PENDING or expired PROCESSING outbox records."""
        async with self.session_factory() as session:
            try:
                # Explicitly acquire write lock in SQLite before querying candidates
                await session.execute(text("BEGIN IMMEDIATE"))

                now = utc_now()

                # Separate candidate sub-queries to leverage directional/partial indexes
                # 1. PENDING records
                stmt_pending = (
                    select(JobDiscoveryOutbox.id, JobDiscoveryOutbox.job_id)
                    .where(JobDiscoveryOutbox.status == OutboxStatus.PENDING)
                    .order_by(JobDiscoveryOutbox.id.asc())
                    .limit(self.batch_size)
                )
                pending_res = await session.execute(stmt_pending)
                candidates = list(pending_res.all())

                # 2. If quota remains, check expired PROCESSING records
                remaining = self.batch_size - len(candidates)
                if remaining > 0:
                    stmt_expired = (
                        select(JobDiscoveryOutbox.id, JobDiscoveryOutbox.job_id)
                        .where(
                            JobDiscoveryOutbox.status == OutboxStatus.PROCESSING,
                            JobDiscoveryOutbox.lease_expires_at < now,
                        )
                        .order_by(JobDiscoveryOutbox.id.asc())
                        .limit(remaining)
                    )
                    expired_res = await session.execute(stmt_expired)
                    candidates.extend(list(expired_res.all()))

                if not candidates:
                    await session.rollback()
                    return []

                claimed_items: list[ClaimedOutboxRecord] = []
                for row_id, job_id in candidates:
                    token = str(uuid.uuid4())
                    lease_expiry = datetime.fromtimestamp(
                        now.timestamp() + self.lease_duration_seconds, tz=now.tzinfo
                    )

                    # Update lease ownership atomically
                    await session.execute(
                        update(JobDiscoveryOutbox)
                        .where(JobDiscoveryOutbox.id == row_id)
                        .values(
                            status=OutboxStatus.PROCESSING,
                            lease_token=token,
                            lease_expires_at=lease_expiry,
                            attempt_count=JobDiscoveryOutbox.attempt_count + 1,
                        )
                    )

                    claimed_items.append(
                        ClaimedOutboxRecord(
                            id=row_id,
                            job_id=job_id,
                            lease_token=token,
                            attempt_count=1,
                        )
                    )

                await session.commit()
                return claimed_items

            except Exception:
                await session.rollback()
                raise

    async def _evaluate_claimed_records(
        self,
        records: list[ClaimedOutboxRecord],
    ) -> tuple[dict[int, list[BacklogCandidate]], set[int]]:
        """Evaluate claimed jobs against active preference profiles outside write transactions."""
        job_ids = [r.job_id for r in records]

        async with self.session_factory() as session:
            # Read jobs and active preference profiles
            job_res = await session.execute(select(Job).where(Job.id.in_(job_ids)))
            jobs_by_id = {j.id: j for j in job_res.scalars().all()}

            prof_res = await session.execute(
                select(PreferenceProfile).where(PreferenceProfile.is_active.is_(True))
            )
            active_profiles = list(prof_res.scalars().all())

        valid_outbox_ids: set[int] = set()
        jobs_to_evaluate: list[tuple[ClaimedOutboxRecord, Job]] = []

        for record in records:
            job = jobs_by_id.get(record.job_id)
            if job is not None:
                valid_outbox_ids.add(record.id)
                jobs_to_evaluate.append((record, job))

        if not active_profiles or not jobs_to_evaluate:
            return {}, valid_outbox_ids

        # Offload synchronous filtering to thread pool so the async event loop remains responsive
        candidates_by_outbox = await asyncio.to_thread(
            self._filter_in_thread, jobs_to_evaluate, active_profiles
        )
        return candidates_by_outbox, valid_outbox_ids

    def _filter_in_thread(
        self,
        jobs_to_evaluate: list[tuple[ClaimedOutboxRecord, Job]],
        active_profiles: list[PreferenceProfile],
    ) -> dict[int, list[BacklogCandidate]]:
        """Synchronous filtering execution run inside thread pool."""
        results: dict[int, list[BacklogCandidate]] = {}

        for record, job in jobs_to_evaluate:
            outbox_candidates: list[BacklogCandidate] = []
            for profile in active_profiles:
                eval_res = self.filtering_engine.evaluate(job, profile)
                if eval_res.status in (EligibilityStatus.ELIGIBLE, EligibilityStatus.NEEDS_REVIEW):
                    score_int = int(round(eval_res.score))
                    outbox_candidates.append(
                        BacklogCandidate(
                            outbox_id=record.id,
                            job_id=job.id,
                            profile_id=profile.id,
                            profile_revision=profile.revision,
                            score=score_int,
                        )
                    )
            results[record.id] = outbox_candidates

        return results

    async def _fenced_commit_outbox(
        self,
        record: ClaimedOutboxRecord,
        candidates: list[BacklogCandidate],
    ) -> tuple[bool, bool]:
        """Perform atomic fenced commit of candidates and outbox PROCESSED transition.

        Returns (success: bool, was_stale: bool).
        """
        async with self.session_factory() as session:
            try:
                # Short write transaction
                await session.execute(text("BEGIN IMMEDIATE"))

                # 1. Verify lease ownership and current PROCESSING state
                verify_outbox = await session.execute(
                    select(JobDiscoveryOutbox.id).where(
                        JobDiscoveryOutbox.id == record.id,
                        JobDiscoveryOutbox.status == OutboxStatus.PROCESSING,
                        JobDiscoveryOutbox.lease_token == record.lease_token,
                    )
                )
                if verify_outbox.scalar_one_or_none() is None:
                    # Lease was lost, expired and reclaimed, or cancelled
                    logger.warning(
                        "Fenced commit rejected: outbox record %d lease lost or reclaimed",
                        record.id,
                    )
                    await session.rollback()
                    return False, True

                # 2. For each candidate, verify target profile active status and matching revision
                if candidates:
                    prof_ids = [c.profile_id for c in candidates]
                    prof_res = await session.execute(
                        select(
                            PreferenceProfile.id,
                            PreferenceProfile.is_active,
                            PreferenceProfile.revision,
                        ).where(PreferenceProfile.id.in_(prof_ids))
                    )
                    current_profiles = {p.id: p for p in prof_res.all()}

                    valid_inserts: list[dict[str, Any]] = []
                    for cand in candidates:
                        curr_prof = current_profiles.get(cand.profile_id)
                        if not curr_prof:
                            continue
                        if not curr_prof.is_active:
                            continue
                        if curr_prof.revision != cand.profile_revision:
                            # Profile criteria changed during filtering; skip candidate
                            continue

                        valid_inserts.append(
                            {
                                "job_id": cand.job_id,
                                "profile_id": cand.profile_id,
                                "profile_revision": cand.profile_revision,
                                "score": cand.score,
                                "status": BacklogStatus.QUEUED,
                                "attempt_count": 0,
                                "created_at": utc_now(),
                            }
                        )

                    if valid_inserts:
                        # Insert qualifying candidates with targeted SQLite conflict resolution
                        insert_stmt = (
                            sqlite_insert(NotificationBacklog)
                            .values(valid_inserts)
                            .on_conflict_do_nothing(
                                index_elements=["job_id", "profile_id", "profile_revision"]
                            )
                        )
                        await session.execute(insert_stmt)

                # 3. Mark outbox record PROCESSED in the same atomic transaction
                await session.execute(
                    update(JobDiscoveryOutbox)
                    .where(
                        JobDiscoveryOutbox.id == record.id,
                        JobDiscoveryOutbox.lease_token == record.lease_token,
                        JobDiscoveryOutbox.status == OutboxStatus.PROCESSING,
                    )
                    .values(
                        status=OutboxStatus.PROCESSED,
                        processed_at=utc_now(),
                        lease_token=None,
                        last_error=None,
                    )
                )

                await session.commit()
                return True, False

            except Exception as exc:
                await session.rollback()
                logger.error(
                    "Error during fenced commit for outbox record %d: %s",
                    record.id,
                    exc,
                )
                await self._record_outbox_failure(record.id, str(exc))
                return False, False

    async def _handle_missing_job(self, record: ClaimedOutboxRecord) -> None:
        """Handle outbox records where the referenced Job was deleted prior to processing."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                # Delete orphaned outbox record if still held
                await session.execute(
                    delete(JobDiscoveryOutbox).where(
                        JobDiscoveryOutbox.id == record.id,
                        JobDiscoveryOutbox.lease_token == record.lease_token,
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning("Could not prune orphaned outbox record %d: %s", record.id, exc)

    async def _record_outbox_failure(self, outbox_id: int, error_msg: str) -> None:
        """Record processing failure and transition to FAILED if max attempts exceeded."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                res = await session.execute(
                    select(JobDiscoveryOutbox.attempt_count).where(
                        JobDiscoveryOutbox.id == outbox_id
                    )
                )
                attempts = res.scalar_one_or_none() or 0

                new_status = (
                    OutboxStatus.FAILED if attempts >= self.max_attempts else OutboxStatus.PENDING
                )

                await session.execute(
                    update(JobDiscoveryOutbox)
                    .where(JobDiscoveryOutbox.id == outbox_id)
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
                logger.error("Failed to record outbox failure state for %d: %s", outbox_id, exc)
