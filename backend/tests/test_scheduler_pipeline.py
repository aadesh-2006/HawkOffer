"""Comprehensive tests for M5 Phase 3 Unit 7: Scheduler Integration."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.company import Company
from app.models.outbox import SystemDrainState
from app.scheduler.orchestrator import (
    BACKLOG_DRAIN_JOB_ID,
    DISCOVERY_JOB_ID,
    REEVALUATION_JOB_ID,
    MonitoringOrchestrator,
)
from app.services.backlog_drain_worker import BacklogDrainWorker, DrainRunResult
from app.services.discovery_worker import DiscoveryRunResult, JobDiscoveryWorker
from app.services.reevaluation_worker import ProfileReevaluationWorker, ReevaluationRunResult


@pytest.fixture
def mock_discovery_worker() -> AsyncMock:
    """Provide a mocked JobDiscoveryWorker."""
    worker = AsyncMock(spec=JobDiscoveryWorker)
    worker.run_once = AsyncMock(
        return_value=DiscoveryRunResult(
            claimed_count=2,
            evaluated_jobs=2,
            candidate_matches=2,
            processed_outbox_count=2,
        )
    )
    return worker


@pytest.fixture
def mock_reevaluation_worker() -> AsyncMock:
    """Provide a mocked ProfileReevaluationWorker."""
    worker = AsyncMock(spec=ProfileReevaluationWorker)
    worker.run_once = AsyncMock(
        return_value=ReevaluationRunResult(
            tasks_claimed=1,
            chunks_processed=1,
            jobs_evaluated=5,
            candidates_created=3,
            tasks_completed=1,
        )
    )
    return worker


@pytest.fixture
def mock_drain_worker() -> AsyncMock:
    """Provide a mocked BacklogDrainWorker."""
    worker = AsyncMock(spec=BacklogDrainWorker)
    worker.run_once = AsyncMock(
        return_value=DrainRunResult(
            claimed_count=3,
            promoted_count=3,
            final_drain_counter=3,
        )
    )
    return worker


# =============================================================================
# 1. REGISTRATION & CONFIGURATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_workers_registration(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify all 3 M5 workers are registered with stable IDs, triggers, and configurations."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
        discovery_interval_seconds=15,
        reevaluation_interval_seconds=45,
        drain_interval_seconds=10,
        scheduler_misfire_grace_time_seconds=90,
    )

    try:
        await orchestrator.start()
        assert orchestrator.is_running is True

        jobs = {job.id: job for job in orchestrator.scheduler.get_jobs()}

        # Verify stable job IDs are registered
        assert DISCOVERY_JOB_ID in jobs
        assert REEVALUATION_JOB_ID in jobs
        assert BACKLOG_DRAIN_JOB_ID in jobs

        # Verify discovery job configuration
        disc_job = jobs[DISCOVERY_JOB_ID]
        assert isinstance(disc_job.trigger, IntervalTrigger)
        assert disc_job.max_instances == 1
        assert disc_job.coalesce is True
        assert disc_job.misfire_grace_time == 90

        # Verify reevaluation job configuration
        reeval_job = jobs[REEVALUATION_JOB_ID]
        assert isinstance(reeval_job.trigger, IntervalTrigger)
        assert reeval_job.max_instances == 1
        assert reeval_job.coalesce is True

        # Verify drain job configuration
        drain_job = jobs[BACKLOG_DRAIN_JOB_ID]
        assert isinstance(drain_job.trigger, IntervalTrigger)
        assert drain_job.max_instances == 1
        assert drain_job.coalesce is True

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_pipeline_and_company_scans_coexist(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify company scan jobs and pipeline worker jobs coexist without conflict."""
    async with session_factory() as session:
        company = Company(name="Coexist Corp", career_url="https://coexist.com", is_active=True)
        session.add(company)
        await session.commit()
        company_id = company.id

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    try:
        await orchestrator.start()
        jobs = {job.id: job for job in orchestrator.scheduler.get_jobs()}

        # Company job and all 3 pipeline jobs present
        assert f"scan_company_{company_id}" in jobs
        assert DISCOVERY_JOB_ID in jobs
        assert REEVALUATION_JOB_ID in jobs
        assert BACKLOG_DRAIN_JOB_ID in jobs

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_repeated_startup_idempotent_no_duplicate_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify repeated start() calls do not duplicate scheduled pipeline jobs."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    try:
        await orchestrator.start()
        await orchestrator.start()  # Second start call

        jobs = orchestrator.scheduler.get_jobs()
        job_ids = [job.id for job in jobs]

        # Verify exactly one instance of each job
        assert job_ids.count(DISCOVERY_JOB_ID) == 1
        assert job_ids.count(REEVALUATION_JOB_ID) == 1
        assert job_ids.count(BACKLOG_DRAIN_JOB_ID) == 1

    finally:
        await orchestrator.shutdown(wait=False)


