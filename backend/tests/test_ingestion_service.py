"""Tests for career site IngestionService and database persistence."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.company_repo import CompanyRepository
from app.repositories.job_repo import JobRepository
from app.repositories.log_repo import MonitoringLogRepository
from app.schemas.company import CompanyCreate
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.exceptions import NotFoundError, RateLimitError
from app.scrapers.registry import ScraperRegistry
from app.services.ingestion_service import IngestionResult, IngestionService


class MockTestAdapter(BaseScraperAdapter):
    """Mock adapter returning controlled test postings."""

    name = "mock_ats"

    def __init__(
        self,
        mock_postings: list[RawJobPosting] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.mock_postings = mock_postings or []
        self.error = error

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "mock_id"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        if self.error:
            raise self.error
        return self.mock_postings


@pytest.mark.asyncio
async def test_ingestion_service_initial_and_duplicate_runs(
    db_session: AsyncSession,
) -> None:
    """Verify initial ingestion creates jobs, while subsequent runs detect duplicates."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Acme Robotics",
            career_url="https://boards.greenhouse.io/acme",
            platform="mock_ats",
        )
    )

    sample_postings = [
        RawJobPosting(
            source_ats="mock_ats",
            external_id="1",
            title="Robotics Software Engineer",
            application_url="https://acme.com/jobs/1",
            location="Boston, MA",
            department="Autonomy",
            description="Write C++ and Python ROS nodes.",
            employment_type="Full-time",
            workplace_type="onsite",
            published_at=datetime(2026, 10, 15, tzinfo=UTC),
        ),
        RawJobPosting(
            source_ats="mock_ats",
            external_id="2",
            title="Perception Intern (Summer 2026)",
            application_url="https://acme.com/jobs/2",
            location="Remote",
            department="Vision",
            description="Computer vision research for class of 2026.",
            employment_type="Internship",
            workplace_type="remote",
            published_at=datetime(2026, 10, 16, tzinfo=UTC),
        ),
    ]

    mock_adapter = MockTestAdapter(mock_postings=sample_postings)
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)

    # 1. First Run: Expect both jobs to be newly inserted
    result1: IngestionResult = await service.ingest_company(company.id)

    assert result1.total_fetched == 2
    assert result1.newly_inserted == 2
    assert result1.already_existing == 0
    assert result1.updated_records == 0
    assert result1.failed_records == 0

    # Verify persisted in database
    jobs_in_db = await job_repo.list_by_company(company.id)
    assert len(jobs_in_db) == 2
    titles = {j.title for j in jobs_in_db}
    assert "Robotics Software Engineer" in titles
    assert "Perception Intern (Summer 2026)" in titles

    # Verify audit log was written
    log_repo = MonitoringLogRepository(db_session)
    logs = await log_repo.list_by_company(company.id)
    assert len(logs) == 1
    assert logs[0].status == "SUCCESS"
    assert logs[0].jobs_found == 2

    # 2. Second Run with identical postings: Expect 0 newly inserted, 2 already existing
    result2: IngestionResult = await service.ingest_company(company.id)

    assert result2.total_fetched == 2
    assert result2.newly_inserted == 0
    assert result2.already_existing == 2
    assert result2.updated_records == 0

    # Total in database must still be exactly 2 (no duplicates!)
    jobs_after_run2 = await job_repo.list_by_company(company.id)
    assert len(jobs_after_run2) == 2


