"""Comprehensive tests for BacklogDrainWorker (M5 Phase 3 Unit 6)."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.outbox import BacklogStatus, NotificationBacklog, SystemDrainState
from app.models.preference import PreferenceProfile
from app.schemas.notification import DeliveryStatus, NotificationStatus, NotificationType
from app.services.backlog_drain_worker import (
    BacklogDrainWorker,
    ClaimedBacklogItem,
    PromotionStatus,
)


@pytest.fixture
async def seeded_company(db_session: AsyncSession) -> Company:
    """Fixture to provide a company for jobs."""
    company = Company(name="Drain Co", career_url="https://drain.com")
    db_session.add(company)
    await db_session.commit()
    return company


@pytest.fixture
async def seeded_profile(db_session: AsyncSession) -> PreferenceProfile:
    """Fixture to provide an active preference profile."""
    profile = PreferenceProfile(
        name="Drain Profile",
        preferred_roles=["Engineer"],
        is_active=True,
        revision=1,
    )
    db_session.add(profile)
    await db_session.commit()
    return profile


async def _create_test_job(
    session: AsyncSession,
    company_id: int,
    title: str = "Engineer",
) -> Job:
    """Helper to create a Job with unique hashes."""
    raw_key = f"{title}_{company_id}_{datetime.now().timestamp()}_{hash(title)}"
    fp = hashlib.sha256(raw_key.encode()).hexdigest()

    job = Job(
        company_id=company_id,
        title=title,
        application_url=f"https://drain.com/{fp[:8]}",
        fingerprint_hash=fp,
        content_hash=fp,
    )
    session.add(job)
    await session.flush()
    return job


async def _create_test_backlog(
    session: AsyncSession,
    job_id: int,
    profile_id: int,
    profile_revision: int = 1,
    score: int = 80,
    status: str = BacklogStatus.QUEUED,
    lease_token: str | None = None,
    lease_expires_at: datetime | None = None,
    attempt_count: int = 0,
    created_at: datetime | None = None,
) -> NotificationBacklog:
    """Helper to create a NotificationBacklog entry."""
    backlog = NotificationBacklog(
        job_id=job_id,
        profile_id=profile_id,
        profile_revision=profile_revision,
        score=score,
        status=status,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        attempt_count=attempt_count,
    )
    if created_at is not None:
        backlog.created_at = created_at
    session.add(backlog)
    await session.commit()
    return backlog


# =============================================================================
# 1. FAIRNESS SEQUENTIAL ALLOCATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_fairness_sequential_allocation_4_to_1(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify that 5 slots follow 4 relevance (counter 0..3) then 1 FIFO (counter 4)."""
    now = datetime.now(UTC)

    # Create 6 jobs
    jobs = [await _create_test_job(db_session, seeded_company.id, f"Eng {i}") for i in range(6)]

    # Create items with distinct scores and timestamps
    # Job 0: created oldest, low score (FIFO candidate)
    b0 = await _create_test_backlog(
        db_session, jobs[0].id, seeded_profile.id, score=10, created_at=now - timedelta(hours=10)
    )
    # Job 1: high score (Relevance candidate)
    b1 = await _create_test_backlog(
        db_session, jobs[1].id, seeded_profile.id, score=99, created_at=now - timedelta(hours=5)
    )
    # Job 2: high score (Relevance candidate)
    b2 = await _create_test_backlog(
        db_session, jobs[2].id, seeded_profile.id, score=90, created_at=now - timedelta(hours=4)
    )
    # Job 3: medium score (Relevance candidate)
    b3 = await _create_test_backlog(
        db_session, jobs[3].id, seeded_profile.id, score=85, created_at=now - timedelta(hours=3)
    )
    # Job 4: medium score (Relevance candidate)
    b4 = await _create_test_backlog(
        db_session, jobs[4].id, seeded_profile.id, score=80, created_at=now - timedelta(hours=2)
    )
    # Job 5: lower score
    await _create_test_backlog(
        db_session, jobs[5].id, seeded_profile.id, score=50, created_at=now - timedelta(hours=1)
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=5)
    claimed, final_counter = await worker._claim_batch()

    assert len(claimed) == 5
    assert final_counter == 5

    # Slot 0 (counter 0, Relevance): b1 (score 99)
    # Slot 1 (counter 1, Relevance): b2 (score 90)
    # Slot 2 (counter 2, Relevance): b3 (score 85)
    # Slot 3 (counter 3, Relevance): b4 (score 80)
    # Slot 4 (counter 4, FIFO): b0 (oldest created_at, even though score is 10!)
    assert claimed[0].id == b1.id
    assert claimed[1].id == b2.id
    assert claimed[2].id == b3.id
    assert claimed[3].id == b4.id
    assert claimed[4].id == b0.id


