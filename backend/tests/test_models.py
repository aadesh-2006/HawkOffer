"""Tests for SQLAlchemy ORM models, relationships, and integrity constraints."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.company import Company
from app.models.job import Job
from app.models.log import MonitoringLog
from app.models.notification import Notification
from app.models.preference import PreferenceProfile


@pytest.mark.asyncio
async def test_company_creation(db_session: AsyncSession) -> None:
    """Test creating a company with default and explicit fields."""
    company = Company(
        name="Acme Corp",
        career_url="https://acme.com/jobs",
        platform="greenhouse",
    )
    db_session.add(company)
    await db_session.commit()

    assert company.id is not None
    assert company.name == "Acme Corp"
    assert company.is_active is True
    assert company.created_at is not None
    assert company.updated_at is not None


@pytest.mark.asyncio
async def test_job_creation_and_relationship(db_session: AsyncSession) -> None:
    """Test job creation linked to a company and verifying relationship navigation."""
    company = Company(
        name="Stark Tech",
        career_url="https://stark.com/careers",
        platform="lever",
    )
    db_session.add(company)
    await db_session.commit()

    job = Job(
        company_id=company.id,
        title="Software Engineer Intern",
        location="New York, NY",
        description="Write high performance python code.",
        application_url="https://stark.com/careers/swe-intern",
        employment_type="internship",
        job_type="hybrid",
        graduation_year=2026,
        fingerprint_hash="a" * 64,
        content_hash="b" * 64,
        status="NEW",
    )
    db_session.add(job)
    await db_session.commit()

    assert job.id is not None
    assert job.company.name == "Stark Tech"

    # Query company jobs relationship
    result = await db_session.execute(select(Company).where(Company.id == company.id))
    fetched_company = result.scalars().first()
    assert fetched_company is not None
    assert len(fetched_company.jobs) == 1
    assert fetched_company.jobs[0].title == "Software Engineer Intern"


@pytest.mark.asyncio
async def test_job_unique_fingerprint_constraint(db_session: AsyncSession) -> None:
    """Verify that duplicate job fingerprint_hash raises IntegrityError."""
    company = Company(name="Test Co", career_url="https://test.co/jobs")
    db_session.add(company)
    await db_session.commit()

    same_hash = "f" * 64
    job1 = Job(
        company_id=company.id,
        title="Role 1",
        application_url="https://test.co/job1",
        fingerprint_hash=same_hash,
        content_hash="c" * 64,
    )
    db_session.add(job1)
    await db_session.commit()

    job2 = Job(
        company_id=company.id,
        title="Role 2 (Duplicate Hash)",
        application_url="https://test.co/job2",
        fingerprint_hash=same_hash,
        content_hash="d" * 64,
    )
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_job_unique_company_external_id_constraint(
    db_session: AsyncSession,
) -> None:
    """Verify duplicate (company_id, external_id) raises IntegrityError, while nulls are allowed."""
    company = Company(name="Acme Corp", career_url="https://acme.com/jobs")
    db_session.add(company)
    await db_session.commit()
    company_id = company.id

    job1 = Job(
        company_id=company_id,
        external_id="gh-1234",
        title="Engineer 1",
        application_url="https://acme.com/job1",
        fingerprint_hash="1" * 64,
        content_hash="c" * 64,
    )
    db_session.add(job1)
    await db_session.commit()

    # Duplicate (company_id, external_id) must raise IntegrityError
    job2 = Job(
        company_id=company_id,
        external_id="gh-1234",
        title="Engineer 2",
        application_url="https://acme.com/job2",
        fingerprint_hash="2" * 64,
        content_hash="c" * 64,
    )
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Same external_id under a DIFFERENT company is allowed
    company2 = Company(name="Beta Corp", career_url="https://beta.com/jobs")
    db_session.add(company2)
    await db_session.commit()
    company2_id = company2.id

    job_comp2 = Job(
        company_id=company2_id,
        external_id="gh-1234",
        title="Engineer at Beta",
        application_url="https://beta.com/job1",
        fingerprint_hash="3" * 64,
        content_hash="c" * 64,
    )
    db_session.add(job_comp2)
    await db_session.commit()

    # Multiple jobs with external_id=None under the same company are allowed
    null_job1 = Job(
        company_id=company_id,
        external_id=None,
        title="Null Ext Job 1",
        application_url="https://acme.com/null1",
        fingerprint_hash="4" * 64,
        content_hash="c" * 64,
    )
    null_job2 = Job(
        company_id=company_id,
        external_id=None,
        title="Null Ext Job 2",
        application_url="https://acme.com/null2",
        fingerprint_hash="5" * 64,
        content_hash="c" * 64,
    )
    db_session.add(null_job1)
    db_session.add(null_job2)
    await db_session.commit()


@pytest.mark.asyncio
async def test_foreign_key_enforcement(db_session: AsyncSession) -> None:
    """Verify that referencing non-existent parent foreign key raises IntegrityError."""
    orphaned_job = Job(
        company_id=999999,  # Does not exist
        title="Ghost Role",
        application_url="https://ghost.com/jobs/1",
        fingerprint_hash="1" * 64,
        content_hash="2" * 64,
    )
    db_session.add(orphaned_job)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_cascade_deletion(db_session: AsyncSession) -> None:
    """Verify deleting a company cascades to jobs and monitoring logs."""
    company = Company(name="Cascade Co", career_url="https://cascade.co")
    db_session.add(company)
    await db_session.commit()

    job = Job(
        company_id=company.id,
        title="Frontend Engineer",
        application_url="https://cascade.co/jobs/fe",
        fingerprint_hash="9" * 64,
        content_hash="8" * 64,
    )
    log = MonitoringLog(
        company_id=company.id,
        status="SUCCESS",
        jobs_found=1,
        execution_time_ms=120,
    )
    db_session.add_all([job, log])
    await db_session.commit()

    # Delete company
    await db_session.delete(company)
    await db_session.commit()

    # Verify jobs and logs are deleted
    job_check = await db_session.execute(select(Job).where(Job.company_id == company.id))
    assert job_check.scalars().first() is None

    log_check = await db_session.execute(
        select(MonitoringLog).where(MonitoringLog.company_id == company.id)
    )
    assert log_check.scalars().first() is None


@pytest.mark.asyncio
async def test_preference_profile_json_fields(db_session: AsyncSession) -> None:
    """Verify PreferenceProfile stores and serializes JSON list fields correctly."""
    profile = PreferenceProfile(
        name="Internship Hunter",
        graduation_year=2026,
        preferred_roles=["Software Engineer", "Backend Developer", "ML Intern"],
        preferred_locations=["Remote", "San Francisco, CA"],
        preferred_skills=["Python", "FastAPI", "Docker"],
        minimum_match_score=75.0,
        is_active=True,
    )
    db_session.add(profile)
    await db_session.commit()

    assert profile.id is not None

    # Fetch fresh
    res = await db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.id == profile.id)
    )
    fetched = res.scalars().first()
    assert fetched is not None
    assert fetched.preferred_roles == ["Software Engineer", "Backend Developer", "ML Intern"]
    assert fetched.preferred_locations == ["Remote", "San Francisco, CA"]
    assert fetched.preferred_skills == ["Python", "FastAPI", "Docker"]
    assert fetched.minimum_match_score == 75.0


@pytest.mark.asyncio
async def test_notification_and_log_models(db_session: AsyncSession) -> None:
    """Test Notification and MonitoringLog entities."""
    company = Company(name="LogTest Co", career_url="https://logtest.com")
    db_session.add(company)
    await db_session.commit()

    job = Job(
        company_id=company.id,
        title="Data Analyst",
        application_url="https://logtest.com/da",
        fingerprint_hash="7" * 64,
        content_hash="6" * 64,
    )
    db_session.add(job)
    await db_session.commit()

    notif = Notification(
        job_id=job.id,
        notification_type="desktop_toast",
        delivery_status="PENDING",
    )
    log = MonitoringLog(
        company_id=company.id,
        status="SUCCESS",
        jobs_found=5,
        execution_time_ms=350,
    )
    db_session.add_all([notif, log])
    await db_session.commit()

    assert notif.id is not None
    assert notif.status == "UNREAD"
    assert notif.delivery_status == "PENDING"
    assert log.id is not None
    assert log.status == "SUCCESS"
    assert log.jobs_found == 5


@pytest.mark.asyncio
async def test_async_relationship_eager_loading_no_missing_greenlet(
    test_engine: AsyncEngine,
) -> None:
    """Regression test: verify lazy='selectin' prevents MissingGreenlet on relationship access."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        company = Company(name="Async Corp", career_url="https://async.com")
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            title="Backend Architect",
            application_url="https://async.com/jobs/1",
            fingerprint_hash="e" * 64,
            content_hash="e" * 64,
        )
        session.add(job)
        await session.flush()

        notif = Notification(job_id=job.id, notification_type="desktop_toast")
        session.add(notif)
        await session.commit()

    # Query in a completely new, clean session
    async with session_factory() as fresh_session:
        res = await fresh_session.execute(select(Notification))
        fetched_notif = res.scalars().first()
        assert fetched_notif is not None

        # These relationship traversals must NOT raise MissingGreenlet!
        assert fetched_notif.job.title == "Backend Architect"
        assert fetched_notif.job.company.name == "Async Corp"