@pytest.mark.asyncio
async def test_ingestion_service_detects_updated_content(
    db_session: AsyncSession,
) -> None:
    """Verify that when a posting's description is modified, its status becomes UPDATED."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Fintech Corp",
            career_url="https://fintech.com/jobs",
            platform="mock_ats",
        )
    )

    original_posting = RawJobPosting(
        source_ats="mock_ats",
        external_id="100",
        title="Payment Engineer",
        application_url="https://fintech.com/jobs/100",
        description="Version 1 requirements.",
    )

    mock_adapter = MockTestAdapter(mock_postings=[original_posting])
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)

    # Initial ingestion
    await service.ingest_company(company.id)
    db_job = (await job_repo.list_by_company(company.id))[0]
    assert db_job.status == "NEW"
    assert db_job.description == "Version 1 requirements."

    # Update posting with new description (compensation and benefits added)
    updated_posting = RawJobPosting(
        source_ats="mock_ats",
        external_id="100",
        title="Payment Engineer",
        application_url="https://fintech.com/jobs/100",
        description="Version 2 requirements with salary band $180k-$220k.",
    )
    mock_adapter.mock_postings = [updated_posting]

    result = await service.ingest_company(company.id)
    assert result.updated_records == 1
    assert result.newly_inserted == 0
    assert result.already_existing == 0

    # Check updated job in DB
    refreshed_job = await job_repo.get_by_id(db_job.id)
    assert refreshed_job is not None
    assert refreshed_job.status == "UPDATED"
    assert "Version 2 requirements" in (refreshed_job.description or "")


@pytest.mark.asyncio
async def test_ingestion_service_scraper_error_handling(
    db_session: AsyncSession,
) -> None:
    """Verify scraper exceptions are caught, logged, and returned gracefully in result."""
    comp_repo = CompanyRepository(db_session)
    log_repo = MonitoringLogRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Ghost Corp",
            career_url="https://boards.greenhouse.io/ghost_token",
            platform="mock_ats",
        )
    )

    mock_adapter = MockTestAdapter(error=NotFoundError("Target career board not found: 404"))
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)
    result = await service.ingest_company(company.id)

    assert result.failed_records == 1
    assert len(result.errors) == 1
    assert "Target career board not found" in result.errors[0]

    # Verify audit log recorded FAILED status
    logs = await log_repo.list_by_company(company.id)
    assert len(logs) == 1
    assert logs[0].status == "FAILED"
    assert "Target career board not found" in (logs[0].error_message or "")


@pytest.mark.asyncio
async def test_ingestion_service_nonexistent_company(db_session: AsyncSession) -> None:
    """Verify ingest_company raises ValueError for non-existent company ID."""
    service = IngestionService(db_session)
    with pytest.raises(ValueError, match="Company with ID 999999 does not exist"):
        await service.ingest_company(999999)


@pytest.mark.asyncio
async def test_ingestion_service_savepoint_isolation_and_partial_failure(
    db_session: AsyncSession,
) -> None:
    """Verify per-record savepoint isolation preserves valid jobs when one record fails."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    log_repo = MonitoringLogRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Reliable Corp",
            career_url="https://reliable.com/jobs",
            platform="mock_ats",
        )
    )

    # 3 postings: #1 valid, #2 triggers DB failure via forced duplicate fingerprint, #3 valid
    posting1 = RawJobPosting(
        source_ats="mock_ats",
        external_id="101",
        title="Valid Job 1",
        application_url="https://reliable.com/jobs/101",
    )
    posting2_bad = RawJobPosting(
        source_ats="mock_ats",
        external_id="102",
        title="Job 2 Bad",
        application_url="https://reliable.com/jobs/102",
    )
    posting3 = RawJobPosting(
        source_ats="mock_ats",
        external_id="103",
        title="Valid Job 3",
        application_url="https://reliable.com/jobs/103",
    )

    mock_adapter = MockTestAdapter(mock_postings=[posting1, posting2_bad, posting3])
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)

    # Monkeypatch job_repo.create so that posting2_bad raises an IntegrityError
    original_create = service.job_repo.create

    async def fail_on_second(schema):  # type: ignore
        if getattr(schema, "external_id", None) == "102":
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError(
                "Forced DB constraint failure", orig=Exception("DB error"), params={}
            )
        return await original_create(schema)

    service.job_repo.create = fail_on_second  # type: ignore[method-assign]

    result = await service.ingest_company(company.id)

    # Verify counts: 3 fetched, 2 inserted, 1 failed
    assert result.total_fetched == 3
    assert result.newly_inserted == 2
    assert result.failed_records == 1
    assert result.already_existing == 0

    # Verify valid records 1 and 3 are present in database
    jobs = await job_repo.list_by_company(company.id)
    assert len(jobs) == 2
    ext_ids = {j.external_id for j in jobs}
    assert "101" in ext_ids
    assert "103" in ext_ids
    assert "102" not in ext_ids

    # Verify monitoring log has PARTIAL status
    logs = await log_repo.list_by_company(company.id)
    assert len(logs) == 1
    assert logs[0].status == "PARTIAL"
    assert logs[0].jobs_found == 3
    assert "Forced DB constraint failure" in (logs[0].error_message or "")

    # Verify session remains healthy and usable for subsequent queries
    company_check = await comp_repo.get_by_id(company.id)
    assert company_check is not None


