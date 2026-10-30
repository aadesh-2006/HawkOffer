"""Tests for M5 Phase 3 models:

JobDiscoveryOutbox, NotificationBacklog, ProfileReevaluationTask, SystemDrainState.
"""

import pytest
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base
from app.models.company import Company
from app.models.job import Job
from app.models.outbox import (
    BacklogStatus,
    JobDiscoveryOutbox,
    NotificationBacklog,
    OutboxStatus,
    ProfileReevaluationTask,
    ReevaluationStatus,
    SystemDrainState,
)
from app.models.preference import PreferenceProfile


def test_models_registered_in_metadata() -> None:
    """Verify that all Phase 3 models are registered in Base.metadata and accessible."""
    table_names = Base.metadata.tables.keys()
    assert "job_discovery_outbox" in table_names
    assert "notification_backlog" in table_names
    assert "profile_reevaluation_tasks" in table_names
    assert "system_drain_state" in table_names


def test_enum_definitions() -> None:
    """Verify enum members and values match architecture specification."""
    assert OutboxStatus.PENDING == "PENDING"
    assert OutboxStatus.PROCESSING == "PROCESSING"
    assert OutboxStatus.PROCESSED == "PROCESSED"
    assert OutboxStatus.FAILED == "FAILED"

    assert BacklogStatus.QUEUED == "QUEUED"
    assert BacklogStatus.PROCESSING == "PROCESSING"
    assert BacklogStatus.PROCESSED == "PROCESSED"
    assert BacklogStatus.STALE == "STALE"
    assert BacklogStatus.DROPPED == "DROPPED"

    assert ReevaluationStatus.PENDING == "PENDING"
    assert ReevaluationStatus.RUNNING == "RUNNING"
    assert ReevaluationStatus.COMPLETED == "COMPLETED"
    assert ReevaluationStatus.SUPERSEDED == "SUPERSEDED"
    assert ReevaluationStatus.CANCELLED == "CANCELLED"
    assert ReevaluationStatus.FAILED == "FAILED"


def test_model_indexes_and_constraints_metadata() -> None:
    """Verify table arguments, unique constraints, and partial index definitions."""
    from typing import cast

    from sqlalchemy import Table

    # JobDiscoveryOutbox constraints
    outbox_table = cast(Table, JobDiscoveryOutbox.__table__)
    assert outbox_table.c.job_id.unique is True
    outbox_indexes = {str(idx.name): idx for idx in outbox_table.indexes}
    assert "ix_outbox_claim_pending" in outbox_indexes
    assert "ix_outbox_claim_expired" in outbox_indexes

    # NotificationBacklog constraints
    backlog_table = cast(Table, NotificationBacklog.__table__)
    backlog_indexes = {str(idx.name): idx for idx in backlog_table.indexes}
    assert "ix_backlog_queued_relevance" in backlog_indexes
    assert "ix_backlog_queued_fifo" in backlog_indexes
    assert "ix_backlog_processing_relevance" in backlog_indexes
    assert "ix_backlog_processing_fifo" in backlog_indexes

    # Check unique constraint (job_id, profile_id, profile_revision)
    uq_backlog = [
        c
        for c in backlog_table.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_backlog_job_profile_rev"
    ]
    assert len(uq_backlog) == 1
    assert {col.name for col in uq_backlog[0].columns} == {
        "job_id",
        "profile_id",
        "profile_revision",
    }

    # ProfileReevaluationTask partial unique index
    task_table = cast(Table, ProfileReevaluationTask.__table__)
    task_indexes = {str(idx.name): idx for idx in task_table.indexes}
    assert "uq_active_task_per_profile" in task_indexes
    assert task_indexes["uq_active_task_per_profile"].unique is True


@pytest.mark.asyncio
async def test_preference_profile_revision_default_and_increment(db_session: AsyncSession) -> None:
    """Verify PreferenceProfile revision column defaults to 1 and increments properly."""
    profile = PreferenceProfile(name="Revision Profile")
    db_session.add(profile)
    await db_session.commit()

    assert profile.id is not None
    assert profile.revision == 1

    profile.revision += 1
    await db_session.commit()
    await db_session.refresh(profile)
    assert profile.revision == 2


