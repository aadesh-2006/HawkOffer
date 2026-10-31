"""Comprehensive tests for JobDiscoveryWorker (M5 Phase 3 Unit 4)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.company import Company
from app.models.job import Job
from app.models.outbox import BacklogStatus, JobDiscoveryOutbox, NotificationBacklog, OutboxStatus
from app.models.preference import PreferenceProfile
from app.services.discovery_worker import (
    BacklogCandidate,
    ClaimedOutboxRecord,
    JobDiscoveryWorker,
)


@pytest.fixture
async def seeded_company(db_session: AsyncSession) -> Company:
    """Fixture to provide a company for jobs."""
    company = Company(name="Discovery Co", career_url="https://discovery.com")
    db_session.add(company)
    await db_session.commit()
    return company


async def _create_test_job_and_outbox(
    session: AsyncSession,
    company_id: int,
    title: str = "Backend Engineer",
    status: str = OutboxStatus.PENDING,
    lease_token: str | None = None,
    lease_expires_at: datetime | None = None,
    attempt_count: int = 0,
) -> tuple[Job, JobDiscoveryOutbox]:
    """Helper to create a Job and its JobDiscoveryOutbox record."""
    import hashlib

    raw_key = f"{title}_{company_id}_{datetime.now().timestamp()}_{hash(title)}"
    fp = hashlib.sha256(raw_key.encode()).hexdigest()

    job = Job(
        company_id=company_id,
        title=title,
        application_url=f"https://discovery.com/{fp[:8]}",
        fingerprint_hash=fp,
        content_hash=fp,
    )
    session.add(job)
    await session.flush()

    outbox = JobDiscoveryOutbox(
        job_id=job.id,
        status=status,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        attempt_count=attempt_count,
    )
    session.add(outbox)
    await session.commit()
    return job, outbox


# =============================================================================
# 1. CLAIMING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_claim_pending_records(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify that PENDING records are claimed with unique lease tokens and attempt bumped."""
    job1, outbox1 = await _create_test_job_and_outbox(
        db_session, seeded_company.id, title="Engineer 1"
    )
    job2, outbox2 = await _create_test_job_and_outbox(
        db_session, seeded_company.id, title="Engineer 2"
    )

    worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    claimed = await worker._claim_outbox_batch()

    assert len(claimed) == 2
    claimed_ids = {c.id for c in claimed}
    assert claimed_ids == {outbox1.id, outbox2.id}

    # Verify unique lease tokens generated
    tokens = {c.lease_token for c in claimed}
    assert len(tokens) == 2
    assert None not in tokens

    # Verify database state updated
    await db_session.refresh(outbox1)
    await db_session.refresh(outbox2)
    assert outbox1.status == OutboxStatus.PROCESSING
    assert outbox1.attempt_count == 1
    assert outbox1.lease_expires_at is not None
    assert outbox2.status == OutboxStatus.PROCESSING
    assert outbox2.attempt_count == 1


@pytest.mark.asyncio
async def test_reclaim_expired_processing_records(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify expired PROCESSING records are reclaimed, while non-expired ones are skipped."""
    now = datetime.now(UTC)
    past_expiry = now - timedelta(seconds=60)
    future_expiry = now + timedelta(seconds=300)

    # Record 1: Expired processing record -> should be reclaimed
    job1, outbox_expired = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        title="Expired Job",
        status=OutboxStatus.PROCESSING,
        lease_token="old-token-1",
        lease_expires_at=past_expiry,
        attempt_count=1,
    )

    # Record 2: Non-expired processing record -> should NOT be reclaimed
    job2, outbox_active = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        title="Active Job",
        status=OutboxStatus.PROCESSING,
        lease_token="active-token-2",
        lease_expires_at=future_expiry,
        attempt_count=1,
    )

    worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    claimed = await worker._claim_outbox_batch()

    assert len(claimed) == 1
    assert claimed[0].id == outbox_expired.id
    assert claimed[0].lease_token != "old-token-1"

    # Verify active lease was untouched
    await db_session.refresh(outbox_active)
    assert outbox_active.lease_token == "active-token-2"
    assert outbox_active.attempt_count == 1


@pytest.mark.asyncio
async def test_batch_size_limit_respected(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify claim query respects configured batch size limit."""
    for i in range(5):
        await _create_test_job_and_outbox(db_session, seeded_company.id, title=f"Job {i}")

    worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=3)
    claimed = await worker._claim_outbox_batch()
    assert len(claimed) == 3


