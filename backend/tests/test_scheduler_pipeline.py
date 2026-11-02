"""Comprehensive tests for M5 Phase 3 Unit 7 & 7.1: Scheduler Integration & Lifecycle Hardening."""

import asyncio
from unittest.mock import AsyncMock, patch

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
# 1. REGISTRATION & CONFIGURATION VALIDATION TESTS
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
        scheduler_max_instances=2,
    )

    try:
        await orchestrator.start()
        assert orchestrator.is_running is True

        jobs = {job.id: job for job in orchestrator.scheduler.get_jobs()}

        # Verify stable job IDs are registered
        assert DISCOVERY_JOB_ID in jobs
        assert REEVALUATION_JOB_ID in jobs
        assert BACKLOG_DRAIN_JOB_ID in jobs

        # Verify discovery job configuration and wired max_instances
        disc_job = jobs[DISCOVERY_JOB_ID]
        assert isinstance(disc_job.trigger, IntervalTrigger)
        assert disc_job.max_instances == 2
        assert disc_job.coalesce is True
        assert disc_job.misfire_grace_time == 90

        # Verify reevaluation job configuration and wired max_instances
        reeval_job = jobs[REEVALUATION_JOB_ID]
        assert isinstance(reeval_job.trigger, IntervalTrigger)
        assert reeval_job.max_instances == 2
        assert reeval_job.coalesce is True

        # Verify drain job configuration and wired max_instances
        drain_job = jobs[BACKLOG_DRAIN_JOB_ID]
        assert isinstance(drain_job.trigger, IntervalTrigger)
        assert drain_job.max_instances == 2
        assert drain_job.coalesce is True

    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.parametrize(
    ("param_name", "invalid_value"),
    [
        ("discovery_interval_seconds", 0),
        ("discovery_interval_seconds", -5),
        ("reevaluation_interval_seconds", 0),
        ("drain_interval_seconds", -1),
        ("discovery_batch_size", 0),
        ("reevaluation_chunk_size", -10),
        ("drain_batch_size", 0),
        ("scheduler_misfire_grace_time_seconds", 0),
        ("scheduler_max_instances", 0),
        ("scheduler_max_instances", -1),
        ("default_interval_minutes", 0),
        ("stale_threshold_seconds", 0),
        ("backoff_factor", 0.5),
    ],
)
def test_invalid_configuration_rejected_before_startup(
    session_factory: async_sessionmaker[AsyncSession],
    param_name: str,
    invalid_value: int | float,
) -> None:
    """Verify any non-positive scheduling configuration parameter raises ValueError upfront."""
    from typing import Any

    kwargs: dict[str, Any] = {param_name: invalid_value}
    with pytest.raises(ValueError, match=param_name):
        MonitoringOrchestrator(session_factory=session_factory, **kwargs)


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
# 2. STARTUP PRECONDITION & FAILURE HANDLING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_startup_fails_safely_if_drain_state_fails(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify start() raises RuntimeError if SystemDrainState fails, leaving scheduler stopped."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    with (
        patch.object(
            orchestrator,
            "_ensure_system_drain_state",
            side_effect=RuntimeError("Database corruption on drain state"),
        ),
        pytest.raises(RuntimeError, match="Database corruption on drain state"),
    ):
        await orchestrator.start()

    # Scheduler must not be left running in a broken state
    assert orchestrator.is_running is False


@pytest.mark.asyncio
async def test_startup_fails_safely_if_stale_scan_recovery_fails(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify start() raises RuntimeError on scan recovery failure, leaving scheduler stopped."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    with (
        patch.object(
            orchestrator,
            "recover_stale_scans",
            side_effect=RuntimeError("Deadlock recovering scans"),
        ),
        pytest.raises(RuntimeError, match="Stale scan startup recovery failed"),
    ):
        await orchestrator.start()

    assert orchestrator.is_running is False


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