@pytest.mark.asyncio
async def test_fairness_counter_advancement_only_on_claimed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify counter advances only by actual claimed count when fewer than K items available."""
    job1 = await _create_test_job(db_session, seeded_company.id, "Job 1")
    job2 = await _create_test_job(db_session, seeded_company.id, "Job 2")

    await _create_test_backlog(db_session, job1.id, seeded_profile.id, score=70)
    await _create_test_backlog(db_session, job2.id, seeded_profile.id, score=80)

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    claimed, final_counter = await worker._claim_batch()

    assert len(claimed) == 2
    assert final_counter == 2  # Started at 0, advanced by 2, not 10!

    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        assert state.drain_counter == 2


@pytest.mark.asyncio
async def test_fairness_fallback_when_target_bucket_empty(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify that when target bucket is empty, fallback bucket is consumed and slot advances."""
    # Seed state counter at 4 (which targets FIFO)
    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        state.drain_counter = 4
        await session.commit()

    # Create only 1 item
    job = await _create_test_job(db_session, seeded_company.id, "Only Job")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id, score=95)

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=1)
    claimed, final_counter = await worker._claim_batch()

    assert len(claimed) == 1
    assert claimed[0].id == b.id
    assert final_counter == 5  # Advanced from 4 to 5


@pytest.mark.asyncio
async def test_fairness_both_buckets_empty_stops_without_counter_advance(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that empty backlog stops allocation immediately without advancing counter."""
    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        state.drain_counter = 3
        await session.commit()

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=5)
    claimed, final_counter = await worker._claim_batch()

    assert len(claimed) == 0
    assert final_counter == 3

    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        assert state.drain_counter == 3


@pytest.mark.asyncio
async def test_fairness_deterministic_ordering(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify deterministic tie-breaking for Relevance (score DESC, created_at ASC, id ASC)."""
    now = datetime.now(UTC)
    jobs = [
        await _create_test_job(db_session, seeded_company.id, f"Deterministic {i}")
        for i in range(3)
    ]

    # Same score, differing timestamps
    b0 = await _create_test_backlog(
        db_session, jobs[0].id, seeded_profile.id, score=90, created_at=now - timedelta(minutes=10)
    )
    b1 = await _create_test_backlog(
        db_session,
        jobs[1].id,
        seeded_profile.id,
        score=90,
        created_at=now - timedelta(minutes=20),  # older created_at should come first
    )
    b2 = await _create_test_backlog(
        db_session, jobs[2].id, seeded_profile.id, score=90, created_at=now - timedelta(minutes=5)
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=3)
    claimed, _ = await worker._claim_batch()

    assert [c.id for c in claimed] == [b1.id, b0.id, b2.id]


# =============================================================================
# 2. CLAIMING & LEASE DYNAMICS TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_claim_queued_and_expired_processing(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify worker claims both QUEUED and expired PROCESSING candidates."""
    now = datetime.now(UTC)
    jobs = [await _create_test_job(db_session, seeded_company.id, f"Claim {i}") for i in range(3)]

    # b0: QUEUED
    b0 = await _create_test_backlog(
        db_session,
        jobs[0].id,
        seeded_profile.id,
        score=80,
        status=BacklogStatus.QUEUED,
    )
    # b1: PROCESSING with expired lease
    b1 = await _create_test_backlog(
        db_session,
        jobs[1].id,
        seeded_profile.id,
        score=75,
        status=BacklogStatus.PROCESSING,
        lease_token="expired-token",
        lease_expires_at=now - timedelta(seconds=10),
        attempt_count=1,
    )
    # b2: PROCESSING with active lease (should NOT be claimed)
    b2 = await _create_test_backlog(
        db_session,
        jobs[2].id,
        seeded_profile.id,
        score=99,
        status=BacklogStatus.PROCESSING,
        lease_token="active-token",
        lease_expires_at=now + timedelta(seconds=200),
        attempt_count=1,
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    claimed, _ = await worker._claim_batch()

    claimed_ids = {c.id for c in claimed}
    assert claimed_ids == {b0.id, b1.id}
    assert b2.id not in claimed_ids

    # Verify attempt_count incremented and lease updated
    async with session_factory() as session:
        res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id.in_([b0.id, b1.id]))
        )
        items = {item.id: item for item in res.scalars().all()}
        assert items[b0.id].attempt_count == 1
        assert items[b1.id].attempt_count == 2
        assert items[b1.id].lease_token != "expired-token"