@pytest.mark.asyncio
async def test_concurrent_claim_protection(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify that multiple concurrent claim calls cannot claim the same record."""
    job, outbox = await _create_test_job_and_outbox(db_session, seeded_company.id, title="Race Job")

    worker1 = JobDiscoveryWorker(session_factory=session_factory, batch_size=1)
    worker2 = JobDiscoveryWorker(session_factory=session_factory, batch_size=1)

    # Execute concurrent claims
    results = await asyncio.gather(worker1._claim_outbox_batch(), worker2._claim_outbox_batch())

    claimed_by_1, claimed_by_2 = results
    total_claimed = len(claimed_by_1) + len(claimed_by_2)
    assert total_claimed == 1  # Exactly one worker acquired the record!


# =============================================================================
# 2. FILTERING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_filtering_creates_candidates_for_matching_profiles(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify matching profiles generate BacklogCandidate records with correct revisions."""
    job, outbox = await _create_test_job_and_outbox(
        db_session, seeded_company.id, title="Senior Python Architect"
    )

    # Create 2 profiles: 1 matching, 1 non-matching (with required role or minimum score)
    profile_match = PreferenceProfile(
        name="Python Match",
        preferred_roles=["Senior Python Architect"],
        minimum_match_score=50.0,
        is_active=True,
    )
    profile_no_match = PreferenceProfile(
        name="Frontend No Match",
        preferred_roles=["React Native Developer"],
        minimum_match_score=50.0,
        is_active=True,
    )
    db_session.add_all([profile_match, profile_no_match])
    await db_session.commit()

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="test-token", attempt_count=1
    )

    candidates_by_outbox, valid_ids = await worker._evaluate_claimed_records([claimed_rec])

    assert outbox.id in valid_ids
    candidates = candidates_by_outbox.get(outbox.id, [])
    assert len(candidates) == 1
    assert candidates[0].profile_id == profile_match.id
    assert candidates[0].score > 80
    assert candidates[0].profile_revision == profile_match.revision


@pytest.mark.asyncio
async def test_empty_active_profiles_yields_zero_candidates(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify jobs evaluated when no active profiles exist produce zero candidates."""
    job, outbox = await _create_test_job_and_outbox(db_session, seeded_company.id)

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="test-token", attempt_count=1
    )

    candidates_by_outbox, valid_ids = await worker._evaluate_claimed_records([claimed_rec])
    assert outbox.id in valid_ids
    assert len(candidates_by_outbox) == 0


# =============================================================================
# 3. FENCED COMMIT TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_fenced_commit_success_and_atomicity(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify valid lease token commits candidates and transitions outbox atomically."""
    job, outbox = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        status=OutboxStatus.PROCESSING,
        lease_token="valid-token-123",
        attempt_count=1,
    )
    profile = PreferenceProfile(name="Dev Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="valid-token-123", attempt_count=1
    )

    from app.services.discovery_worker import BacklogCandidate

    candidate = BacklogCandidate(
        outbox_id=outbox.id,
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        score=95,
    )

    success, was_stale = await worker._fenced_commit_outbox(claimed_rec, [candidate])
    assert success is True
    assert was_stale is False

    # Verify outbox status updated to PROCESSED
    await db_session.refresh(outbox)
    assert outbox.status == OutboxStatus.PROCESSED
    assert outbox.lease_token is None
    assert outbox.processed_at is not None

    # Verify NotificationBacklog row inserted
    res = await db_session.execute(
        select(NotificationBacklog).where(NotificationBacklog.job_id == job.id)
    )
    backlog_entry = res.scalar_one_or_none()
    assert backlog_entry is not None
    assert backlog_entry.score == 95
    assert backlog_entry.status == BacklogStatus.QUEUED
    assert backlog_entry.profile_revision == profile.revision