@pytest.mark.asyncio
async def test_job_discovery_outbox_crud_and_uniqueness(db_session: AsyncSession) -> None:
    """Verify JobDiscoveryOutbox creation, defaults, lease fields, and job_id uniqueness."""
    company = Company(name="Outbox Co", career_url="https://outbox.com")
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="Software Engineer",
        application_url="https://outbox.com/jobs/1",
        fingerprint_hash="a" * 64,
        content_hash="b" * 64,
    )
    db_session.add(job)
    await db_session.commit()

    outbox = JobDiscoveryOutbox(
        job_id=job.id,
        status=OutboxStatus.PENDING,
    )
    db_session.add(outbox)
    await db_session.commit()

    assert outbox.id is not None
    assert outbox.status == OutboxStatus.PENDING
    assert outbox.attempt_count == 0
    assert outbox.lease_token is None
    assert outbox.lease_expires_at is None
    assert outbox.created_at is not None

    # Relationship navigation
    assert outbox.job.title == "Software Engineer"

    # Verify duplicate job_id violates unique constraint
    dup_outbox = JobDiscoveryOutbox(job_id=job.id, status=OutboxStatus.PENDING)
    db_session.add(dup_outbox)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_job_discovery_outbox_cascade_deletion(db_session: AsyncSession) -> None:
    """Verify cascading deletion of Job removes corresponding JobDiscoveryOutbox record."""
    company = Company(name="Cascade Co", career_url="https://cascade.com")
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="Platform Engineer",
        application_url="https://cascade.com/jobs/platform",
        fingerprint_hash="c" * 64,
        content_hash="d" * 64,
    )
    db_session.add(job)
    await db_session.flush()

    outbox = JobDiscoveryOutbox(job_id=job.id)
    db_session.add(outbox)
    await db_session.commit()

    # Delete Job
    await db_session.delete(job)
    await db_session.commit()

    # Verify outbox record was deleted via cascade
    res = await db_session.execute(
        select(JobDiscoveryOutbox).where(JobDiscoveryOutbox.id == outbox.id)
    )
    assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_notification_backlog_crud_and_uniqueness(db_session: AsyncSession) -> None:
    """Verify NotificationBacklog creation, candidate scoring, and revision uniqueness."""
    company = Company(name="Backlog Co", career_url="https://backlog.com")
    profile = PreferenceProfile(name="Candidate Profile")
    db_session.add_all([company, profile])
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="Backend Engineer",
        application_url="https://backlog.com/jobs/backend",
        fingerprint_hash="e" * 64,
        content_hash="f" * 64,
    )
    db_session.add(job)
    await db_session.commit()

    backlog = NotificationBacklog(
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        score=85,
        status=BacklogStatus.QUEUED,
    )
    db_session.add(backlog)
    await db_session.commit()

    assert backlog.id is not None
    assert backlog.score == 85
    assert backlog.status == BacklogStatus.QUEUED
    assert backlog.attempt_count == 0
    assert backlog.created_at is not None

    # Relationship navigation
    assert backlog.job.title == "Backend Engineer"
    assert backlog.profile.name == "Candidate Profile"

    # Duplicate insertion for same (job_id, profile_id, profile_revision) must fail
    dup_backlog = NotificationBacklog(
        job_id=job.id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        score=90,
    )
    db_session.add(dup_backlog)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # Re-fetch profile and job IDs for next insertion after rollback
    res_prof = await db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.name == "Candidate Profile")
    )
    fresh_profile = res_prof.scalar_one()

    res_job = await db_session.execute(select(Job).where(Job.title == "Backend Engineer"))
    fresh_job = res_job.scalar_one()

    # Different profile revision is allowed
    new_rev_backlog = NotificationBacklog(
        job_id=fresh_job.id,
        profile_id=fresh_profile.id,
        profile_revision=fresh_profile.revision + 1,
        score=90,
    )
    db_session.add(new_rev_backlog)
    await db_session.commit()
    assert new_rev_backlog.id is not None