@pytest.mark.asyncio
async def test_claim_drops_candidate_exceeding_max_attempts(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify expired PROCESSING candidates exceeding max_attempts are dropped during claim."""
    now = datetime.now(UTC)
    job = await _create_test_job(db_session, seeded_company.id, "Max Attempts Job")

    # attempt_count already 5 (max_attempts default is 5)
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        score=90,
        status=BacklogStatus.PROCESSING,
        lease_token="expired-token",
        lease_expires_at=now - timedelta(seconds=10),
        attempt_count=5,
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=1, max_attempts=5)
    claimed, _ = await worker._claim_batch()

    assert len(claimed) == 0

    async with session_factory() as session:
        res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = res.scalar_one()
        assert updated_b.status == BacklogStatus.DROPPED
        assert updated_b.lease_token is None


# =============================================================================
# 3. NOTIFICATION PROMOTION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_promote_valid_candidate_creates_notification(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify successful promotion creates Notification and transitions backlog to PROCESSED."""
    job = await _create_test_job(db_session, seeded_company.id, "Promote Job")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id, score=88)

    worker = BacklogDrainWorker(session_factory=session_factory)
    result = await worker.run_once()

    assert result.claimed_count == 1
    assert result.promoted_count == 1
    assert result.failed_count == 0

    async with session_factory() as session:
        # Check Notification created
        notif_res = await session.execute(
            select(Notification).where(
                Notification.job_id == job.id,
                Notification.profile_id == seeded_profile.id,
            )
        )
        notif = notif_res.scalar_one()
        assert notif.notification_type == NotificationType.JOB_ALERT
        assert notif.status == NotificationStatus.UNREAD
        assert notif.delivery_status == DeliveryStatus.PENDING

        # Check Backlog transitioned
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = b_res.scalar_one()
        assert updated_b.status == BacklogStatus.PROCESSED
        assert updated_b.processed_at is not None
        assert updated_b.lease_token is None


@pytest.mark.asyncio
async def test_promote_profile_revision_mismatch_becomes_stale(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify backlog candidate transitions to STALE if profile revision changed."""
    job = await _create_test_job(db_session, seeded_company.id, "Stale Job")
    # Candidate evaluated against revision 1
    b = await _create_test_backlog(
        db_session, job.id, seeded_profile.id, profile_revision=1, score=85
    )

    # Bump profile revision to 2
    seeded_profile.revision = 2
    await db_session.commit()

    worker = BacklogDrainWorker(session_factory=session_factory)
    result = await worker.run_once()

    assert result.claimed_count == 1
    assert result.stale_revision_count == 1
    assert result.promoted_count == 0

    async with session_factory() as session:
        # Ensure no notification created
        notif_res = await session.execute(select(Notification).where(Notification.job_id == job.id))
        assert notif_res.scalar_one_or_none() is None

        # Ensure backlog marked STALE
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = b_res.scalar_one()
        assert updated_b.status == BacklogStatus.STALE
        assert updated_b.lease_token is None


@pytest.mark.asyncio
async def test_promote_deleted_job_becomes_dropped(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify backlog candidate transitions to DROPPED if job was deleted before promotion."""
    job = await _create_test_job(db_session, seeded_company.id, "Deleted Job")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id)

    # Claim candidate first
    worker = BacklogDrainWorker(session_factory=session_factory)
    claimed, _ = await worker._claim_batch()
    assert len(claimed) == 1

    # Simulate deletion of job
    await db_session.delete(job)
    await db_session.commit()

    # Now promote
    status = await worker._promote_candidate(claimed[0])
    assert status == PromotionStatus.DROPPED

    async with session_factory() as session:
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = b_res.scalar_one_or_none()
        if updated_b:
            assert updated_b.status == BacklogStatus.DROPPED