# =============================================================================
# 3. EXECUTION & OBSERVABILITY TESTS
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
# 4. DETERMINISTIC LIFECYCLE & CONCURRENCY TESTS (ASYNCIO.EVENT)
# =============================================================================


@pytest.mark.asyncio
async def test_overlapping_worker_runs_skipped_via_in_process_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify concurrent attempts to run the same worker return None and skip execution."""
    started_event = asyncio.Event()
    finish_event = asyncio.Event()

    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _slow_run() -> DiscoveryRunResult:
        started_event.set()
        await finish_event.wait()
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _slow_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    # Launch first run in background
    task1 = asyncio.create_task(orchestrator.run_discovery_worker())
    await started_event.wait()  # Deterministically ensure task1 holds the lock

    # Second run attempted concurrently while task1 is inside lock
    task2 = asyncio.create_task(orchestrator.run_discovery_worker())
    res2 = await task2

    # Second run must be skipped immediately
    assert res2 is None
    assert slow_worker.run_once.await_count == 1

    # Release task1
    finish_event.set()
    res1 = await task1
    assert res1 is not None
    assert res1.claimed_count == 1


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_scheduler_dispatched_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify shutdown(wait=True) waits for active scheduled worker to complete cleanly."""
    started_event = asyncio.Event()
    finish_event = asyncio.Event()
    worker_finished = False

    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _in_flight_run() -> DiscoveryRunResult:
        nonlocal worker_finished
        started_event.set()
        await finish_event.wait()
        worker_finished = True
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _in_flight_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    # Start orchestrator
    await orchestrator.start()

    # Trigger worker execution in background
    task = asyncio.create_task(orchestrator.run_discovery_worker())
    await started_event.wait()

    # Launch shutdown(wait=True) while worker is active
    shutdown_task = asyncio.create_task(orchestrator.shutdown(wait=True))
    await asyncio.sleep(0.01)

    # Shutdown should be blocked waiting for finish_event
    assert not shutdown_task.done()
    assert worker_finished is False

    # Release worker
    finish_event.set()
    await shutdown_task
    await task

    assert worker_finished is True
    assert orchestrator.is_running is False


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_manually_triggered_worker_even_if_scheduler_stopped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify shutdown(wait=True) waits for active manual trigger with stopped scheduler."""
    started_event = asyncio.Event()
    finish_event = asyncio.Event()
    worker_finished = False

    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _in_flight_run() -> DiscoveryRunResult:
        nonlocal worker_finished
        started_event.set()
        await finish_event.wait()
        worker_finished = True
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _in_flight_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    # Scheduler is NOT running
    assert orchestrator.is_running is False

    # Launch manual trigger in background
    task = asyncio.create_task(orchestrator.trigger_discovery_now())
    await started_event.wait()

    # Launch shutdown(wait=True)
    shutdown_task = asyncio.create_task(orchestrator.shutdown(wait=True))
    await asyncio.sleep(0.01)

    assert not shutdown_task.done()
    assert worker_finished is False

    # Release worker
    finish_event.set()
    await shutdown_task
    await task

    assert worker_finished is True


@pytest.mark.asyncio
async def test_no_worker_can_begin_execution_after_shutdown_gate_closed(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify all worker entry points reject execution after shutdown closes the lifecycle gate."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    await orchestrator.start()
    await orchestrator.shutdown(wait=True)

    # Every scheduled worker and manual trigger must return None immediately
    assert await orchestrator.run_discovery_worker() is None
    assert await orchestrator.run_reevaluation_worker() is None
    assert await orchestrator.run_backlog_drain_worker() is None
    assert await orchestrator.trigger_discovery_now() is None
    assert await orchestrator.trigger_reevaluation_now() is None
    assert await orchestrator.trigger_backlog_drain_now() is None
    assert await orchestrator.run_company_scan(1) is None
    assert await orchestrator.trigger_scan_now(1) is None

    # None of the mocked worker run_once methods should have been called
    mock_discovery_worker.run_once.assert_not_called()
    mock_reevaluation_worker.run_once.assert_not_called()
    mock_drain_worker.run_once.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_wait_false_does_not_wait_for_active_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify shutdown(wait=False) returns immediately without waiting for in-flight work."""
    started_event = asyncio.Event()
    finish_event = asyncio.Event()

    slow_worker = AsyncMock(spec=JobDiscoveryWorker)

    async def _in_flight_run() -> DiscoveryRunResult:
        started_event.set()
        await finish_event.wait()
        return DiscoveryRunResult(claimed_count=1)

    slow_worker.run_once.side_effect = _in_flight_run

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=slow_worker,
    )

    task = asyncio.create_task(orchestrator.run_discovery_worker())
    await started_event.wait()

    # shutdown(wait=False) should return immediately
    await orchestrator.shutdown(wait=False)
    assert orchestrator.is_running is False
    # Task is still running (blocked on finish_event)
    assert not task.done()

    # Cleanup background task
    finish_event.set()
    await task


