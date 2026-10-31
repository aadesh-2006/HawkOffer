"""Tests for Profile Revision Management and Historical Reevaluation (M5 Phase 3 Unit 5)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utc_now
from app.models.company import Company
from app.models.job import Job
from app.models.outbox import (
    BacklogStatus,
    NotificationBacklog,
    ProfileReevaluationTask,
    ReevaluationStatus,
)
from app.models.preference import PreferenceProfile
from app.schemas.preference import PreferenceProfileUpdate
from app.services.profile_service import ProfileUpdateService
from app.services.reevaluation_worker import (
    ChunkCommitStatus,
    ClaimedReevaluationTask,
    ProfileReevaluationWorker,
    ReevaluationCandidate,
)


@pytest.fixture
async def seeded_company(db_session: AsyncSession) -> Company:
    """Fixture providing a company for job creation."""
    company = Company(name="Reeval Tech", career_url="https://reeval.com")
    db_session.add(company)
    await db_session.commit()
    return company


async def _create_test_job(
    session: AsyncSession,
    company_id: int,
    title: str = "Software Engineer",
    description: str | None = None,
) -> Job:
    """Helper to insert a Job record."""
    import hashlib

    raw = f"{title}_{company_id}_{datetime.now().timestamp()}_{hash(title)}"
    fp = hashlib.sha256(raw.encode()).hexdigest()

    job = Job(
        company_id=company_id,
        title=title,
        description=description,
        application_url=f"https://reeval.com/{fp[:8]}",
        fingerprint_hash=fp,
        content_hash=fp,
    )
    session.add(job)
    await session.commit()
    return job


# =============================================================================
# 1. PROFILE UPDATES & REVISION MANAGEMENT
# =============================================================================


@pytest.mark.asyncio
async def test_meaningful_preference_update_increments_revision_once(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that updating filtering preferences increments revision from 1 to 2."""
    profile = PreferenceProfile(name="Profile 1", revision=1, preferred_roles=["DevOps"])
    db_session.add(profile)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)
    updated_prof, task = await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(preferred_roles=["Senior Python Engineer"]),
    )

    assert updated_prof is not None
    assert updated_prof.revision == 2
    assert updated_prof.preferred_roles == ["Senior Python Engineer"]
    assert task is not None
    assert task.profile_revision == 2
    assert task.status == ReevaluationStatus.PENDING


@pytest.mark.asyncio
async def test_noop_update_does_not_increment_revision(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that updating with identical preferences or only name does not bump revision."""
    profile = PreferenceProfile(
        name="Original Name",
        revision=1,
        preferred_roles=["DevOps"],
        minimum_match_score=60.0,
    )
    db_session.add(profile)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)

    # 1. Empty update
    p1, t1 = await service.update_profile(profile.id, PreferenceProfileUpdate())
    assert p1 is not None and p1.revision == 1
    assert t1 is None

    # 2. Identical values
    p2, t2 = await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(preferred_roles=["DevOps"], minimum_match_score=60.0),
    )
    assert p2 is not None and p2.revision == 1
    assert t2 is None

    # 3. Only name changed
    p3, t3 = await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(name="Brand New Name"),
    )
    assert p3 is not None and p3.revision == 1
    assert p3.name == "Brand New Name"
    assert t3 is None


@pytest.mark.asyncio
async def test_old_queued_backlog_rows_become_stale(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify existing QUEUED backlog items transition to STALE when preferences change."""
    profile = PreferenceProfile(name="Backlog User", revision=1)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    # Add QUEUED backlog row for rev 1
    backlog_item = NotificationBacklog(
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=1,
        score=75,
        status=BacklogStatus.QUEUED,
    )
    db_session.add(backlog_item)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)
    await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(preferred_roles=["Data Scientist"]),
    )

    await db_session.refresh(backlog_item)
    assert backlog_item.status == BacklogStatus.STALE