@pytest.mark.asyncio
async def test_promote_deactivated_profile_becomes_dropped(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify backlog candidate transitions to DROPPED if profile is deactivated."""
    job = await _create_test_job(db_session, seeded_company.id, "Deactivated Job")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id)

    # Deactivate profile
    seeded_profile.is_active = False
    await db_session.commit()

    worker = BacklogDrainWorker(session_factory=session_factory)
    result = await worker.run_once()

    assert result.dropped_count == 1
    assert result.promoted_count == 0

    async with session_factory() as session:
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = b_res.scalar_one()
        assert updated_b.status == BacklogStatus.DROPPED


@pytest.mark.asyncio
async def test_promote_deduplication_job_alert(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify candidate is marked PROCESSED without duplicate if job_alert notification exists."""
    job = await _create_test_job(db_session, seeded_company.id, "Dedup Job Alert")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id)

    # Existing notification
    existing_notif = Notification(
        job_id=job.id,
        profile_id=seeded_profile.id,
        notification_type=NotificationType.JOB_ALERT,
        status=NotificationStatus.UNREAD,
        delivery_status=DeliveryStatus.PENDING,
    )
    db_session.add(existing_notif)
    await db_session.commit()

    worker = BacklogDrainWorker(session_factory=session_factory)
    result = await worker.run_once()

    assert result.already_promoted_count == 1
    assert result.promoted_count == 0

    async with session_factory() as session:
        # Check only 1 notification exists
        notifs = await session.execute(
            select(Notification).where(
                Notification.job_id == job.id,
                Notification.profile_id == seeded_profile.id,
            )
        )
        assert len(notifs.scalars().all()) == 1

        # Backlog marked PROCESSED
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        assert b_res.scalar_one().status == BacklogStatus.PROCESSED