@pytest.mark.asyncio
async def test_repeated_shutdown_is_safe(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify shutdown can be called multiple times consecutively without errors."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    await orchestrator.start()
    await orchestrator.shutdown(wait=True)
    await orchestrator.shutdown(wait=True)  # Second call
    await orchestrator.shutdown(wait=False)  # Third call

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


# =============================================================================
# 5. STARTUP LIFECYCLE GATE TESTS (UNIT 7.2)
# =============================================================================


@pytest.mark.asyncio
async def test_manual_worker_triggers_rejected_while_startup_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify manual worker triggers are rejected while startup is blocked at an async step."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    prerequisite_reached = asyncio.Event()
    unblock_prerequisite = asyncio.Event()

    original_sync = orchestrator.sync_company_schedules

    async def blocking_sync() -> None:
        prerequisite_reached.set()
        await unblock_prerequisite.wait()
        await original_sync()

    with patch.object(orchestrator, "sync_company_schedules", side_effect=blocking_sync):
        start_task = asyncio.create_task(orchestrator.start())
        try:
            await prerequisite_reached.wait()

            # Startup is in progress; lifecycle gate must be closed and scheduler not running
            assert orchestrator.is_running is False
            assert orchestrator.is_lifecycle_gate_open is False
            assert orchestrator._is_shutting_down is True

            # All manual triggers must be rejected while startup is blocked
            assert await orchestrator.trigger_discovery_now() is None
            assert await orchestrator.trigger_reevaluation_now() is None
            assert await orchestrator.trigger_backlog_drain_now() is None
            assert await orchestrator.trigger_scan_now(1) is None

            # None of the workers should have been invoked
            mock_discovery_worker.run_once.assert_not_called()
            mock_reevaluation_worker.run_once.assert_not_called()
            mock_drain_worker.run_once.assert_not_called()

            # Unblock prerequisite and allow startup to complete
            unblock_prerequisite.set()
            await start_task

            # Gate must now be open and scheduler running
            assert orchestrator.is_running is True
            assert orchestrator.is_lifecycle_gate_open is True
            assert orchestrator._is_shutting_down is False

            # Manual triggers now succeed
            assert await orchestrator.trigger_discovery_now() is not None
            assert mock_discovery_worker.run_once.call_count == 1
        finally:
            unblock_prerequisite.set()
            if not start_task.done():
                await start_task
            await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_company_schedule_sync_failure_leaves_gate_closed_and_scheduler_stopped(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify company sync failure leaves gate closed, scheduler stopped, and jobs removed."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    with (
        patch.object(
            orchestrator,
            "sync_company_schedules",
            side_effect=RuntimeError("Database failure during company schedule sync"),
        ),
        pytest.raises(RuntimeError, match="Company schedule startup synchronization failed"),
    ):
        await orchestrator.start()

    # Scheduler must be stopped
    assert orchestrator.is_running is False
    # Lifecycle gate must remain closed
    assert orchestrator.is_lifecycle_gate_open is False
    assert orchestrator._is_shutting_down is True
    # Partially registered pipeline jobs must be cleared
    assert len(orchestrator.scheduler.get_jobs()) == 0

    # Manual triggers must be rejected
    assert await orchestrator.trigger_discovery_now() is None
    mock_discovery_worker.run_once.assert_not_called()


@pytest.mark.asyncio
async def test_failed_startup_can_be_retried_safely(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
    mock_reevaluation_worker: AsyncMock,
    mock_drain_worker: AsyncMock,
) -> None:
    """Verify an orchestrator that failed startup can be retried and started successfully."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
        reevaluation_worker=mock_reevaluation_worker,
        drain_worker=mock_drain_worker,
    )

    fail_once = True
    original_sync = orchestrator.sync_company_schedules

    async def flaky_sync() -> None:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("Transient connection error during schedule sync")
        await original_sync()

    # First attempt fails
    with (
        patch.object(orchestrator, "sync_company_schedules", side_effect=flaky_sync),
        pytest.raises(RuntimeError, match="Company schedule startup synchronization failed"),
    ):
        await orchestrator.start()

    assert orchestrator.is_running is False
    assert orchestrator.is_lifecycle_gate_open is False
    assert len(orchestrator.scheduler.get_jobs()) == 0

    # Second attempt succeeds (flaky_sync no longer raises)
    with patch.object(orchestrator, "sync_company_schedules", side_effect=flaky_sync):
        await orchestrator.start()

    try:
        assert orchestrator.is_running is True
        assert orchestrator.is_lifecycle_gate_open is True
        assert orchestrator.scheduler.get_job(DISCOVERY_JOB_ID) is not None
        assert orchestrator.scheduler.get_job(REEVALUATION_JOB_ID) is not None
        assert orchestrator.scheduler.get_job(BACKLOG_DRAIN_JOB_ID) is not None

        result = await orchestrator.trigger_discovery_now()
        assert result is not None
    finally:
        await orchestrator.shutdown(wait=False)


@pytest.mark.asyncio
async def test_repeated_startup_after_shutdown_succeeds(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify orchestrator can restart cleanly after a previous successful shutdown."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    # First start and clean shutdown
    await orchestrator.start()
    assert orchestrator.is_running is True
    assert orchestrator.is_lifecycle_gate_open is True

    await orchestrator.shutdown(wait=True)
    assert orchestrator.is_running is False
    assert orchestrator.is_lifecycle_gate_open is False

    # Second start (restart)
    await orchestrator.start()
    try:
        assert orchestrator.is_running is True
        assert orchestrator.is_lifecycle_gate_open is True
        assert orchestrator.scheduler.get_job(DISCOVERY_JOB_ID) is not None
        assert orchestrator.scheduler.get_job(REEVALUATION_JOB_ID) is not None
        assert orchestrator.scheduler.get_job(BACKLOG_DRAIN_JOB_ID) is not None
    finally:
        await orchestrator.shutdown(wait=True)
        assert orchestrator.is_running is False
        assert orchestrator.is_lifecycle_gate_open is False


@pytest.mark.asyncio
async def test_concurrent_start_calls_are_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    mock_discovery_worker: AsyncMock,
) -> None:
    """Verify multiple concurrent calls to start() execute safely without duplicate jobs."""
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        discovery_worker=mock_discovery_worker,
    )

    try:
        await asyncio.gather(
            orchestrator.start(),
            orchestrator.start(),
            orchestrator.start(),
        )

        assert orchestrator.is_running is True
        assert orchestrator.is_lifecycle_gate_open is True

        # Pipeline jobs must be registered exactly once
        job_ids = [job.id for job in orchestrator.scheduler.get_jobs()]
        assert job_ids.count(DISCOVERY_JOB_ID) == 1
        assert job_ids.count(REEVALUATION_JOB_ID) == 1
        assert job_ids.count(BACKLOG_DRAIN_JOB_ID) == 1
    finally:
        await orchestrator.shutdown(wait=False)