@pytest.mark.asyncio
async def test_fenced_commit_rejects_stale_lease_token(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Regression test: Old worker with replaced lease token CANNOT commit stale results."""
    # Worker A claimed lease with 'token-A'
    job, outbox = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        status=OutboxStatus.PROCESSING,
        lease_token="token-A",
        attempt_count=1,
    )
    profile = PreferenceProfile(name="Profile A", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    # Simulate lease expiry and Worker B reclaiming outbox with 'token-B'
    await db_session.execute(
        update(JobDiscoveryOutbox)
        .where(JobDiscoveryOutbox.id == outbox.id)
        .values(lease_token="token-B", attempt_count=2)
    )
    await db_session.commit()

    # Worker A now attempts fenced commit using its stale 'token-A'
    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec_a = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="token-A", attempt_count=1
    )

    from app.services.discovery_worker import BacklogCandidate

    candidate = BacklogCandidate(
        outbox_id=outbox.id,
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        score=90,
    )

    success, was_stale = await worker._fenced_commit_outbox(claimed_rec_a, [candidate])
    assert success is False
    assert was_stale is True

    # Assert Worker B's lease ownership is intact and no backlog rows were persisted by Worker A
    await db_session.refresh(outbox)
    assert outbox.lease_token == "token-B"
    assert outbox.status == OutboxStatus.PROCESSING

    backlog_res = await db_session.execute(
        select(NotificationBacklog).where(NotificationBacklog.job_id == job.id)
    )
    assert backlog_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_fenced_commit_rejects_stale_profile_revision(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify that if a profile revision changes during filtering, candidate is discarded."""
    job, outbox = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        status=OutboxStatus.PROCESSING,
        lease_token="valid-token",
        attempt_count=1,
    )
    profile = PreferenceProfile(name="Criteria Changing Profile", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    # Profile criteria modified concurrently -> revision bumps to 2
    profile.revision = 2
    await db_session.commit()

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="valid-token", attempt_count=1
    )

    from app.services.discovery_worker import BacklogCandidate

    # Candidate evaluated under stale revision 1
    stale_candidate = BacklogCandidate(
        outbox_id=outbox.id,
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=1,
        score=88,
    )

    success, was_stale = await worker._fenced_commit_outbox(claimed_rec, [stale_candidate])
    assert success is True  # Outbox transitions PROCESSED, but stale candidate discarded

    # Verify zero backlog entries were created for stale revision 1
    backlog_res = await db_session.execute(
        select(NotificationBacklog).where(NotificationBacklog.job_id == job.id)
    )
    assert backlog_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_job_with_no_matching_profiles_marks_outbox_processed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify a job with no qualifying profile matches is still marked PROCESSED without errors."""
    job, outbox = await _create_test_job_and_outbox(
        db_session,
        seeded_company.id,
        status=OutboxStatus.PROCESSING,
        lease_token="no-match-token",
        attempt_count=1,
    )

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed_rec = ClaimedOutboxRecord(
        id=outbox.id, job_id=job.id, lease_token="no-match-token", attempt_count=1
    )

    # Empty candidates list
    success, was_stale = await worker._fenced_commit_outbox(claimed_rec, [])
    assert success is True
    assert was_stale is False

    await db_session.refresh(outbox)
    assert outbox.status == OutboxStatus.PROCESSED
    assert outbox.lease_token is None


# =============================================================================
# 4. FAILURE HANDLING & END-TO-END EXECUTION
# =============================================================================


@pytest.mark.asyncio
async def test_end_to_end_run_once(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify full end-to-end run_once lifecycle across Phases A, B, and C."""
    job, outbox = await _create_test_job_and_outbox(
        db_session, seeded_company.id, title="Lead Staff Systems Engineer"
    )

    profile = PreferenceProfile(
        name="Staff Lead Profile",
        preferred_roles=["Lead Staff Systems Engineer"],
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    result = await worker.run_once()

    assert result.claimed_count == 1
    assert result.evaluated_jobs == 1
    assert result.candidate_matches == 1
    assert result.processed_outbox_count == 1
    assert result.failed_outbox_count == 0

    await db_session.refresh(outbox)
    assert outbox.status == OutboxStatus.PROCESSED

    backlog_items = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlog_items) == 1
    assert backlog_items[0].job_id == job.id
    assert backlog_items[0].profile_id == profile.id