# =============================================================================
# 2. EXECUTION & OBSERVABILITY TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_discovery_job_execution(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify run_discovery_worker delegates to worker and returns typed result."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    result = await orchestrator.run_discovery_worker()
    assert result is not None
    assert result.claimed_count == 2
    assert result.candidate_matches == 2
    mock_discovery_worker.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_reevaluation_job_execution(
    session_factory: async_sessionmaker[AsyncSession],
    mock_reevaluation_worker: AsyncMock,
) -> None:
    """Verify run_reevaluation_worker delegates to worker and returns typed result."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        reevaluation_worker=mock_reevaluation_worker,
    )

    result = await orchestrator.run_reevaluation_worker()
    assert result is not None
    assert result.tasks_claimed == 1
    assert result.candidates_created == 3
    mock_reevaluation_worker.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_backlog_drain_job_execution(
    session_factory: async_sessionmaker[AsyncSession],
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify run_backlog_drain_worker delegates to worker and returns typed result."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        drain_worker=mock_drain_worker,
    )

    result = await orchestrator.run_backlog_drain_worker()
    assert result is not None
    assert result.claimed_count == 3
    assert result.promoted_count == 3
    mock_drain_worker.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_exception_logged_and_does_not_disable_future_runs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify exceptions in a worker run are caught and do not disable subsequent runs."""
    mock_discovery_worker.run_once.side_effect = [
        RuntimeError("Transient discovery failure"),
        DiscoveryRunResult(claimed_count=1),
    ]

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    # First execution fails safely
    res1 = await orchestrator.run_discovery_worker()
    assert res1 is None

    # Subsequent execution succeeds
    res2 = await orchestrator.run_discovery_worker()
    assert res2 is not None
    assert res2.claimed_count == 1
    assert mock_discovery_worker.run_once.await_count == 2


@pytest.mark.asyncio
async def test_worker_failure_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify failure in one worker does not affect other workers."""
    mock_discovery_worker.run_once.side_effect = RuntimeError("Discovery crash")

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    disc_res = await orchestrator.run_discovery_worker()
    reeval_res = await orchestrator.run_reevaluation_worker()
    drain_res = await orchestrator.run_backlog_drain_worker()

    assert disc_res is None
    assert reeval_res is not None
    assert drain_res is not None


@pytest.mark.asyncio
async def test_manual_triggers(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify manual triggers execute pipeline workers directly."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    d_res = await orchestrator.trigger_discovery_now()
    r_res = await orchestrator.trigger_reevaluation_now()
    b_res = await orchestrator.trigger_backlog_drain_now()

    assert d_res is not None
    assert r_res is not None
    assert b_res is not None


# =============================================================================
# 3. CONCURRENCY & LIFECYCLE TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_overlapping_worker_runs_skipped_via_in_process_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify concurrent attempts to run the same worker return None and skip execution."""
    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _slow_run() -> DiscoveryRunResult:
        await asyncio.sleep(0.1)
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _slow_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    # Launch two concurrent runs
    res1, res2 = await asyncio.gather(
        orchestrator.run_discovery_worker(),
        orchestrator.run_discovery_worker(),
    )

    # Exactly one succeeded; the other was skipped
    results = [res1, res2]
    assert None in results
    assert any(isinstance(r, DiscoveryRunResult) for r in results)
    assert slow_worker.run_once.await_count == 1


@pytest.mark.asyncio
async def test_shutdown_waits_for_in_flight_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify shutdown(wait=True) waits for active in-flight worker run to complete cleanly."""
    executed = False

    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _in_flight_run() -> DiscoveryRunResult:
        nonlocal executed
        await asyncio.sleep(0.05)
        executed = True
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _in_flight_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    # Start task in background
    task = asyncio.create_task(orchestrator.run_discovery_worker())
    await asyncio.sleep(0.01)

    # Shutdown with wait=True
    await orchestrator.shutdown(wait=True)
    await task

    assert executed is True
    assert orchestrator.is_running is False


@pytest.mark.asyncio
async def test_pause_and_resume_pipeline_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify pause_pipeline_job and resume_pipeline_job functionality."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    try:
        await orchestrator.start()

        # Pause discovery job
        assert orchestrator.pause_pipeline_job(DISCOVERY_JOB_ID) is True
        status = orchestrator.get_pipeline_status()
        assert status["discovery"]["paused"] is True

        # Resume discovery job
        assert orchestrator.resume_pipeline_job(DISCOVERY_JOB_ID) is True
        status = orchestrator.get_pipeline_status()
        assert status["discovery"]["paused"] is False

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_startup_initializes_system_drain_state(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify orchestrator start() ensures SystemDrainState row 1 exists."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    try:
        await orchestrator.start()

        async with session_factory() as session:
            res = await session.execute(select(SystemDrainState).where(SystemDrainState.id == 1))
            state = res.scalar_one_or_none()
            assert state is not None
            assert state.id == 1
            assert state.drain_counter >= 0

    finally:
        await orchestrator.shutdown(wait=False)