@pytest.mark.asyncio
async def test_ingestion_service_external_id_identity_deduplication(
    db_session: AsyncSession,
) -> None:
    """Verify external_id matching updates jobs when title, location, or URL changes."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Evolving Corp",
            career_url="https://evolving.com/jobs",
            platform="mock_ats",
        )
    )

    # 1. Initial insert
    posting = RawJobPosting(
        source_ats="mock_ats",
        external_id="ext-500",
        title="Backend Engineer",
        location="Austin, TX",
        application_url="https://evolving.com/jobs/500",
        description="Initial description",
    )
    mock_adapter = MockTestAdapter(mock_postings=[posting])
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)
    res1 = await service.ingest_company(company.id)
    assert res1.newly_inserted == 1

    jobs = await job_repo.list_by_company(company.id)
    assert len(jobs) == 1
    job_id = jobs[0].id

    # 2. Company updates title and location for the SAME external_id
    updated_title_loc = RawJobPosting(
        source_ats="mock_ats",
        external_id="ext-500",
        title="Staff Backend Engineer",
        location="Remote (US)",
        application_url="https://evolving.com/jobs/500",
        description="Initial description",
    )
    mock_adapter.mock_postings = [updated_title_loc]
    res2 = await service.ingest_company(company.id)

    # Must update the existing row, NOT create a duplicate!
    assert res2.newly_inserted == 0
    assert res2.updated_records == 1

    jobs_after_update = await job_repo.list_by_company(company.id)
    assert len(jobs_after_update) == 1
    assert jobs_after_update[0].id == job_id
    assert jobs_after_update[0].title == "Staff Backend Engineer"
    assert jobs_after_update[0].location == "Remote (US)"
    assert jobs_after_update[0].status == "UPDATED"

    # 3. Two different external_ids with identical title and location must NOT collide
    diff_ext_1 = RawJobPosting(
        source_ats="mock_ats",
        external_id="ext-601",
        title="Software Engineer",
        location="San Francisco, CA",
        application_url="https://evolving.com/jobs/apply",
    )
    diff_ext_2 = RawJobPosting(
        source_ats="mock_ats",
        external_id="ext-602",
        title="Software Engineer",
        location="San Francisco, CA",
        application_url="https://evolving.com/jobs/apply",
    )
    mock_adapter.mock_postings = [diff_ext_1, diff_ext_2]
    res3 = await service.ingest_company(company.id)
    assert res3.newly_inserted == 2

    all_jobs = await job_repo.list_by_company(company.id)
    assert len(all_jobs) == 3


@pytest.mark.asyncio
async def test_ingestion_service_metadata_only_update(db_session: AsyncSession) -> None:
    """Verify that metadata changes (department, employment_type) trigger an update."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Meta Corp",
            career_url="https://meta.com/jobs",
            platform="mock_ats",
        )
    )

    posting_v1 = RawJobPosting(
        source_ats="mock_ats",
        external_id="meta-1",
        title="Data Engineer",
        department="Analytics",
        employment_type="Contract",
        application_url="https://meta.com/jobs/1",
        description="Write SQL and ETL pipelines.",
    )
    mock_adapter = MockTestAdapter(mock_postings=[posting_v1])
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)
    await service.ingest_company(company.id)

    # Update only department and employment_type (description identical)
    posting_v2 = RawJobPosting(
        source_ats="mock_ats",
        external_id="meta-1",
        title="Data Engineer",
        department="Core Data Platform",
        employment_type="Full-time",
        application_url="https://meta.com/jobs/1",
        description="Write SQL and ETL pipelines.",
    )
    mock_adapter.mock_postings = [posting_v2]
    res = await service.ingest_company(company.id)

    assert res.updated_records == 1
    job = (await job_repo.list_by_company(company.id))[0]
    assert job.department == "Core Data Platform"
    assert job.employment_type == "full_time"
    assert job.status == "UPDATED"