@pytest.mark.asyncio
async def test_notification_deduplication_populated_profile(db_session: AsyncSession) -> None:
    """Regression test: verify duplicate notifications with populated profile_id are blocked."""
    company = Company(name="Dedup Co", career_url="https://dedup.com")
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="ML Engineer",
        application_url="https://dedup.com/ml",
        fingerprint_hash="d1" * 32,
        content_hash="d2" * 32,
    )
    profile = PreferenceProfile(name="Profile A")
    db_session.add_all([job, profile])
    await db_session.flush()

    notif1 = Notification(
        job_id=job.id,
        profile_id=profile.id,
        notification_type="desktop_toast",
    )
    db_session.add(notif1)
    await db_session.commit()

    # Attempt inserting identical duplicate
    notif2 = Notification(
        job_id=job.id,
        profile_id=profile.id,
        notification_type="desktop_toast",
    )
    db_session.add(notif2)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_notification_allows_multiple_null_profile_notifications(
    db_session: AsyncSession,
) -> None:
    """Verify multiple notifications with NULL profile_id are permitted for the same job.

    This ensures that when multiple profiles alerting on the same job are deleted,
    their cascading ON DELETE SET NULL does not collide or raise an IntegrityError.
    """
    company = Company(name="NullProfile Co", career_url="https://nullprofile.com")
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="Systems Engineer",
        application_url="https://nullprofile.com/sys",
        fingerprint_hash="n1" * 32,
        content_hash="n2" * 32,
    )
    db_session.add(job)
    await db_session.flush()

    notif1 = Notification(
        job_id=job.id,
        profile_id=None,
        notification_type="desktop_toast",
    )
    notif2 = Notification(
        job_id=job.id,
        profile_id=None,
        notification_type="desktop_toast",
    )
    db_session.add_all([notif1, notif2])
    await db_session.commit()

    assert notif1.id is not None
    assert notif2.id is not None
    assert notif1.id != notif2.id


