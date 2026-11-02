"""Backlog drain worker fairly allocating candidates and promoting them to notifications."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utc_now
from app.database.session import async_session_factory
from app.models.job import Job
from app.models.notification import Notification
from app.models.outbox import BacklogStatus, NotificationBacklog, SystemDrainState
from app.models.preference import PreferenceProfile
from app.schemas.notification import DeliveryStatus, NotificationStatus, NotificationType

logger = logging.getLogger(__name__)


class PromotionStatus(StrEnum):
    """Outcome of promoting a single NotificationBacklog candidate."""

    PROMOTED = "PROMOTED"
    ALREADY_PROMOTED = "ALREADY_PROMOTED"
    STALE_REVISION = "STALE_REVISION"
    DROPPED = "DROPPED"
    STALE_LEASE = "STALE_LEASE"
    FAILED = "FAILED"


@dataclass
class ClaimedBacklogItem:
    """Snapshot of a claimed backlog candidate held under worker lease."""

    id: int
    job_id: int
    profile_id: int
    profile_revision: int
    score: int
    lease_token: str
    attempt_count: int


@dataclass
class DrainRunResult:
    """Execution statistics for a backlog drain cycle."""

    claimed_count: int = 0
    promoted_count: int = 0
    already_promoted_count: int = 0
    stale_revision_count: int = 0
    dropped_count: int = 0
    stale_lease_skips: int = 0
    failed_count: int = 0
    final_drain_counter: int = 0


class BacklogDrainWorker:
    """Fairness-aware worker that drains NotificationBacklog into user-facing Notification records.

    Architecture:
    - Phase 1 (Atomic Sequential Allocation & Claim):
      Under short BEGIN IMMEDIATE write lock, inspects persistent SystemDrainState.drain_counter.
      Executes sequential slot allocation:
        - If drain_counter % 5 == 4: target FIFO bucket.
        - Otherwise: target Relevance bucket.
        - If target bucket is empty, falls back to the alternate bucket.
        - The counter advances by 1 only when a candidate is successfully claimed.
        - Stops when batch_size is reached or both buckets are empty.
    - Phase 2 (Fenced Notification Promotion):
      Per claimed candidate, opens an atomic transaction to:
        - Verify lease ownership, status RUNNING/PROCESSING, job existence, profile parity.
        - Check for existing duplicate notification (including desktop_toast alias).
        - If duplicate exists, marks candidate PROCESSED.
        - If valid, inserts Notification (status=UNREAD, delivery=PENDING) and marks PROCESSED.
        - If revision changed, transitions candidate to STALE.
        - If promotion fails, records failure fenced by original lease token.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
        batch_size: int = 50,
        lease_duration_seconds: int = 300,
        max_attempts: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.batch_size = batch_size
        self.lease_duration_seconds = lease_duration_seconds
        self.max_attempts = max_attempts

    async def run_once(self) -> DrainRunResult:
        """Execute one complete drain cycle: claiming a batch then promoting each candidate."""
        result = DrainRunResult()

        claimed_items, final_counter = await self._claim_batch()
        result.claimed_count = len(claimed_items)
        result.final_drain_counter = final_counter

        if not claimed_items:
            return result

        logger.info(
            "Claimed %d backlog candidates for promotion (drain_counter now %d)",
            len(claimed_items),
            final_counter,
        )

        for item in claimed_items:
            status = await self._promote_candidate(item)
            if status == PromotionStatus.PROMOTED:
                result.promoted_count += 1
            elif status == PromotionStatus.ALREADY_PROMOTED:
                result.already_promoted_count += 1
            elif status == PromotionStatus.STALE_REVISION:
                result.stale_revision_count += 1
            elif status == PromotionStatus.DROPPED:
                result.dropped_count += 1
            elif status == PromotionStatus.STALE_LEASE:
                result.stale_lease_skips += 1
            elif status == PromotionStatus.FAILED:
                result.failed_count += 1

        return result

    async def _claim_batch(self) -> tuple[list[ClaimedBacklogItem], int]:
        """Atomically claim candidates using fairness sequential slot allocation."""
        now = utc_now()
        claimed_items: list[ClaimedBacklogItem] = []
        claimed_ids: set[int] = set()

        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))

                drain_state = await SystemDrainState.get_or_create(session)
                curr_counter = drain_state.drain_counter

                for _ in range(self.batch_size):
                    # Determine target bucket based on persistent counter
                    if curr_counter % 5 == 4:
                        primary_bucket = "FIFO"
                        secondary_bucket = "RELEVANCE"
                    else:
                        primary_bucket = "RELEVANCE"
                        secondary_bucket = "FIFO"

                    cand = await self._select_candidate_from_bucket(
                        session, primary_bucket, claimed_ids, now
                    )
                    if not cand:
                        cand = await self._select_candidate_from_bucket(
                            session, secondary_bucket, claimed_ids, now
                        )

                    if not cand:
                        # Both buckets empty; stop sequential allocation
                        break

                    # Claim candidate
                    lease_token = str(uuid.uuid4())
                    lease_expiry = now + timedelta(seconds=self.lease_duration_seconds)

                    cand.status = BacklogStatus.PROCESSING
                    cand.lease_token = lease_token
                    cand.lease_expires_at = lease_expiry
                    cand.attempt_count += 1

                    claimed_ids.add(cand.id)
                    claimed_items.append(
                        ClaimedBacklogItem(
                            id=cand.id,
                            job_id=cand.job_id,
                            profile_id=cand.profile_id,
                            profile_revision=cand.profile_revision,
                            score=cand.score,
                            lease_token=lease_token,
                            attempt_count=cand.attempt_count,
                        )
                    )

                    # Advance counter exactly once per successful claim
                    curr_counter += 1

                # Commit claims and updated counter atomically
                drain_state.drain_counter = curr_counter
                drain_state.updated_at = now
                await session.commit()
                return claimed_items, curr_counter

            except Exception:
                await session.rollback()
                raise

    async def _select_candidate_from_bucket(
        self,
        session: AsyncSession,
        bucket_type: str,
        excluded_ids: set[int],
        now: datetime,
    ) -> NotificationBacklog | None:
        """Select a single eligible candidate from the specified bucket using partial indexes."""
        while True:
            exclusion_clause = ""
            if excluded_ids:
                exclusion_clause = f"AND id NOT IN ({','.join(str(i) for i in excluded_ids)})"

            if bucket_type == "RELEVANCE":
                stmt_queued = select(NotificationBacklog).from_statement(
                    text(
                        "SELECT * FROM notification_backlog "
                        "INDEXED BY ix_backlog_queued_relevance "
                        f"WHERE status = 'QUEUED' {exclusion_clause} "
                        "ORDER BY score DESC, created_at ASC, id ASC LIMIT 1"
                    )
                )
                res_queued = await session.execute(stmt_queued)
                cand = cast(NotificationBacklog | None, res_queued.scalar_one_or_none())
                if cand:
                    return cand

                stmt_proc = (
                    select(NotificationBacklog)
                    .from_statement(
                        text(
                            "SELECT * FROM notification_backlog "
                            "INDEXED BY ix_backlog_processing_relevance "
                            "WHERE status = 'PROCESSING' "
                            f"AND lease_expires_at <= :now {exclusion_clause} "
                            "ORDER BY score DESC, created_at ASC, id ASC LIMIT 1"
                        )
                    )
                    .params(now=now)
                )
                res_proc = await session.execute(stmt_proc)
                cand_proc = cast(NotificationBacklog | None, res_proc.scalar_one_or_none())
                if cand_proc:
                    if cand_proc.attempt_count >= self.max_attempts:
                        cand_proc.status = BacklogStatus.DROPPED
                        cand_proc.lease_token = None
                        cand_proc.lease_expires_at = None
                        logger.warning(
                            "Dropping expired backlog item %d: exceeded max attempts limit of %d",
                            cand_proc.id,
                            self.max_attempts,
                        )
                        excluded_ids.add(cand_proc.id)
                        continue
                    return cand_proc
                return None
            else:
                stmt_queued = select(NotificationBacklog).from_statement(
                    text(
                        "SELECT * FROM notification_backlog "
                        "INDEXED BY ix_backlog_queued_fifo "
                        f"WHERE status = 'QUEUED' {exclusion_clause} "
                        "ORDER BY created_at ASC, id ASC LIMIT 1"
                    )
                )
                res_queued = await session.execute(stmt_queued)
                cand = cast(NotificationBacklog | None, res_queued.scalar_one_or_none())
                if cand:
                    return cand

                stmt_proc = (
                    select(NotificationBacklog)
                    .from_statement(
                        text(
                            "SELECT * FROM notification_backlog "
                            "INDEXED BY ix_backlog_processing_fifo "
                            "WHERE status = 'PROCESSING' "
                            f"AND lease_expires_at <= :now {exclusion_clause} "
                            "ORDER BY created_at ASC, id ASC LIMIT 1"
                        )
                    )
                    .params(now=now)
                )
                res_proc = await session.execute(stmt_proc)
                cand_proc = cast(NotificationBacklog | None, res_proc.scalar_one_or_none())
                if cand_proc:
                    if cand_proc.attempt_count >= self.max_attempts:
                        cand_proc.status = BacklogStatus.DROPPED
                        cand_proc.lease_token = None
                        cand_proc.lease_expires_at = None
                        logger.warning(
                            "Dropping expired backlog item %d: exceeded max attempts limit of %d",
                            cand_proc.id,
                            self.max_attempts,
                        )
                        excluded_ids.add(cand_proc.id)
                        continue
                    return cand_proc
                return None

    async def _promote_candidate(self, item: ClaimedBacklogItem) -> PromotionStatus:
        """Promote a single claimed candidate into a Notification under lease fence."""
        now = utc_now()
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))

                # 1. Verify candidate lease ownership
                res = await session.execute(
                    select(NotificationBacklog).where(NotificationBacklog.id == item.id)
                )
                backlog_db = res.scalar_one_or_none()
                if not backlog_db:
                    await session.rollback()
                    logger.warning("Backlog candidate %d no longer exists; dropped", item.id)
                    return PromotionStatus.DROPPED

                if (
                    backlog_db.status != BacklogStatus.PROCESSING
                    or backlog_db.lease_token != item.lease_token
                ):
                    await session.rollback()
                    return PromotionStatus.STALE_LEASE

                # 2. Verify Job still exists
                job_res = await session.execute(select(Job.id).where(Job.id == backlog_db.job_id))
                if not job_res.scalar_one_or_none():
                    backlog_db.status = BacklogStatus.DROPPED
                    backlog_db.lease_token = None
                    backlog_db.lease_expires_at = None
                    logger.warning(
                        "Dropping backlog candidate %d: job %d was deleted",
                        backlog_db.id,
                        backlog_db.job_id,
                    )
                    await session.commit()
                    return PromotionStatus.DROPPED

                # 3. Verify Profile still exists, is active, and revision matches
                prof_res = await session.execute(
                    select(
                        PreferenceProfile.id,
                        PreferenceProfile.revision,
                        PreferenceProfile.is_active,
                    ).where(PreferenceProfile.id == backlog_db.profile_id)
                )
                prof_row = prof_res.first()
                if not prof_row:
                    backlog_db.status = BacklogStatus.DROPPED
                    backlog_db.lease_token = None
                    backlog_db.lease_expires_at = None
                    logger.warning(
                        "Dropping backlog candidate %d: profile %d was deleted",
                        backlog_db.id,
                        backlog_db.profile_id,
                    )
                    await session.commit()
                    return PromotionStatus.DROPPED

                if not prof_row.is_active:
                    backlog_db.status = BacklogStatus.DROPPED
                    backlog_db.lease_token = None
                    backlog_db.lease_expires_at = None
                    logger.warning(
                        "Dropping backlog candidate %d: profile %d is deactivated",
                        backlog_db.id,
                        backlog_db.profile_id,
                    )
                    await session.commit()
                    return PromotionStatus.DROPPED

                if prof_row.revision != backlog_db.profile_revision:
                    backlog_db.status = BacklogStatus.STALE
                    backlog_db.lease_token = None
                    backlog_db.lease_expires_at = None
                    logger.info(
                        "Backlog candidate %d marked STALE: revision %d != candidate revision %d",
                        backlog_db.id,
                        prof_row.revision,
                        backlog_db.profile_revision,
                    )
                    await session.commit()
                    return PromotionStatus.STALE_REVISION

                # 4. Check for existing equivalent notification (deduplication)
                existing_notif_res = await session.execute(
                    select(Notification.id).where(
                        Notification.job_id == backlog_db.job_id,
                        Notification.profile_id == backlog_db.profile_id,
                        Notification.notification_type.in_(
                            [NotificationType.JOB_ALERT, NotificationType.DESKTOP_TOAST]
                        ),
                    )
                )
                if existing_notif_res.scalar_one_or_none():
                    # Deduplication: mark candidate PROCESSED without creating duplicate
                    backlog_db.status = BacklogStatus.PROCESSED
                    backlog_db.processed_at = now
                    backlog_db.lease_token = None
                    backlog_db.lease_expires_at = None
                    await session.commit()
                    return PromotionStatus.ALREADY_PROMOTED

                # 5. Create canonical Notification
                new_notification = Notification(
                    job_id=backlog_db.job_id,
                    profile_id=backlog_db.profile_id,
                    notification_type=NotificationType.JOB_ALERT,
                    status=NotificationStatus.UNREAD,
                    delivery_status=DeliveryStatus.PENDING,
                    created_at=now,
                )
                session.add(new_notification)

                # Atomically mark backlog candidate PROCESSED
                backlog_db.status = BacklogStatus.PROCESSED
                backlog_db.processed_at = now
                backlog_db.lease_token = None
                backlog_db.lease_expires_at = None

                await session.commit()
                return PromotionStatus.PROMOTED

            except Exception as exc:
                await session.rollback()
                logger.error(
                    "Error promoting backlog candidate %d: %s",
                    item.id,
                    exc,
                )
                await self._record_backlog_failure(item.id, item.lease_token, str(exc))
                return PromotionStatus.FAILED

    async def _record_backlog_failure(
        self, backlog_id: int, lease_token: str, error_msg: str
    ) -> bool:
        """Record promotion failure fenced by task lease token."""
        async with self.session_factory() as session:
            try:
                await session.execute(text("BEGIN IMMEDIATE"))
                res = await session.execute(
                    select(NotificationBacklog).where(
                        NotificationBacklog.id == backlog_id,
                        NotificationBacklog.status == BacklogStatus.PROCESSING,
                        NotificationBacklog.lease_token == lease_token,
                    )
                )
                backlog_db = res.scalar_one_or_none()
                if not backlog_db:
                    await session.rollback()
                    logger.warning(
                        "Stale worker failed to update backlog %d (lease mismatch)",
                        backlog_id,
                    )
                    return False

                attempts = backlog_db.attempt_count
                new_status = (
                    BacklogStatus.DROPPED if attempts >= self.max_attempts else BacklogStatus.QUEUED
                )

                backlog_db.status = new_status
                backlog_db.lease_token = None
                backlog_db.lease_expires_at = None

                logger.warning(
                    "Backlog item %d failed (attempts=%d, status=%s): %s",
                    backlog_id,
                    attempts,
                    new_status,
                    error_msg,
                )

                await session.commit()
                return True
            except Exception as exc:
                await session.rollback()
                logger.error("Failed to record failure status for backlog %d: %s", backlog_id, exc)
                return False