@pytest.mark.asyncio
async def test_existing_active_task_becomes_superseded(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify any active PENDING or RUNNING task is transitioned to SUPERSEDED."""
    profile = PreferenceProfile(name="Multi Task User", revision=1)
    db_session.add(profile)
    await db_session.commit()

    # Create active task for rev 1
    old_task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="active-lease-token",
    )
    db_session.add(old_task)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)
    _, new_task = await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(preferred_roles=["New Role"]),
    )

    await db_session.refresh(old_task)
    assert old_task.status == ReevaluationStatus.SUPERSEDED
    assert old_task.lease_token is None

    assert new_task is not None
    assert new_task.status == ReevaluationStatus.PENDING
    assert new_task.profile_revision == 2


@pytest.mark.asyncio
async def test_new_task_captures_correct_revision_and_max_job_id(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify the new task records the incremented revision and the exact max Job ID snapshot."""
    profile = PreferenceProfile(name="Job Snapshot Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    job1 = await _create_test_job(db_session, seeded_company.id, title="Job 1")
    job2 = await _create_test_job(db_session, seeded_company.id, title="Job 2")
    expected_max = max(job1.id, job2.id)

    service = ProfileUpdateService(session_factory=session_factory)
    _, new_task = await service.update_profile(
        profile.id,
        PreferenceProfileUpdate(preferred_roles=["Tech Lead"]),
    )

    assert new_task is not None
    assert new_task.profile_revision == 2
    assert new_task.max_job_id == expected_max
    assert new_task.last_processed_job_id == 0


@pytest.mark.asyncio
async def test_profile_update_and_task_creation_are_atomic(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify transaction rollback if an error occurs during task creation."""
    profile = PreferenceProfile(name="Atomic Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)

    # Force failure during commit
    async def failing_commit(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated DB commit error")

    # Patch session.commit in _update_profile_in_session
    original_update = service._update_profile_in_session

    async def patched_update(session: AsyncSession, p_id: int, u_data: Any) -> Any:
        monkeypatch.setattr(session, "commit", failing_commit)
        return await original_update(session, p_id, u_data)

    monkeypatch.setattr(service, "_update_profile_in_session", patched_update)

    with pytest.raises(RuntimeError, match="Simulated DB commit error"):
        await service.update_profile(
            profile.id, PreferenceProfileUpdate(preferred_roles=["AI Dev"])
        )

    await db_session.refresh(profile)
    assert profile.revision == 1

    tasks = (await db_session.execute(select(ProfileReevaluationTask))).scalars().all()
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_concurrent_updates_do_not_create_multiple_active_tasks(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify serial execution under BEGIN IMMEDIATE prevents multiple active tasks."""
    profile = PreferenceProfile(name="Concurrent Target", revision=1)
    db_session.add(profile)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)

    # Run two updates concurrently
    await asyncio.gather(
        service.update_profile(profile.id, PreferenceProfileUpdate(preferred_roles=["Role A"])),
        service.update_profile(profile.id, PreferenceProfileUpdate(preferred_roles=["Role B"])),
    )

    # Check that both succeeded in bumping revision
    await db_session.refresh(profile)
    assert profile.revision == 3

    # Verify only ONE task is currently active (PENDING or RUNNING)
    active_tasks = (
        (
            await db_session.execute(
                select(ProfileReevaluationTask).where(
                    ProfileReevaluationTask.profile_id == profile.id,
                    ProfileReevaluationTask.status.in_(
                        [ReevaluationStatus.PENDING, ReevaluationStatus.RUNNING]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active_tasks) == 1
    assert active_tasks[0].profile_revision == 3


# =============================================================================
# 2. TASK CLAIMING & CONCURRENCY
# =============================================================================


@pytest.mark.asyncio
async def test_pending_task_claim(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify claiming a PENDING task transitions it to RUNNING with lease token."""
    profile = PreferenceProfile(name="Claim Target", revision=1)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=50,
        status=ReevaluationStatus.PENDING,
        attempt_count=0,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = await worker._claim_task()

    assert claimed is not None
    assert claimed.id == task.id
    assert claimed.attempt_count == 1
    assert len(claimed.lease_token) == 36

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.RUNNING
    assert task.lease_token == claimed.lease_token
    assert task.lease_expires_at is not None
    expiry = (
        task.lease_expires_at
        if task.lease_expires_at.tzinfo
        else task.lease_expires_at.replace(tzinfo=UTC)
    )
    assert expiry > utc_now()


@pytest.mark.asyncio
async def test_expired_running_task_reclaim(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify an expired RUNNING task is reclaimed with an incremented attempt count."""
    profile = PreferenceProfile(name="Reclaim Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    past_expiry = utc_now() - timedelta(minutes=5)
    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=25,
        status=ReevaluationStatus.RUNNING,
        lease_token="expired-token",
        lease_expires_at=past_expiry,
        attempt_count=1,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = await worker._claim_task()

    assert claimed is not None
    assert claimed.id == task.id
    assert claimed.attempt_count == 2
    assert claimed.lease_token != "expired-token"

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.RUNNING
    assert task.lease_token == claimed.lease_token


@pytest.mark.asyncio
async def test_active_lease_not_reclaimed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify an actively running task with unexpired lease is not reclaimed."""
    profile = PreferenceProfile(name="Active Lease Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    future_expiry = utc_now() + timedelta(minutes=5)
    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=25,
        status=ReevaluationStatus.RUNNING,
        lease_token="valid-active-token",
        lease_expires_at=future_expiry,
        attempt_count=1,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = await worker._claim_task()
    assert claimed is None


@pytest.mark.asyncio
async def test_unique_lease_token_and_attempt_increment(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify lease token generation creates valid UUID4 and increments attempts."""
    profile = PreferenceProfile(name="UUID Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = await worker._claim_task()
    assert claimed is not None
    import uuid

    uuid.UUID(claimed.lease_token)
    assert claimed.attempt_count == 1


@pytest.mark.asyncio
async def test_concurrent_claims_cannot_acquire_the_same_active_task(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that multiple workers claiming concurrently only yield 1 claim for a single task."""
    profile = PreferenceProfile(name="Solo Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker1 = ProfileReevaluationWorker(session_factory=session_factory)
    worker2 = ProfileReevaluationWorker(session_factory=session_factory)

    c1, c2 = await asyncio.gather(worker1._claim_task(), worker2._claim_task())
    claimed_results = [c for c in (c1, c2) if c is not None]
    assert len(claimed_results) == 1


# =============================================================================
# 3. HISTORICAL REEVALUATION KEYSET PAGINATION & FILTERING
# =============================================================================


@pytest.mark.asyncio
async def test_keyset_pagination_processes_jobs_in_ascending_order(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify jobs are fetched and examined in strictly ascending order."""
    profile = PreferenceProfile(name="Asc Order Profile", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    # Create 4 jobs
    await _create_test_job(db_session, seeded_company.id, title="Job 1")
    j2 = await _create_test_job(db_session, seeded_company.id, title="Job 2")
    await _create_test_job(db_session, seeded_company.id, title="Job 3")
    j4 = await _create_test_job(db_session, seeded_company.id, title="Job 4")

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=j4.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="lease-asc",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=j4.id,
        last_processed_job_id=0,
        lease_token="lease-asc",
        attempt_count=1,
    )

    # Use chunk_size = 2
    worker = ProfileReevaluationWorker(session_factory=session_factory, chunk_size=2)
    status1, cursor1, _, evaluated_count1 = await worker._process_chunk(claimed)
    assert status1 == ChunkCommitStatus.COMMITTED
    assert cursor1 == j2.id
    assert evaluated_count1 == 2

    claimed.last_processed_job_id = cursor1
    status2, cursor2, _, evaluated_count2 = await worker._process_chunk(claimed)
    assert status2 == ChunkCommitStatus.COMMITTED
    assert cursor2 == j4.id
    assert evaluated_count2 == 2


@pytest.mark.asyncio
async def test_chunk_boundaries_do_not_skip_or_repeat_jobs(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify chunk boundary traversal processes every job exactly once."""
    profile = PreferenceProfile(
        name="Chunk Boundary Profile",
        preferred_roles=["Target Role"],
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    job_ids = []
    for i in range(5):
        j = await _create_test_job(db_session, seeded_company.id, title=f"Target Role {i}")
        job_ids.append(j.id)

    max_id = max(job_ids)
    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=max_id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory, chunk_size=2)
    res = await worker.run_once()

    assert res.tasks_completed == 1
    assert res.jobs_evaluated == 5

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.COMPLETED
    assert task.last_processed_job_id == max_id


@pytest.mark.asyncio
async def test_only_jobs_at_or_below_max_job_id_are_evaluated(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify jobs created after max_job_id snapshot are never processed by the historical task."""
    profile = PreferenceProfile(name="Frozen Snapshot Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    await _create_test_job(db_session, seeded_company.id, title="Old Job 1")
    j2 = await _create_test_job(db_session, seeded_company.id, title="Old Job 2")

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=j2.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    # Job created after task snapshot
    await _create_test_job(db_session, seeded_company.id, title="New Job 3")

    worker = ProfileReevaluationWorker(session_factory=session_factory, chunk_size=10)
    res = await worker.run_once()

    assert res.jobs_evaluated == 2
    assert res.tasks_completed == 1

    await db_session.refresh(task)
    assert task.last_processed_job_id == j2.id


@pytest.mark.asyncio
async def test_matching_jobs_create_backlog_candidates(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify qualifying jobs are persisted into NotificationBacklog as QUEUED candidates."""
    profile = PreferenceProfile(
        name="Matching Profile",
        preferred_roles=["Senior Python Architect"],
        minimum_match_score=50.0,
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id, title="Senior Python Architect")

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    res = await worker.run_once()

    assert res.candidates_created == 1
    assert res.tasks_completed == 1

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 1
    assert backlogs[0].job_id == job.id
    assert backlogs[0].profile_id == profile.id
    assert backlogs[0].status == BacklogStatus.QUEUED


@pytest.mark.asyncio
async def test_nonmatching_jobs_create_none(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify non-qualifying jobs do not create backlog records."""
    profile = PreferenceProfile(
        name="Strict Profile",
        preferred_roles=["Senior Rust Core Developer"],
        minimum_match_score=80.0,
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id, title="Frontend UI Designer")

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    res = await worker.run_once()

    assert res.jobs_evaluated == 1
    assert res.candidates_created == 0
    assert res.tasks_completed == 1

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 0


@pytest.mark.asyncio
async def test_empty_history_completes_correctly(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a task with max_job_id=0 completes immediately without errors."""
    profile = PreferenceProfile(name="Empty DB Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=0,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    res = await worker.run_once()

    assert res.tasks_completed == 1
    assert res.jobs_evaluated == 0

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.COMPLETED


@pytest.mark.asyncio
async def test_deleted_profiles_are_handled_safely(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify task is CANCELLED or cascade-deleted safely if profile is deleted."""
    profile = PreferenceProfile(name="Transient Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = await worker._claim_task()
    assert claimed is not None

    # Delete profile directly (triggers FK CASCADE in SQLite WAL)
    await db_session.delete(profile)
    await db_session.commit()

    status, _, _, _ = await worker._process_chunk(claimed)
    assert status == ChunkCommitStatus.CANCELLED

    task_db = (
        await db_session.execute(
            select(ProfileReevaluationTask).where(ProfileReevaluationTask.id == task.id)
        )
    ).scalar_one_or_none()
    assert task_db is None or task_db.status == ReevaluationStatus.CANCELLED


# =============================================================================
# 4. FENCING, RETRIES & HEARTBEAT
# =============================================================================


@pytest.mark.asyncio
async def test_old_lease_token_cannot_commit(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify chunk commit is rejected if task lease_token was rotated."""
    profile = PreferenceProfile(name="Fencing Target", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="worker-a-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="worker-a-token",
        attempt_count=1,
    )

    # Concurrently overtake lease
    task.lease_token = "worker-b-token"
    await db_session.commit()

    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=90
    )
    status = await worker._fenced_commit_chunk(claimed, job.id, [candidate])

    assert status == ChunkCommitStatus.STALE_LEASE

    # Backlog row must NOT be committed
    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 0


@pytest.mark.asyncio
async def test_superseded_task_cannot_commit(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify task transitioned to SUPERSEDED rejects commits."""
    profile = PreferenceProfile(name="Superseded Target", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.SUPERSEDED,
        lease_token=None,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="stale-token",
        attempt_count=1,
    )

    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=90
    )
    status = await worker._fenced_commit_chunk(claimed, job.id, [candidate])
    assert status == ChunkCommitStatus.STALE_LEASE


@pytest.mark.asyncio
async def test_fenced_commit_chunk_rejects_deleted_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify _fenced_commit_chunk returns STALE_LEASE if task was completely deleted."""
    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=999999,
        profile_id=1,
        profile_revision=1,
        max_job_id=10,
        last_processed_job_id=0,
        lease_token="ghost-token",
        attempt_count=1,
    )
    candidate = ReevaluationCandidate(job_id=1, profile_id=1, profile_revision=1, score=90)
    status = await worker._fenced_commit_chunk(claimed, 1, [candidate])
    assert status == ChunkCommitStatus.STALE_LEASE


@pytest.mark.asyncio
async def test_fenced_commit_chunk_cancels_if_profile_deactivated_prior_to_commit(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify _fenced_commit_chunk cancels task if profile becomes inactive before commit."""
    profile = PreferenceProfile(name="Pre-Commit Deact", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="valid-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    # Deactivate profile
    profile.is_active = False
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="valid-token",
        attempt_count=1,
    )
    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=90
    )
    status = await worker._fenced_commit_chunk(claimed, job.id, [candidate])
    assert status == ChunkCommitStatus.CANCELLED

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.CANCELLED


@pytest.mark.asyncio
async def test_changed_profile_revision_blocks_stale_candidates(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify that if profile revision changed, commit is blocked and task superseded."""
    profile = PreferenceProfile(name="Rev Mismatch Profile", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="eval-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    # Profile revision was updated to 2
    profile.revision = 2
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="eval-token",
        attempt_count=1,
    )

    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=90
    )
    status = await worker._fenced_commit_chunk(claimed, job.id, [candidate])

    assert status == ChunkCommitStatus.SUPERSEDED

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.SUPERSEDED

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 0


@pytest.mark.asyncio
async def test_candidate_insert_and_cursor_update_are_atomic(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify cursor advancement occurs atomically with candidate insertion."""
    profile = PreferenceProfile(name="Atomic Chunk Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="atomic-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="atomic-token",
        attempt_count=1,
    )

    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=85
    )
    status = await worker._fenced_commit_chunk(claimed, job.id, [candidate])

    assert status == ChunkCommitStatus.COMMITTED

    await db_session.refresh(task)
    assert task.last_processed_job_id == job.id

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 1


@pytest.mark.asyncio
async def test_failure_retries_from_the_last_committed_cursor(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify that after a simulated failure, a retry resumes from the last committed cursor."""
    profile = PreferenceProfile(
        name="Retry Profile",
        preferred_roles=["Software Engineer"],
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    j1 = await _create_test_job(db_session, seeded_company.id, title="Software Engineer 1")
    await _create_test_job(db_session, seeded_company.id, title="Software Engineer 2")
    j3 = await _create_test_job(db_session, seeded_company.id, title="Software Engineer 3")

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=j3.id,
        last_processed_job_id=j1.id,  # Chunk 1 previously committed at j1
        status=ReevaluationStatus.PENDING,
        attempt_count=1,
    )
    db_session.add(task)
    await db_session.commit()

    # Re-run worker
    worker = ProfileReevaluationWorker(session_factory=session_factory, chunk_size=2)
    res = await worker.run_once()

    assert res.tasks_completed == 1
    # Only jobs > j1 (i.e. j2 and j3) were evaluated
    assert res.jobs_evaluated == 2

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.COMPLETED
    assert task.last_processed_job_id == j3.id


@pytest.mark.asyncio
async def test_duplicate_chunk_execution_remains_idempotent(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify ON CONFLICT DO NOTHING prevents duplicate backlog records when chunk repeats."""
    profile = PreferenceProfile(name="Idempotent Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="idem-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="idem-token",
        attempt_count=1,
    )

    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=88
    )

    # First commit
    s1 = await worker._fenced_commit_chunk(claimed, job.id, [candidate])
    assert s1 == ChunkCommitStatus.COMMITTED

    # Duplicate commit of same candidate
    s2 = await worker._fenced_commit_chunk(claimed, job.id, [candidate])
    assert s2 == ChunkCommitStatus.COMMITTED

    backlogs = (await db_session.execute(select(NotificationBacklog))).scalars().all()
    assert len(backlogs) == 1


@pytest.mark.asyncio
async def test_heartbeat_renews_a_valid_lease(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify renew_lease successfully advances lease_expires_at when holding valid token."""
    profile = PreferenceProfile(name="Heartbeat Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    initial_expiry = utc_now() + timedelta(seconds=10)
    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="heartbeat-valid-token",
        lease_expires_at=initial_expiry,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory, lease_duration_seconds=300)
    renewed = await worker.renew_lease(task.id, "heartbeat-valid-token")
    assert renewed is True

    await db_session.refresh(task)
    assert task.lease_expires_at is not None
    expiry = (
        task.lease_expires_at
        if task.lease_expires_at.tzinfo
        else task.lease_expires_at.replace(tzinfo=UTC)
    )
    assert expiry > initial_expiry


@pytest.mark.asyncio
async def test_lost_lease_prevents_heartbeat_renewal_and_chunk_commit(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify renew_lease and chunk commit fail if worker lease token is invalid."""
    profile = PreferenceProfile(name="Lost Lease Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.RUNNING,
        lease_token="real-token",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)

    # 1. Heartbeat renewal fails
    renewed = await worker.renew_lease(task.id, "bogus-stale-token")
    assert renewed is False

    # 2. Chunk commit fails
    stale_claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        lease_token="bogus-stale-token",
        attempt_count=1,
    )
    candidate = ReevaluationCandidate(
        job_id=job.id, profile_id=profile.id, profile_revision=1, score=77
    )
    status = await worker._fenced_commit_chunk(stale_claimed, job.id, [candidate])
    assert status == ChunkCommitStatus.STALE_LEASE


# =============================================================================
# 5. EDGE CASES & COVERAGE BOOST
# =============================================================================


@pytest.mark.asyncio
async def test_update_profile_with_direct_session_and_dict(
    db_session: AsyncSession,
) -> None:
    """Verify ProfileUpdateService works with an injected session and dict payload."""
    profile = PreferenceProfile(name="Session Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    service = ProfileUpdateService(session=db_session)
    updated, task = await service.update_profile(
        profile.id, {"preferred_roles": ["Direct Session Role"]}
    )

    assert updated is not None
    assert updated.revision == 2
    assert task is not None
    assert task.profile_revision == 2


@pytest.mark.asyncio
async def test_update_nonexistent_profile_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify updating a non-existent profile ID safely returns (None, None)."""
    service = ProfileUpdateService(session_factory=session_factory)
    prof, task = await service.update_profile(999999, {"name": "Ghost Profile"})
    assert prof is None
    assert task is None


@pytest.mark.asyncio
async def test_profile_deactivation_invalidates_backlog_and_supersedes_tasks(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify deactivating profile sets backlog STALE, task SUPERSEDED, and no new task."""
    profile = PreferenceProfile(name="Deactivate Profile", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    backlog = NotificationBacklog(
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=1,
        score=70,
        status=BacklogStatus.QUEUED,
    )
    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        status=ReevaluationStatus.RUNNING,
        lease_token="lease-deact",
    )
    db_session.add(backlog)
    db_session.add(task)
    await db_session.commit()

    service = ProfileUpdateService(session_factory=session_factory)
    updated, new_task = await service.update_profile(
        profile.id, PreferenceProfileUpdate(is_active=False)
    )

    assert updated is not None
    assert updated.is_active is False
    assert updated.revision == 1  # No revision increment on deactivation
    assert new_task is None  # No new task scheduled

    await db_session.refresh(backlog)
    assert backlog.status == BacklogStatus.STALE

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_profile_reactivation_increments_revision_and_enqueues_task(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify reactivating an inactive profile increments revision and schedules reevaluation."""
    profile = PreferenceProfile(name="Inactive Profile", revision=1, is_active=False)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    service = ProfileUpdateService(session_factory=session_factory)
    updated, new_task = await service.update_profile(
        profile.id, PreferenceProfileUpdate(is_active=True)
    )

    assert updated is not None
    assert updated.is_active is True
    assert updated.revision == 2
    assert new_task is not None
    assert new_task.profile_revision == 2
    assert new_task.max_job_id == job.id


@pytest.mark.asyncio
async def test_task_claim_fails_when_exceeding_max_attempts(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify task with attempt_count >= max_attempts is transitioned to FAILED and not claimed."""
    profile = PreferenceProfile(name="Failed Task Profile", revision=1)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.PENDING,
        attempt_count=5,  # >= max_attempts (default 5)
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory, max_attempts=5)
    claimed = await worker._claim_task()
    assert claimed is None

    await db_session.refresh(task)
    assert task.status == ReevaluationStatus.FAILED
    assert "Exceeded maximum attempt" in (task.last_error or "")


@pytest.mark.asyncio
async def test_record_task_failure_transitions_to_pending_or_failed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify _record_task_failure resets to PENDING if attempts < max else transitions FAILED."""
    p1 = PreferenceProfile(name="Failure P1", revision=1)
    p2 = PreferenceProfile(name="Failure P2", revision=1)
    db_session.add(p1)
    db_session.add(p2)
    await db_session.commit()

    t1 = ProfileReevaluationTask(
        profile_id=p1.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="t1-token",
        attempt_count=1,
    )
    t2 = ProfileReevaluationTask(
        profile_id=p2.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="t2-token",
        attempt_count=3,
    )
    db_session.add(t1)
    db_session.add(t2)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory, max_attempts=3)
    await worker._record_task_failure(t1.id, "Transient failure")
    await worker._record_task_failure(t2.id, "Fatal failure")

    await db_session.refresh(t1)
    await db_session.refresh(t2)

    assert t1.status == ReevaluationStatus.PENDING
    assert t1.lease_token is None
    assert t1.last_error == "Transient failure"

    assert t2.status == ReevaluationStatus.FAILED
    assert t2.lease_token is None
    assert t2.last_error == "Fatal failure"


@pytest.mark.asyncio
async def test_run_once_records_superseded_task_statistics(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify worker.run_once increments tasks_superseded when profile revision changes mid-run."""
    profile = PreferenceProfile(name="Mid Run Supersede", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)

    # Intercept _process_chunk to return SUPERSEDED
    async def mock_chunk(*args: Any, **kwargs: Any) -> tuple[ChunkCommitStatus, int, int, int]:
        return ChunkCommitStatus.SUPERSEDED, 0, 0, 1

    worker._process_chunk = mock_chunk  # type: ignore

    res = await worker.run_once()
    assert res.tasks_claimed == 1
    assert res.tasks_superseded == 1
    assert res.tasks_completed == 0


@pytest.mark.asyncio
async def test_run_once_with_no_pending_tasks_returns_zeroes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify run_once returns zeroes immediately when no tasks are eligible."""
    worker = ProfileReevaluationWorker(session_factory=session_factory)
    res = await worker.run_once()
    assert res.tasks_claimed == 0
    assert res.chunks_processed == 0
    assert res.tasks_completed == 0


@pytest.mark.asyncio
async def test_heartbeat_loop_executes_renewal_periodically(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify background heartbeat loop executes renew_lease and stops on event."""
    profile = PreferenceProfile(name="Loop Profile", is_active=True)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="loop-token",
        lease_expires_at=utc_now() + timedelta(seconds=10),
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(
        session_factory=session_factory,
        heartbeat_interval_seconds=1,  # Short 1-second interval
        lease_duration_seconds=300,
    )

    stop_event = asyncio.Event()
    loop_task = asyncio.create_task(worker._heartbeat_loop(task.id, "loop-token", stop_event))

    # Let it tick once
    await asyncio.sleep(1.2)
    stop_event.set()
    await loop_task

    await db_session.refresh(task)
    assert task.lease_expires_at is not None
    expiry = (
        task.lease_expires_at
        if task.lease_expires_at.tzinfo
        else task.lease_expires_at.replace(tzinfo=UTC)
    )
    assert expiry > utc_now() + timedelta(seconds=200)


@pytest.mark.asyncio
async def test_run_once_handles_chunk_failure_and_cancellation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_company: Company,
) -> None:
    """Verify worker.run_once handles CANCELLED and FAILED chunk commit statuses."""
    profile = PreferenceProfile(name="Cancel Profile", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    job = await _create_test_job(db_session, seeded_company.id)

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=job.id,
        last_processed_job_id=0,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)

    # 1. CANCELLED
    async def mock_cancelled(*args: Any, **kwargs: Any) -> tuple[ChunkCommitStatus, int, int, int]:
        return ChunkCommitStatus.CANCELLED, 0, 0, 1

    worker._process_chunk = mock_cancelled  # type: ignore
    res1 = await worker.run_once()
    assert res1.tasks_claimed == 1
    assert res1.tasks_completed == 0

    # 2. FAILED
    # Reset task to PENDING
    await db_session.execute(
        update(ProfileReevaluationTask)
        .where(ProfileReevaluationTask.id == task.id)
        .values(status=ReevaluationStatus.PENDING, lease_token=None)
    )
    await db_session.commit()

    async def mock_failed(*args: Any, **kwargs: Any) -> tuple[ChunkCommitStatus, int, int, int]:
        return ChunkCommitStatus.FAILED, 0, 0, 1

    worker._process_chunk = mock_failed  # type: ignore
    res2 = await worker.run_once()
    assert res2.tasks_claimed == 1
    assert res2.tasks_failed == 1


@pytest.mark.asyncio
async def test_process_chunk_direct_cancellation_and_supersession(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify _process_chunk cancels task if profile deactivated and supersedes on rev mismatch."""
    profile = PreferenceProfile(name="Direct Check", revision=1, is_active=False)
    db_session.add(profile)
    await db_session.commit()

    task1 = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="tok1",
    )
    db_session.add(task1)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed1 = ClaimedReevaluationTask(
        id=task1.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        last_processed_job_id=0,
        lease_token="tok1",
        attempt_count=1,
    )

    # 1. Profile is inactive -> CANCELLED
    status1, _, _, _ = await worker._process_chunk(claimed1)
    assert status1 == ChunkCommitStatus.CANCELLED
    await db_session.refresh(task1)
    assert task1.status == ReevaluationStatus.CANCELLED

    # 2. Profile revision mismatch -> SUPERSEDED
    profile.is_active = True
    profile.revision = 2
    task2 = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,  # Mismatched old rev
        max_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="tok2",
    )
    db_session.add(task2)
    await db_session.commit()

    claimed2 = ClaimedReevaluationTask(
        id=task2.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=10,
        last_processed_job_id=0,
        lease_token="tok2",
        attempt_count=1,
    )
    status2, _, _, _ = await worker._process_chunk(claimed2)
    assert status2 == ChunkCommitStatus.SUPERSEDED
    await db_session.refresh(task2)
    assert task2.status == ReevaluationStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_process_chunk_returns_no_more_jobs_when_range_is_empty(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify _process_chunk returns NO_MORE_JOBS if no jobs exist between cursor and max_job_id."""
    profile = PreferenceProfile(name="Empty Range", revision=1, is_active=True)
    db_session.add(profile)
    await db_session.commit()

    task = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=50,
        last_processed_job_id=10,
        status=ReevaluationStatus.RUNNING,
        lease_token="tok-gap",
    )
    db_session.add(task)
    await db_session.commit()

    worker = ProfileReevaluationWorker(session_factory=session_factory)
    claimed = ClaimedReevaluationTask(
        id=task.id,
        profile_id=profile.id,
        profile_revision=1,
        max_job_id=50,
        last_processed_job_id=10,
        lease_token="tok-gap",
        attempt_count=1,
    )

    status, final_id, _, _ = await worker._process_chunk(claimed)
    assert status == ChunkCommitStatus.NO_MORE_JOBS
    assert final_id == 50