@pytest.mark.asyncio
async def test_notification_allows_different_types_and_profiles(db_session: AsyncSession) -> None:
    """Verify distinct notification types and profiles for same job are allowed."""
    company = Company(name="MultiNotif Co", career_url="https://multinotif.com")
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="Staff Engineer",
        application_url="https://multinotif.com/staff",
        fingerprint_hash="m1" * 32,
        content_hash="m2" * 32,
    )
    profile = PreferenceProfile(name="Profile B")
    db_session.add_all([job, profile])
    await db_session.flush()

    notif_toast_null = Notification(
        job_id=job.id, profile_id=None, notification_type="desktop_toast"
    )
    notif_email_null = Notification(job_id=job.id, profile_id=None, notification_type="email")
    notif_toast_profile = Notification(
        job_id=job.id, profile_id=profile.id, notification_type="desktop_toast"
    )

    db_session.add_all([notif_toast_null, notif_email_null, notif_toast_profile])
    await db_session.commit()

    assert notif_toast_null.id is not None
    assert notif_email_null.id is not None
    assert notif_toast_profile.id is not None


@pytest.mark.asyncio
async def test_preference_profile_deletion_preserves_notifications(
    db_session: AsyncSession,
) -> None:
    """Regression test: deleting a profile nullifies profile_id and preserves notifications."""
    company = Company(name="Preserve Co", career_url="https://preserve.com")
    profile = PreferenceProfile(name="Disposable Profile")
    db_session.add_all([company, profile])
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="DevOps Engineer",
        application_url="https://preserve.com/devops",
        fingerprint_hash="p1" * 32,
        content_hash="p2" * 32,
    )
    db_session.add(job)
    await db_session.flush()

    notif = Notification(
        job_id=job.id,
        profile_id=profile.id,
        notification_type="desktop_toast",
        delivery_status="DELIVERED",
    )
    db_session.add(notif)
    await db_session.commit()

    # Delete the profile
    await db_session.delete(profile)
    await db_session.commit()

    # Refresh notification instance from database
    await db_session.refresh(notif)
    assert notif.profile_id is None
    assert notif.delivery_status == "DELIVERED"
    assert notif.status == "UNREAD"