@pytest.mark.asyncio
async def test_deleted_job_is_handled_safely(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify if a Job is deleted before processing, orphaned outbox row is pruned safely."""
    job, outbox = await _create_test_job_and_outbox(db_session, seeded_company.id)

    # Manually delete Job without cascade in session to simulate orphaned outbox record
    # (or job deleted in concurrent transaction)
    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed = await worker._claim_outbox_batch()
    assert len(claimed) == 1

    # Delete job directly
    await db_session.delete(job)
    await db_session.commit()

    # Worker runs remaining phases
    candidates_by_outbox, valid_ids = await worker._evaluate_claimed_records(claimed)
    assert claimed[0].id not in valid_ids

    # Calling _handle_missing_job prunes the outbox row
    await worker._handle_missing_job(claimed[0])

    res = await db_session.execute(
        select(JobDiscoveryOutbox).where(JobDiscoveryOutbox.id == outbox.id)
    )
    assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_run_once_empty_outbox_returns_zero_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify run_once returns immediately with zeroes when no outbox items are eligible."""
    worker = JobDiscoveryWorker(session_factory=session_factory)
    result = await worker.run_once()
    assert result.claimed_count == 0
    assert result.processed_outbox_count == 0
    assert result.candidate_matches == 0


@pytest.mark.asyncio
async def test_fenced_commit_skips_deactivated_profile(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify candidate is skipped if profile became inactive before fenced commit."""
    job, outbox = await _create_test_job_and_outbox(db_session, seeded_company.id)
    profile = PreferenceProfile(name="Temp Profile", is_active=True, revision=1)
    db_session.add(profile)
    await db_session.commit()

    worker = JobDiscoveryWorker(session_factory=session_factory)
    claimed = await worker._claim_outbox_batch()
    assert len(claimed) == 1

    candidate = BacklogCandidate(
        outbox_id=claimed[0].id,
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        score=85,
    )

    # Deactivate profile prior to commit
    profile.is_active = False
    await db_session.commit()

    success, was_stale = await worker._fenced_commit_outbox(claimed[0], [candidate])
    assert success is True
    assert was_stale is False

    # Outbox is marked processed, but backlog candidate was skipped
    await db_session.refresh(outbox)
    assert outbox.status == OutboxStatus.PROCESSED

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 0


@pytest.mark.asyncio
async def test_record_outbox_failure_transitions_to_pending_or_failed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify failure resets to PENDING if attempts < max_attempts, else transitions to FAILED."""
    job1, outbox1 = await _create_test_job_and_outbox(db_session, seeded_company.id)
    job2, outbox2 = await _create_test_job_and_outbox(db_session, seeded_company.id)

    worker = JobDiscoveryWorker(session_factory=session_factory, max_attempts=3)

    # Outbox 1: attempt 1 < 3 -> returns to PENDING
    outbox1.attempt_count = 1
    outbox1.status = OutboxStatus.PROCESSING
    outbox1.lease_token = "lease-1"
    # Outbox 2: attempt 3 >= 3 -> transitions to FAILED
    outbox2.attempt_count = 3
    outbox2.status = OutboxStatus.PROCESSING
    outbox2.lease_token = "lease-2"
    await db_session.commit()

    await worker._record_outbox_failure(outbox1.id, "Simulated transient failure")
    await worker._record_outbox_failure(outbox2.id, "Simulated fatal failure")

    await db_session.refresh(outbox1)
    await db_session.refresh(outbox2)

    assert outbox1.status == OutboxStatus.PENDING
    assert outbox1.lease_token is None
    assert outbox1.last_error == "Simulated transient failure"

    assert outbox2.status == OutboxStatus.FAILED
    assert outbox2.lease_token is None
    assert outbox2.last_error == "Simulated fatal failure"


@pytest.mark.asyncio
async def test_run_once_records_stale_lease_skip(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify stale lease skip counter increments during run_once."""
    job, outbox = await _create_test_job_and_outbox(db_session, seeded_company.id)
    worker = JobDiscoveryWorker(session_factory=session_factory)

    # Intercept _fenced_commit_outbox to return (False, True) simulating stale lease
    async def mock_fenced_commit(record: Any, candidates: Any) -> tuple[bool, bool]:
        return False, True

    monkeypatch.setattr(worker, "_fenced_commit_outbox", mock_fenced_commit)

    result = await worker.run_once()
    assert result.claimed_count == 1
    assert result.stale_lease_skips == 1
    assert result.processed_outbox_count == 0