@pytest.mark.asyncio
async def test_promote_deduplication_desktop_toast(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify candidate is marked PROCESSED without duplicate if desktop_toast exists."""
    job = await _create_test_job(db_session, seeded_company.id, "Dedup Desktop Toast")
    b = await _create_test_backlog(db_session, job.id, seeded_profile.id)

    # Existing notification with desktop_toast
    existing_notif = Notification(
        job_id=job.id,
        profile_id=seeded_profile.id,
        notification_type=NotificationType.DESKTOP_TOAST,
        status=NotificationStatus.READ,
        delivery_status=DeliveryStatus.DELIVERED,
    )
    db_session.add(existing_notif)
    await db_session.commit()

    worker = BacklogDrainWorker(session_factory=session_factory)
    result = await worker.run_once()

    assert result.already_promoted_count == 1
    assert result.promoted_count == 0

    async with session_factory() as session:
        notifs = await session.execute(
            select(Notification).where(
                Notification.job_id == job.id,
                Notification.profile_id == seeded_profile.id,
            )
        )
        assert len(notifs.scalars().all()) == 1
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        assert b_res.scalar_one().status == BacklogStatus.PROCESSED


# =============================================================================
# 4. FENCING & FAILURE HANDLING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_promote_stale_lease_abort(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify stale worker with outdated lease cannot promote reclaimed candidate."""
    job = await _create_test_job(db_session, seeded_company.id, "Stale Lease Job")
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        status=BacklogStatus.PROCESSING,
        lease_token="worker-b-token",  # New owner
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    stale_item = ClaimedBacklogItem(
        id=b.id,
        job_id=job.id,
        profile_id=seeded_profile.id,
        profile_revision=1,
        score=80,
        lease_token="worker-a-stale-token",
        attempt_count=1,
    )

    worker = BacklogDrainWorker(session_factory=session_factory)
    status = await worker._promote_candidate(stale_item)

    assert status == PromotionStatus.STALE_LEASE

    async with session_factory() as session:
        # Ensure Worker B's lease is untouched
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        item = b_res.scalar_one()
        assert item.status == BacklogStatus.PROCESSING
        assert item.lease_token == "worker-b-token"


@pytest.mark.asyncio
async def test_record_failure_stale_lease_ignored(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify stale worker cannot record failure or change status if lease was reclaimed."""
    job = await _create_test_job(db_session, seeded_company.id, "Failure Fence Job")
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        status=BacklogStatus.PROCESSING,
        lease_token="worker-b-token",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        attempt_count=2,
    )

    worker = BacklogDrainWorker(session_factory=session_factory)
    recorded = await worker._record_backlog_failure(
        b.id, "worker-a-stale-token", "Something went wrong"
    )

    assert recorded is False

    async with session_factory() as session:
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        item = b_res.scalar_one()
        assert item.status == BacklogStatus.PROCESSING
        assert item.lease_token == "worker-b-token"


@pytest.mark.asyncio
async def test_promote_failure_transient_queued(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify promotion failure resets candidate to QUEUED when attempt_count < max_attempts."""
    job = await _create_test_job(db_session, seeded_company.id, "Transient Failure Job")
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        status=BacklogStatus.PROCESSING,
        lease_token="current-token",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        attempt_count=1,
    )

    claimed_item = ClaimedBacklogItem(
        id=b.id,
        job_id=job.id,
        profile_id=seeded_profile.id,
        profile_revision=1,
        score=80,
        lease_token="current-token",
        attempt_count=1,
    )

    worker = BacklogDrainWorker(session_factory=session_factory, max_attempts=3)

    # Simulate error creating notification
    with patch(
        "app.services.backlog_drain_worker.Notification",
        side_effect=RuntimeError("Simulated notification error"),
    ):
        status = await worker._promote_candidate(claimed_item)

    assert status == PromotionStatus.FAILED

    async with session_factory() as session:
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        item = b_res.scalar_one()
        assert item.status == BacklogStatus.QUEUED
        assert item.lease_token is None


@pytest.mark.asyncio
async def test_promote_failure_terminal_dropped(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify promotion failure marks candidate DROPPED when attempt_count >= max_attempts."""
    job = await _create_test_job(db_session, seeded_company.id, "Terminal Failure Job")
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        status=BacklogStatus.PROCESSING,
        lease_token="current-token",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        attempt_count=3,  # Reached max_attempts
    )

    claimed_item = ClaimedBacklogItem(
        id=b.id,
        job_id=job.id,
        profile_id=seeded_profile.id,
        profile_revision=1,
        score=80,
        lease_token="current-token",
        attempt_count=3,
    )

    worker = BacklogDrainWorker(session_factory=session_factory, max_attempts=3)

    with patch(
        "app.services.backlog_drain_worker.Notification",
        side_effect=RuntimeError("Simulated notification error"),
    ):
        status = await worker._promote_candidate(claimed_item)

    assert status == PromotionStatus.FAILED

    async with session_factory() as session:
        b_res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        item = b_res.scalar_one()
        assert item.status == BacklogStatus.DROPPED
        assert item.lease_token is None


@pytest.mark.asyncio
async def test_persistent_counter_across_runs(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify persistent counter persists across worker instances and sequential cycles."""
    jobs = [
        await _create_test_job(db_session, seeded_company.id, f"Multi-cycle {i}") for i in range(6)
    ]
    for j in jobs:
        await _create_test_backlog(db_session, j.id, seeded_profile.id, score=80)

    worker1 = BacklogDrainWorker(session_factory=session_factory, batch_size=3)
    res1 = await worker1.run_once()
    assert res1.claimed_count == 3
    assert res1.final_drain_counter == 3

    worker2 = BacklogDrainWorker(session_factory=session_factory, batch_size=3)
    res2 = await worker2.run_once()
    assert res2.claimed_count == 3
    assert res2.final_drain_counter == 6


@pytest.mark.asyncio
async def test_concurrent_drain_claims(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify concurrent claim batches serialize cleanly with no double claims."""
    jobs = [
        await _create_test_job(db_session, seeded_company.id, f"Concurrent {i}") for i in range(10)
    ]
    for j in jobs:
        await _create_test_backlog(db_session, j.id, seeded_profile.id, score=80)

    worker_a = BacklogDrainWorker(session_factory=session_factory, batch_size=5)
    worker_b = BacklogDrainWorker(session_factory=session_factory, batch_size=5)

    (claimed_a, counter_a), (claimed_b, counter_b) = await asyncio.gather(
        worker_a._claim_batch(),
        worker_b._claim_batch(),
    )

    ids_a = {item.id for item in claimed_a}
    ids_b = {item.id for item in claimed_b}

    # No overlap in claimed candidates
    assert len(ids_a.intersection(ids_b)) == 0
    assert len(ids_a) + len(ids_b) == 10

    # Final counter in DB is 10
    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        assert state.drain_counter == 10


@pytest.mark.asyncio
async def test_claim_drops_candidate_exceeding_max_attempts_fifo(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify expired PROCESSING candidates exceeding max_attempts in FIFO bucket are dropped."""
    now = datetime.now(UTC)

    # Set counter to 4 so it targets FIFO bucket
    async with session_factory() as session:
        state = await SystemDrainState.get_or_create(session)
        state.drain_counter = 4
        await session.commit()

    job = await _create_test_job(db_session, seeded_company.id, "FIFO Max Attempts Job")
    b = await _create_test_backlog(
        db_session,
        job.id,
        seeded_profile.id,
        score=90,
        status=BacklogStatus.PROCESSING,
        lease_token="expired-token",
        lease_expires_at=now - timedelta(seconds=10),
        attempt_count=5,
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=1, max_attempts=5)
    claimed, _ = await worker._claim_batch()

    assert len(claimed) == 0

    async with session_factory() as session:
        res = await session.execute(
            select(NotificationBacklog).where(NotificationBacklog.id == b.id)
        )
        updated_b = res.scalar_one()
        assert updated_b.status == BacklogStatus.DROPPED
        assert updated_b.lease_token is None


@pytest.mark.asyncio
async def test_promote_deleted_profile_becomes_dropped(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify candidate transitions to DROPPED if profile was deleted before promotion."""
    job = await _create_test_job(db_session, seeded_company.id, "Deleted Profile Job")
    await _create_test_backlog(db_session, job.id, seeded_profile.id)

    worker = BacklogDrainWorker(session_factory=session_factory)
    claimed, _ = await worker._claim_batch()
    assert len(claimed) == 1

    # Simulate profile deletion in DB
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM preference_profiles WHERE id = :pid").params(pid=seeded_profile.id)
        )
        await session.commit()

    status = await worker._promote_candidate(claimed[0])
    assert status == PromotionStatus.DROPPED


@pytest.mark.asyncio
async def test_run_once_statistics_aggregation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    seeded_profile: PreferenceProfile,
) -> None:
    """Verify run_once tracks all promotion status counts in DrainRunResult."""
    job1 = await _create_test_job(db_session, seeded_company.id, "Stat Job 1")
    job2 = await _create_test_job(db_session, seeded_company.id, "Stat Job 2")

    # Item 1: normal promotion
    await _create_test_backlog(db_session, job1.id, seeded_profile.id, score=90)
    # Item 2: will become STALE (profile revision 99)
    await _create_test_backlog(
        db_session, job2.id, seeded_profile.id, profile_revision=99, score=80
    )

    worker = BacklogDrainWorker(session_factory=session_factory, batch_size=2)
    res = await worker.run_once()

    assert res.claimed_count == 2
    assert res.promoted_count == 1
    assert res.stale_revision_count == 1
    assert res.final_drain_counter == 2


# =============================================================================
# 5. SQLITE QUERY PLAN & INDEX PERFORMANCE TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_sqlite_query_plan_zero_temp_btrees(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify EXPLAIN QUERY PLAN for all 4 partial indexes uses index with zero temp B-trees."""
    now = datetime.now(UTC)

    queries = [
        # 1. QUEUED Relevance
        (
            "ix_backlog_queued_relevance",
            "EXPLAIN QUERY PLAN SELECT * FROM notification_backlog "
            "INDEXED BY ix_backlog_queued_relevance "
            "WHERE status = 'QUEUED' ORDER BY score DESC, created_at ASC, id ASC LIMIT 1",
            {},
        ),
        # 2. QUEUED FIFO
        (
            "ix_backlog_queued_fifo",
            "EXPLAIN QUERY PLAN SELECT * FROM notification_backlog "
            "INDEXED BY ix_backlog_queued_fifo "
            "WHERE status = 'QUEUED' ORDER BY created_at ASC, id ASC LIMIT 1",
            {},
        ),
        # 3. PROCESSING Relevance
        (
            "ix_backlog_processing_relevance",
            "EXPLAIN QUERY PLAN SELECT * FROM notification_backlog "
            "INDEXED BY ix_backlog_processing_relevance "
            "WHERE status = 'PROCESSING' AND lease_expires_at <= :now "
            "ORDER BY score DESC, created_at ASC, id ASC LIMIT 1",
            {"now": now},
        ),
        # 4. PROCESSING FIFO
        (
            "ix_backlog_processing_fifo",
            "EXPLAIN QUERY PLAN SELECT * FROM notification_backlog "
            "INDEXED BY ix_backlog_processing_fifo "
            "WHERE status = 'PROCESSING' AND lease_expires_at <= :now "
            "ORDER BY created_at ASC, id ASC LIMIT 1",
            {"now": now},
        ),
    ]

    async with session_factory() as session:
        for idx_name, sql, params in queries:
            res = await session.execute(text(sql).params(**params))
            rows = res.all()
            plan_str = " ".join(str(r) for r in rows).lower()

            # Verify target index is scanned/searched
            assert idx_name.lower() in plan_str, f"Query did not use index {idx_name}: {plan_str}"

            # Verify ZERO temporary B-trees are created for ordering or sorting
            assert "temp b-tree" not in plan_str, (
                f"Query plan for {idx_name} unexpectedly generated temporary B-tree: {plan_str}"
            )
