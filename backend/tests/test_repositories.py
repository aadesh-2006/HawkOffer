"""Tests for asynchronous Repository layer implementations."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.company_repo import CompanyRepository
from app.repositories.job_repo import JobRepository
from app.repositories.log_repo import MonitoringLogRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.preference_repo import PreferenceProfileRepository
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.schemas.job import JobCreate, JobFilterParams
from app.schemas.log import MonitoringLogCreate
from app.schemas.notification import NotificationCreate
from app.schemas.preference import PreferenceProfileCreate, PreferenceProfileUpdate


@pytest.mark.asyncio
async def test_company_repository_crud(db_session: AsyncSession) -> None:
    """Test CompanyRepository create, read, update, list_active, and delete."""
    repo = CompanyRepository(db_session)

    # Create
    created = await repo.create(
        CompanyCreate(
            name="Alpha Corp",
            career_url="https://alpha.com/careers",
            platform="greenhouse",
            is_active=True,
        )
    )
    assert created.id is not None
    assert created.name == "Alpha Corp"

    # Get by ID (uses session.get)
    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.name == "Alpha Corp"

    # Get by Name
    by_name = await repo.get_by_name("Alpha Corp")
    assert by_name is not None
    assert by_name.id == created.id

    # Get by Career URL
    by_url = await repo.get_by_career_url("https://alpha.com/careers")
    assert by_url is not None
    assert by_url.id == created.id

    # List active
    active_companies = await repo.list_active()
    assert len(active_companies) == 1
    assert active_companies[0].id == created.id

    # Update
    updated = await repo.update(created.id, CompanyUpdate(name="Alpha Global", is_active=False))
    assert updated is not None
    assert updated.name == "Alpha Global"
    assert updated.is_active is False

    # List active after deactivation
    active_now = await repo.list_active()
    assert len(active_now) == 0

    # Delete
    deleted = await repo.delete(created.id)
    assert deleted is True
    assert await repo.get_by_id(created.id) is None


@pytest.mark.asyncio
async def test_job_repository_lookups_and_filtering(db_session: AsyncSession) -> None:
    """Test JobRepository lookups by fingerprint, URL, status updates, and filters."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(name="Beta Labs", career_url="https://beta.com/jobs")
    )

    fingerprint1 = "1" * 64
    app_url1 = "https://beta.com/jobs/dev-1"
    job1 = await job_repo.create(
        JobCreate(
            company_id=company.id,
            title="Backend Engineer",
            location="Remote",
            external_id="ext-beta-1",
            application_url=app_url1,
            employment_type="full_time",
            job_type="remote",
            graduation_year=2025,
            fingerprint_hash=fingerprint1,
            content_hash="c1" * 32,
            status="NEW",
        )
    )

    await job_repo.create(
        JobCreate(
            company_id=company.id,
            title="Frontend Intern",
            location="Seattle, WA",
            application_url="https://beta.com/jobs/intern-1",
            employment_type="internship",
            job_type="onsite",
            graduation_year=2026,
            fingerprint_hash="2" * 64,
            content_hash="c2" * 32,
            status="NEW",
        )
    )

    # Lookup by fingerprint
    found_by_fp = await job_repo.get_by_fingerprint(fingerprint1)
    assert found_by_fp is not None
    assert found_by_fp.id == job1.id
    assert found_by_fp.title == "Backend Engineer"

    # Lookup by application URL
    found_by_url = await job_repo.get_by_application_url(app_url1)
    assert found_by_url is not None
    assert found_by_url.id == job1.id

    # Lookup by external ID
    found_by_ext = await job_repo.get_by_external_id(company.id, "ext-beta-1")
    assert found_by_ext is not None
    assert found_by_ext.id == job1.id
    assert await job_repo.get_by_external_id(company.id, "nonexistent") is None

    # Lookup non-existent
    assert await job_repo.get_by_fingerprint("nonexistent" * 4) is None
    assert await job_repo.get_by_application_url("https://unknown.com") is None

    # List by company
    company_jobs = await job_repo.list_by_company(company.id)
    assert len(company_jobs) == 2

    # Filter by job_type
    remote_jobs = await job_repo.filter_jobs(JobFilterParams(job_type="remote"))
    assert len(remote_jobs) == 1
    assert remote_jobs[0].title == "Backend Engineer"

    # Filter by search string
    searched_jobs = await job_repo.filter_jobs(JobFilterParams(search="Frontend"))
    assert len(searched_jobs) == 1
    assert searched_jobs[0].title == "Frontend Intern"

    # Update job status
    updated_job = await job_repo.update_status(job1.id, "ACTIVE")
    assert updated_job is not None
    assert updated_job.status == "ACTIVE"
    assert updated_job.last_seen_at is not None