@pytest.mark.asyncio
async def test_ingestion_service_rate_limited_audit_status(
    db_session: AsyncSession,
) -> None:
    """Verify that RateLimitError records RATE_LIMITED status in monitoring_logs."""
    comp_repo = CompanyRepository(db_session)
    log_repo = MonitoringLogRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Rate Limited Corp",
            career_url="https://limited.com/jobs",
            platform="mock_ats",
        )
    )

    mock_adapter = MockTestAdapter(error=RateLimitError("Too Many Requests: 429", retry_after=30.0))
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)
    result = await service.ingest_company(company.id)

    assert result.failed_records == 1
    assert "Too Many Requests" in result.errors[0]

    logs = await log_repo.list_by_company(company.id)
    assert len(logs) == 1
    assert logs[0].status == "RATE_LIMITED"
    assert "Too Many Requests" in (logs[0].error_message or "")


@pytest.mark.asyncio
async def test_ingestion_service_fingerprint_transition_to_external_id(
    db_session: AsyncSession,
) -> None:
    """Verify job ingested without external_id transitions cleanly when external_id added."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Transition Corp",
            career_url="https://transition.com/jobs",
            platform="mock_ats",
        )
    )

    # 1. Job first ingested without external_id
    posting_v1 = RawJobPosting(
        source_ats="mock_ats",
        external_id=None,
        title="DevOps Engineer",
        location="Austin, TX",
        application_url="https://transition.com/jobs/devops",
        description="Kubernetes, Terraform, CI/CD.",
    )
    mock_adapter = MockTestAdapter(mock_postings=[posting_v1])
    custom_registry = ScraperRegistry()
    custom_registry.register("mock_ats", lambda **kwargs: mock_adapter)

    service = IngestionService(db_session, registry=custom_registry)
    res1 = await service.ingest_company(company.id)
    assert res1.newly_inserted == 1

    jobs_phase1 = await job_repo.list_by_company(company.id)
    assert len(jobs_phase1) == 1
    original_id = jobs_phase1[0].id
    assert jobs_phase1[0].external_id is None

    # 2. Same job later received WITH external_id
    posting_v2 = RawJobPosting(
        source_ats="mock_ats",
        external_id="gh-devops-999",
        title="DevOps Engineer",
        location="Austin, TX",
        application_url="https://transition.com/jobs/devops",
        description="Kubernetes, Terraform, CI/CD.",
    )
    mock_adapter.mock_postings = [posting_v2]

    # 3. Existing job is updated rather than duplicated
    res2 = await service.ingest_company(company.id)
    assert res2.newly_inserted == 0
    assert res2.updated_records == 1

    jobs_phase2 = await job_repo.list_by_company(company.id)
    assert len(jobs_phase2) == 1
    assert jobs_phase2[0].id == original_id
    assert jobs_phase2[0].external_id == "gh-devops-999"
    assert jobs_phase2[0].status == "UPDATED"

    # 4. Subsequent scans match the same row via external_id
    res3 = await service.ingest_company(company.id)
    assert res3.newly_inserted == 0
    assert res3.already_existing == 1
    assert res3.updated_records == 0

    jobs_phase3 = await job_repo.list_by_company(company.id)
    assert len(jobs_phase3) == 1
    assert jobs_phase3[0].id == original_id
    assert jobs_phase3[0].external_id == "gh-devops-999"
