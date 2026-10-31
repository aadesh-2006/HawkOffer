"""Tests for transactional JobDiscoveryOutbox integration with IngestionService."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.job import Job
from app.models.outbox import JobDiscoveryOutbox, OutboxStatus
from app.repositories.company_repo import CompanyRepository
from app.repositories.job_repo import JobRepository
from app.schemas.company import CompanyCreate
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.registry import ScraperRegistry
from app.services.ingestion_service import IngestionService


class OutboxTestAdapter(BaseScraperAdapter):
    """Controlled mock adapter for testing outbox ingestion workflows."""

    name = "outbox_mock"

    def __init__(self, postings: list[RawJobPosting]) -> None:
        super().__init__()
        self.postings = postings

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "outbox_id"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        return self.postings


@pytest.fixture
async def test_company(db_session: AsyncSession) -> Company:
    """Fixture providing a test company for ingestion tests."""
    comp_repo = CompanyRepository(db_session)
    company = await comp_repo.create(
        CompanyCreate(
            name="Outbox Integrated Corp",
            career_url="https://outbox-corp.com/careers",
            platform="outbox_mock",
        )
    )
    await db_session.commit()
    return company


@pytest.mark.asyncio
async def test_new_job_creates_atomic_pending_outbox_event(
    db_session: AsyncSession,
    test_company: Company,
) -> None:
    """Verify genuinely new jobs generate atomic PENDING outbox events with correct fields."""
    postings = [
        RawJobPosting(
            source_ats="outbox_mock",
            external_id="job-101",
            title="Senior Backend Engineer",
            application_url="https://outbox-corp.com/jobs/101",
            location="Remote",
            department="Core Infrastructure",
            description="Build robust async pipelines.",
            published_at=datetime(2026, 10, 31, tzinfo=UTC),
        ),
        RawJobPosting(
            source_ats="outbox_mock",
            external_id="job-102",
            title="Systems Intern (Summer 2027)",
            application_url="https://outbox-corp.com/jobs/102",
            location="New York, NY",
            department="Infrastructure",
            description="Internship opportunity for class of 2027.",
            published_at=datetime(2026, 10, 31, tzinfo=UTC),
        ),
    ]

    registry = ScraperRegistry()
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter(postings))
    service = IngestionService(db_session, registry=registry)

    result = await service.ingest_company(test_company.id)
    await db_session.commit()

    assert result.total_fetched == 2
    assert result.newly_inserted == 2
    assert result.failed_records == 0

    # Query database to confirm Job and Outbox records exist and are correctly linked
    job_repo = JobRepository(db_session)
    jobs = await job_repo.list_by_company(test_company.id)
    assert len(jobs) == 2

    job1 = next(j for j in jobs if j.external_id == "job-101")
    job2 = next(j for j in jobs if j.external_id == "job-102")

    outbox_res = await db_session.execute(
        select(JobDiscoveryOutbox).order_by(JobDiscoveryOutbox.id.asc())
    )
    outbox_entries = list(outbox_res.scalars().all())
    assert len(outbox_entries) == 2

    ob1 = next(o for o in outbox_entries if o.job_id == job1.id)
    assert ob1.status == OutboxStatus.PENDING
    assert ob1.attempt_count == 0
    assert ob1.lease_token is None
    assert ob1.lease_expires_at is None
    assert ob1.processed_at is None
    assert ob1.created_at is not None

    ob2 = next(o for o in outbox_entries if o.job_id == job2.id)
    assert ob2.status == OutboxStatus.PENDING
    assert ob2.attempt_count == 0


@pytest.mark.asyncio
async def test_existing_jobs_and_updates_do_not_create_duplicate_outbox_events(
    db_session: AsyncSession,
    test_company: Company,
) -> None:
    """Verify existing jobs matched by ID, fingerprint, or updates do not create outbox events."""
    posting1 = RawJobPosting(
        source_ats="outbox_mock",
        external_id="job-201",
        title="Frontend Specialist",
        application_url="https://outbox-corp.com/jobs/201",
        location="Austin, TX",
        description="Initial job description.",
    )

    registry = ScraperRegistry()
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter([posting1]))
    service = IngestionService(db_session, registry=registry)

    # Initial ingestion: creates job and outbox record
    res1 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res1.newly_inserted == 1

    outbox_count_before = (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()
    assert len(outbox_count_before) == 1

    # Second ingestion: Identical posting (external_id match, no change)
    res2 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res2.already_existing == 1
    assert res2.newly_inserted == 0

    outbox_count_after_dup = (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()
    assert len(outbox_count_after_dup) == 1

    # Third ingestion: Content update (description changed)
    posting1_updated = RawJobPosting(
        source_ats="outbox_mock",
        external_id="job-201",
        title="Frontend Specialist",
        application_url="https://outbox-corp.com/jobs/201",
        location="Austin, TX",
        description="Updated job description with new requirements.",
    )
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter([posting1_updated]))
    res3 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res3.updated_records == 1
    assert res3.newly_inserted == 0

    # Still strictly 1 outbox record
    outbox_count_after_update = (
        (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()
    )
    assert len(outbox_count_after_update) == 1


@pytest.mark.asyncio
async def test_fingerprint_fallback_does_not_create_outbox_event(
    db_session: AsyncSession,
    test_company: Company,
) -> None:
    """Verify job matched by fallback fingerprint does not create duplicate outbox event."""
    posting_no_ext = RawJobPosting(
        source_ats="outbox_mock",
        external_id=None,
        title="DevOps Lead",
        application_url="https://outbox-corp.com/jobs/devops",
        location="Chicago, IL",
        description="Kubernetes & Terraform.",
    )

    registry = ScraperRegistry()
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter([posting_no_ext]))
    service = IngestionService(db_session, registry=registry)

    res1 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res1.newly_inserted == 1

    # Ingest again with same content/url but still no external_id
    res2 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res2.already_existing == 1
    assert res2.newly_inserted == 0

    outbox_count = (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()
    assert len(outbox_count) == 1


@pytest.mark.asyncio
async def test_simulated_outbox_failure_rolls_back_job_atomically(
    db_session: AsyncSession,
    test_company: Company,
) -> None:
    """Verify outbox insertion failure rolls back the corresponding Job insertion in savepoint."""
    postings = [
        RawJobPosting(
            source_ats="outbox_mock",
            external_id="job-fail",
            title="Problematic Posting",
            application_url="https://outbox-corp.com/jobs/fail",
        ),
        RawJobPosting(
            source_ats="outbox_mock",
            external_id="job-success",
            title="Healthy Posting",
            application_url="https://outbox-corp.com/jobs/success",
        ),
    ]

    registry = ScraperRegistry()
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter(postings))
    service = IngestionService(db_session, registry=registry)

    original_execute = db_session.execute

    async def mock_execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        # Trigger an intentional error when inserting into job_discovery_outbox for job-fail
        if (
            hasattr(stmt, "table")
            and getattr(stmt.table, "name", None) == "job_discovery_outbox"
            and getattr(mock_execute, "failed_once", False) is False
        ):
            mock_execute.failed_once = True  # type: ignore[attr-defined]
            raise RuntimeError("Simulated database failure during outbox insert")
        return await original_execute(stmt, *args, **kwargs)

    mock_execute.failed_once = False  # type: ignore[attr-defined]

    with patch.object(db_session, "execute", side_effect=mock_execute):
        result = await service.ingest_company(test_company.id)

    await db_session.commit()

    # Verify ingestion stats: 1 failed record, 1 successful record
    assert result.total_fetched == 2
    assert result.failed_records == 1
    assert result.newly_inserted == 1
    assert len(result.errors) == 1
    assert "Simulated database failure during outbox insert" in result.errors[0]

    # Verify database state: job-fail was rolled back completely
    job_repo = JobRepository(db_session)
    failed_job = await job_repo.get_by_external_id(test_company.id, "job-fail")
    assert failed_job is None  # Job was rolled back!

    success_job = await job_repo.get_by_external_id(test_company.id, "job-success")
    assert success_job is not None

    outbox_entries = (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()
    assert len(outbox_entries) == 1
    assert outbox_entries[0].job_id == success_job.id


@pytest.mark.asyncio
async def test_repeated_ingestion_idempotency(
    db_session: AsyncSession,
    test_company: Company,
) -> None:
    """Verify repeated ingestion cycles maintain exact 1:1 mapping between jobs and outbox."""
    postings = [
        RawJobPosting(
            source_ats="outbox_mock",
            external_id=f"job-{i}",
            title=f"Engineer {i}",
            application_url=f"https://outbox-corp.com/jobs/{i}",
        )
        for i in range(5)
    ]

    registry = ScraperRegistry()
    registry.register("outbox_mock", lambda **kwargs: OutboxTestAdapter(postings))
    service = IngestionService(db_session, registry=registry)

    # Run 1: 5 new jobs
    res1 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res1.newly_inserted == 5

    # Run 2: 5 existing jobs
    res2 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res2.already_existing == 5
    assert res2.newly_inserted == 0

    # Run 3: 5 existing jobs
    res3 = await service.ingest_company(test_company.id)
    await db_session.commit()
    assert res3.already_existing == 5
    assert res3.newly_inserted == 0

    # Confirm exactly 5 jobs and 5 outbox records
    jobs = (await db_session.execute(select(Job))).scalars().all()
    outbox_rows = (await db_session.execute(select(JobDiscoveryOutbox))).scalars().all()

    assert len(jobs) == 5
    assert len(outbox_rows) == 5
    assert {o.job_id for o in outbox_rows} == {j.id for j in jobs}