@pytest.mark.asyncio
async def test_notification_backlog_cascade_deletions(db_session: AsyncSession) -> None:
    """Verify NotificationBacklog rows cascade-delete with Job or PreferenceProfile."""
    company = Company(name="Backlog Cascade Co", career_url="https://backlog-cascade.com")
    profile = PreferenceProfile(name="Backlog User")
    db_session.add_all([company, profile])
    await db_session.flush()

    job1 = Job(
        company_id=company.id,
        title="Job 1",
        application_url="https://backlog-cascade.com/1",
        fingerprint_hash="11" * 32,
        content_hash="12" * 32,
    )
    job2 = Job(
        company_id=company.id,
        title="Job 2",
        application_url="https://backlog-cascade.com/2",
        fingerprint_hash="21" * 32,
        content_hash="22" * 32,
    )
    db_session.add_all([job1, job2])
    await db_session.flush()

    b1 = NotificationBacklog(job_id=job1.id, profile_id=profile.id, profile_revision=1, score=80)
    b2 = NotificationBacklog(job_id=job2.id, profile_id=profile.id, profile_revision=1, score=70)
    db_session.add_all([b1, b2])
    await db_session.commit()

    # Deleting job1 cascade-deletes b1
    await db_session.delete(job1)
    await db_session.commit()
    res = await db_session.execute(
        select(NotificationBacklog).where(NotificationBacklog.id == b1.id)
    )
    assert res.scalar_one_or_none() is None

    # Deleting profile cascade-deletes b2
    await db_session.delete(profile)
    await db_session.commit()
    res = await db_session.execute(
        select(NotificationBacklog).where(NotificationBacklog.id == b2.id)
    )
    assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_profile_reevaluation_task_crud_and_partial_unique(db_session: AsyncSession) -> None:
    """Verify ProfileReevaluationTask creation and partial unique index on active states."""
    profile = PreferenceProfile(name="Re-eval Profile")
    db_session.add(profile)
    await db_session.commit()

    task1 = ProfileReevaluationTask(
        profile_id=profile.id,
        profile_revision=profile.revision,
        max_job_id=100,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task1)
    await db_session.commit()

    assert task1.id is not None
    assert task1.last_processed_job_id == 0
    assert task1.attempt_count == 0
    assert task1.profile_id == profile.id
    saved_task1_id = task1.id
    saved_profile_id = profile.id
    saved_revision = profile.revision

    # Attempting to add another active (PENDING or RUNNING) task for same profile
    # must violate partial index
    task2 = ProfileReevaluationTask(
        profile_id=saved_profile_id,
        profile_revision=saved_revision,
        max_job_id=150,
        status=ReevaluationStatus.RUNNING,
    )
    db_session.add(task2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # Re-fetch fresh profile
    res_prof = await db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.id == saved_profile_id)
    )
    fresh_profile = res_prof.scalar_one()

    # Transition task1 to COMPLETED via direct update statement
    from sqlalchemy import update

    await db_session.execute(
        update(ProfileReevaluationTask)
        .where(ProfileReevaluationTask.id == saved_task1_id)
        .values(status=ReevaluationStatus.COMPLETED)
    )
    await db_session.commit()

    task3 = ProfileReevaluationTask(
        profile_id=fresh_profile.id,
        profile_revision=fresh_profile.revision + 1,
        max_job_id=200,
        status=ReevaluationStatus.PENDING,
    )
    db_session.add(task3)
    await db_session.commit()
    assert task3.id is not None


@pytest.mark.asyncio
async def test_system_drain_state_singleton_get_or_create(db_session: AsyncSession) -> None:
    """Verify SystemDrainState singleton initialization strategy via get_or_create."""
    # First invocation initializes row with id=1
    state1 = await SystemDrainState.get_or_create(db_session)
    await db_session.commit()

    assert state1.id == 1
    assert state1.drain_counter == 0
    assert state1.updated_at is not None

    # Advance drain_counter
    state1.drain_counter = 4
    await db_session.commit()

    # Second invocation retrieves existing singleton row without resetting counter
    state2 = await SystemDrainState.get_or_create(db_session)
    assert state2.id == 1
    assert state2.drain_counter == 4
