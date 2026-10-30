"""Tests for MonitoringOrchestrator scheduling, concurrency, backoff, and lifecycle."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.company_repo import CompanyRepository
from app.repositories.scan_state_repo import ScanStateRepository
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.schemas.scan_state import ScanStateCreate
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.exceptions import RateLimitError, ScraperError
from app.scrapers.registry import ScraperRegistry


class MockSchedulerAdapter(BaseScraperAdapter):
    """Mock scraper adapter for testing orchestrator executions."""

    name = "mock_sched_ats"

    def __init__(
        self,
        postings: list[RawJobPosting] | None = None,
        error: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__()
        self.postings = postings or []
        self.error = error
        self.delay_seconds = delay_seconds
        self.call_count = 0

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "sched_corp"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        self.call_count += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        return self.postings


@pytest.fixture
def mock_registry() -> tuple[ScraperRegistry, MockSchedulerAdapter]:
    """Provide a dedicated registry and mock adapter."""
    adapter = MockSchedulerAdapter()
    reg = ScraperRegistry()
    reg.register(adapter.name, lambda **kwargs: adapter)
    return reg, adapter


@pytest.mark.asyncio
async def test_scheduler_sync_registration_and_filtering(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify orchestrator registers active companies, respects intervals, and ignores inactive."""
    reg, _ = mock_registry

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        state_repo = ScanStateRepository(session)

        c1 = await comp_repo.create(
            CompanyCreate(
                name="Active One",
                career_url="https://active1.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        s1 = await state_repo.get_or_create(c1.id, scan_interval_minutes=15)
        assert s1.scan_interval_minutes == 15

        c2 = await comp_repo.create(
            CompanyCreate(
                name="Active Two",
                career_url="https://active2.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        s2 = await state_repo.get_or_create(c2.id, scan_interval_minutes=45)
        assert s2.scan_interval_minutes == 45

        c3 = await comp_repo.create(
            CompanyCreate(
                name="Inactive Three",
                career_url="https://inactive3.com/jobs",
                platform="mock_sched_ats",
                is_active=False,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=reg,
        stagger_range_seconds=(1.0, 5.0),
    )

    try:
        await orchestrator.start()
        jobs = orchestrator.scheduler.get_jobs()
        job_ids = {j.id for j in jobs}

        # c1 and c2 should be scheduled; c3 should not
        assert f"scan_company_{c1.id}" in job_ids
        assert f"scan_company_{c2.id}" in job_ids
        assert f"scan_company_{c3.id}" not in job_ids

        # Deactivate c1 and resync
        async with session_factory() as session:
            comp_repo = CompanyRepository(session)
            await comp_repo.update(c1.id, CompanyUpdate(is_active=False))
            await session.commit()

        await orchestrator.sync_company_schedules()
        updated_jobs = {j.id for j in orchestrator.scheduler.get_jobs()}
        assert f"scan_company_{c1.id}" not in updated_jobs
        assert f"scan_company_{c2.id}" in updated_jobs

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_scheduler_successful_scan_execution(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify successful scan transitions state from IDLE -> RUNNING -> IDLE and persists jobs."""
    reg, adapter = mock_registry
    adapter.postings = [
        RawJobPosting(
            source_ats="mock_sched_ats",
            external_id="post-101",
            title="Senior Staff Engineer",
            application_url="https://active1.com/jobs/101",
            location="Remote",
        )
    ]

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        company = await comp_repo.create(
            CompanyCreate(
                name="Tech Inc",
                career_url="https://tech.com/careers",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=reg,
        jitter_range_seconds=(0.0, 0.0),  # Deterministic in tests
    )

    result = await orchestrator.trigger_scan_now(company.id)
    assert result is not None
    assert result.newly_inserted == 1
    assert result.failed_records == 0

    async with session_factory() as session:
        state_repo = ScanStateRepository(session)
        state = await state_repo.get_by_company_id(company.id)
        assert state is not None
        assert state.status == "IDLE"
        assert state.total_scans == 1
        assert state.total_errors == 0
        assert state.consecutive_failures == 0
        assert state.last_error is None
        assert state.last_scan_started_at is not None
        assert state.last_scan_completed_at is not None
        assert state.next_scan_at is not None


@pytest.mark.asyncio
async def test_scheduler_overlapping_scan_prevention(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify in-process concurrency guard prevents concurrent overlapping runs for same company."""
    reg, adapter = mock_registry
    adapter.delay_seconds = 0.2  # Simulate non-trivial async work

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        company = await comp_repo.create(
            CompanyCreate(
                name="Concurrent Corp",
                career_url="https://concurrent.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(session_factory=session_factory, registry=reg)

    # Launch two concurrent scans for the same company
    task1 = asyncio.create_task(orchestrator.run_company_scan(company.id))
    task2 = asyncio.create_task(orchestrator.run_company_scan(company.id))

    res1, res2 = await asyncio.gather(task1, task2)

    # Exactly one scan should succeed, and the other must be skipped (return None)
    results = [res1, res2]
    successful_runs = [r for r in results if r is not None]
    skipped_runs = [r for r in results if r is None]

    assert len(successful_runs) == 1
    assert len(skipped_runs) == 1
    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_scheduler_failure_and_exponential_backoff(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify exponential backoff calculation, failure tracking, and reset on recovery."""
    reg, adapter = mock_registry
    adapter.error = ScraperError("HTTP 500 Internal Server Error")

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        company = await comp_repo.create(
            CompanyCreate(
                name="Failing Corp",
                career_url="https://fail.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=reg,
        default_interval_minutes=10,
        backoff_factor=2.0,
        max_backoff_minutes=60,
        jitter_range_seconds=(0.0, 0.0),
    )

    # 1st Failure: base interval = 10m
    t0 = datetime.now(UTC)
    await orchestrator.run_company_scan(company.id)
    async with session_factory() as session:
        state1 = await ScanStateRepository(session).get_by_company_id(company.id)
        assert state1 is not None
        assert state1.status == "FAILED"
        assert state1.consecutive_failures == 1
        assert state1.total_errors == 1
        assert state1.next_scan_at is not None
        diff1 = (state1.next_scan_at.replace(tzinfo=UTC) - t0).total_seconds()
        assert 590 <= diff1 <= 615  # ~10 minutes

    # 2nd Failure: 10m * 2^1 = 20m
    t1 = datetime.now(UTC)
    await orchestrator.run_company_scan(company.id)
    async with session_factory() as session:
        state2 = await ScanStateRepository(session).get_by_company_id(company.id)
        assert state2 is not None
        assert state2.consecutive_failures == 2
        assert state2.total_errors == 2
        assert state2.next_scan_at is not None
        diff2 = (state2.next_scan_at.replace(tzinfo=UTC) - t1).total_seconds()
        assert 1190 <= diff2 <= 1215  # ~20 minutes

    # 3rd Failure: 10m * 2^2 = 40m
    t2 = datetime.now(UTC)
    await orchestrator.run_company_scan(company.id)
    async with session_factory() as session:
        state3 = await ScanStateRepository(session).get_by_company_id(company.id)
        assert state3 is not None
        assert state3.consecutive_failures == 3
        assert state3.next_scan_at is not None
        diff3 = (state3.next_scan_at.replace(tzinfo=UTC) - t2).total_seconds()
        assert 2390 <= diff3 <= 2415  # ~40 minutes

    # Recovery: Scraper succeeds
    adapter.error = None
    adapter.postings = [
        RawJobPosting(
            source_ats="mock_sched_ats",
            external_id="recovered-1",
            title="SRE",
            application_url="https://fail.com/jobs/1",
        )
    ]
    await orchestrator.run_company_scan(company.id)
    async with session_factory() as session:
        state_recovered = await ScanStateRepository(session).get_by_company_id(company.id)
        assert state_recovered is not None
        assert state_recovered.status == "IDLE"
        assert state_recovered.consecutive_failures == 0
        assert state_recovered.last_error is None
        assert state_recovered.total_errors == 3


@pytest.mark.asyncio
async def test_scheduler_rate_limit_status(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify RateLimitError marks scan state as RATE_LIMITED."""
    reg, adapter = mock_registry
    adapter.error = RateLimitError("Rate limit exceeded: 429", retry_after=120)

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        company = await comp_repo.create(
            CompanyCreate(
                name="Throttled Corp",
                career_url="https://throttled.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(session_factory=session_factory, registry=reg)
    await orchestrator.run_company_scan(company.id)

    async with session_factory() as session:
        state = await ScanStateRepository(session).get_by_company_id(company.id)
        assert state is not None
        assert state.status == "RATE_LIMITED"
        assert state.consecutive_failures == 1
        assert "429" in (state.last_error or "")


@pytest.mark.asyncio
async def test_scheduler_stale_scan_recovery_on_startup(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify startup recovery resets scans stuck in RUNNING status past threshold."""
    reg, _ = mock_registry

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        state_repo = ScanStateRepository(session)

        c = await comp_repo.create(
            CompanyCreate(
                name="Crash Corp",
                career_url="https://crash.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        # Create state stuck in RUNNING from 25 minutes ago
        stale_started = datetime.now(UTC) - timedelta(minutes=25)
        await state_repo.create(
            ScanStateCreate(
                company_id=c.id,
                status="RUNNING",
                last_scan_started_at=stale_started,
                scan_interval_minutes=30,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=reg,
        stale_threshold_seconds=900,  # 15 minutes
    )

    recovered = await orchestrator.recover_stale_scans()
    assert recovered == 1

    async with session_factory() as session:
        state = await ScanStateRepository(session).get_by_company_id(c.id)
        assert state is not None
        assert state.status == "FAILED"
        assert "timeout" in (state.last_error or "").lower()


@pytest.mark.asyncio
async def test_scheduler_pause_resume_and_status(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify pause, resume, and get_schedule_status reporting."""
    reg, _ = mock_registry

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        c = await comp_repo.create(
            CompanyCreate(
                name="Controls Corp",
                career_url="https://controls.com/jobs",
                platform="mock_sched_ats",
                is_active=True,
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(session_factory=session_factory, registry=reg)
    try:
        await orchestrator.start()

        # Check status
        status = await orchestrator.get_schedule_status(c.id)
        assert status is not None
        assert status["company_id"] == c.id
        assert status["job_info"]["scheduled"] is True

        # Pause
        assert orchestrator.pause_company(c.id) is True

        # Resume
        assert orchestrator.resume_company(c.id) is True

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_scheduler_lifecycle_idempotency(
    session_factory: async_sessionmaker[AsyncSession],
    mock_registry: tuple[ScraperRegistry, MockSchedulerAdapter],
) -> None:
    """Verify start and shutdown can be called multiple times without exceptions."""
    reg, _ = mock_registry
    orchestrator = MonitoringOrchestrator(session_factory=session_factory, registry=reg)

    # Double start
    await orchestrator.start()
    assert orchestrator.is_running is True
    await orchestrator.start()
    assert orchestrator.is_running is True

    # Double shutdown
    await orchestrator.shutdown(wait=False)
    assert orchestrator.is_running is False
    await orchestrator.shutdown(wait=False)
    assert orchestrator.is_running is False


@pytest.mark.asyncio
async def test_scheduler_independent_company_error_isolation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify failure of Company A does not impede successful scanning of Company B."""
    adapter_a = MockSchedulerAdapter(error=ScraperError("Adapter A broken"))
    adapter_a.name = "ats_a"
    adapter_b = MockSchedulerAdapter(
        postings=[
            RawJobPosting(
                source_ats="ats_b",
                external_id="b1",
                title="B Engineer",
                application_url="https://b.com/jobs/1",
            )
        ]
    )
    adapter_b.name = "ats_b"

    reg = ScraperRegistry()
    reg.register(adapter_a.name, lambda **kwargs: adapter_a)
    reg.register(adapter_b.name, lambda **kwargs: adapter_b)

    async with session_factory() as session:
        comp_repo = CompanyRepository(session)
        comp_a = await comp_repo.create(
            CompanyCreate(
                name="Company A",
                career_url="https://a.com/jobs",
                platform="ats_a",
            )
        )
        comp_b = await comp_repo.create(
            CompanyCreate(
                name="Company B",
                career_url="https://b.com/jobs",
                platform="ats_b",
            )
        )
        await session.commit()

    orchestrator = MonitoringOrchestrator(session_factory=session_factory, registry=reg)

    res_a = await orchestrator.trigger_scan_now(comp_a.id)
    res_b = await orchestrator.trigger_scan_now(comp_b.id)

    assert res_a is not None and res_a.failed_records == 1
    assert res_b is not None and res_b.newly_inserted == 1

    async with session_factory() as session:
        state_repo = ScanStateRepository(session)
        state_a = await state_repo.get_by_company_id(comp_a.id)
        state_b = await state_repo.get_by_company_id(comp_b.id)

        assert state_a is not None and state_a.status == "FAILED"
        assert state_b is not None and state_b.status == "IDLE"