@pytest.mark.asyncio
async def test_job_search_wildcard_escaping(db_session: AsyncSession) -> None:
    """Verify literal % and _ characters are properly escaped in filter searches."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(name="Escape Labs", career_url="https://escapelabs.com")
    )

    await job_repo.create(
        JobCreate(
            company_id=company.id,
            title="100% Remote Developer",
            application_url="https://escapelabs.com/job/1",
            fingerprint_hash="e1" * 32,
            content_hash="c1" * 32,
        )
    )
    await job_repo.create(
        JobCreate(
            company_id=company.id,
            title="1000 Remote Developer",
            application_url="https://escapelabs.com/job/2",
            fingerprint_hash="e2" * 32,
            content_hash="c2" * 32,
        )
    )

    # Search for literal "100%"
    results = await job_repo.filter_jobs(JobFilterParams(search="100%"))
    assert len(results) == 1
    assert results[0].title == "100% Remote Developer"


@pytest.mark.asyncio
async def test_preference_profile_repository(db_session: AsyncSession) -> None:
    """Test PreferenceProfileRepository CRUD and active profile retrieval."""
    repo = PreferenceProfileRepository(db_session)

    profile = await repo.create(
        PreferenceProfileCreate(
            name="Primary Profile",
            graduation_year=2026,
            preferred_roles=["AI Engineer", "Software Engineer"],
            preferred_locations=["Remote", "San Jose, CA"],
            preferred_skills=["Python", "PyTorch"],
            minimum_match_score=80.0,
            is_active=True,
        )
    )

    active_profile = await repo.get_active_profile()
    assert active_profile is not None
    assert active_profile.id == profile.id
    assert active_profile.preferred_roles == ["AI Engineer", "Software Engineer"]

    # Update profile
    updated = await repo.update(
        profile.id,
        PreferenceProfileUpdate(minimum_match_score=85.0),
    )
    assert updated is not None
    assert updated.minimum_match_score == 85.0


@pytest.mark.asyncio
async def test_notification_repository(db_session: AsyncSession) -> None:
    """Test NotificationRepository list_pending and mark_delivered."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    company = await comp_repo.create(CompanyCreate(name="Alert Co", career_url="https://alert.co"))
    job = await job_repo.create(
        JobCreate(
            company_id=company.id,
            title="Security Engineer",
            application_url="https://alert.co/sec",
            fingerprint_hash="3" * 64,
            content_hash="4" * 64,
        )
    )

    notif = await notif_repo.create(
        NotificationCreate(
            job_id=job.id,
            notification_type="desktop_toast",
            status="PENDING",
        )
    )
    assert notif.id is not None
    assert notif.status == "PENDING"

    # List pending
    pending = await notif_repo.list_pending()
    assert len(pending) == 1
    assert pending[0].id == notif.id

    # Mark delivered
    delivered = await notif_repo.mark_delivered(notif.id)
    assert delivered is not None
    assert delivered.status == "DELIVERED"
    assert delivered.delivered_at is not None

    # Pending should now be empty
    pending_after = await notif_repo.list_pending()
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_monitoring_log_repository(db_session: AsyncSession) -> None:
    """Test MonitoringLogRepository creation and querying."""
    comp_repo = CompanyRepository(db_session)
    log_repo = MonitoringLogRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(name="Metric Co", career_url="https://metric.co")
    )

    await log_repo.create(
        MonitoringLogCreate(
            company_id=company.id,
            status="SUCCESS",
            jobs_found=12,
            execution_time_ms=230,
        )
    )
    log2 = await log_repo.create(
        MonitoringLogCreate(
            company_id=company.id,
            status="RATE_LIMITED",
            jobs_found=0,
            error_message="HTTP 429 Too Many Requests",
            execution_time_ms=85,
        )
    )

    # List by company
    company_logs = await log_repo.list_by_company(company.id)
    assert len(company_logs) == 2
    # Verify ordered desc by created_at
    assert company_logs[0].id == log2.id

    # List recent
    recent_logs = await log_repo.list_recent(limit=10)
    assert len(recent_logs) >= 2


def test_company_schema_url_validation() -> None:
    """Verify CompanyBase validates career_url format."""
    from pydantic import ValidationError

    # Valid URL
    valid = CompanyCreate(name="Valid Co", career_url="https://valid.com/careers")
    assert valid.career_url == "https://valid.com/careers"

    # Invalid URL
    with pytest.raises(ValidationError):
        CompanyCreate(name="Invalid Co", career_url="ftp://invalid.com")

    with pytest.raises(ValidationError):
        CompanyCreate(name="Invalid Co", career_url="not-a-valid-url")
