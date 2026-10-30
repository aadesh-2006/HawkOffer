"""Tests for CompanyScanState model, repository, and lifecycle transitions."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan_state import CompanyScanState
from app.repositories.company_repo import CompanyRepository
from app.repositories.scan_state_repo import ScanStateRepository
from app.schemas.company import CompanyCreate


@pytest.mark.asyncio
async def test_scan_state_get_or_create_and_defaults(db_session: AsyncSession) -> None:
    """Verify get_or_create creates default IDLE state with 30m interval."""
    comp_repo = CompanyRepository(db_session)
    state_repo = ScanStateRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Acme Tech",
            career_url="https://acme.com/jobs",
            platform="greenhouse",
        )
    )

    state = await state_repo.get_or_create(company.id, scan_interval_minutes=45)
    assert state.company_id == company.id
    assert state.scan_interval_minutes == 45
    assert state.status == "IDLE"
    assert state.consecutive_failures == 0
    assert state.total_scans == 0
    assert state.total_errors == 0
    assert state.last_scan_started_at is None

    # Calling again returns the exact same instance without recreating
    state2 = await state_repo.get_or_create(company.id, scan_interval_minutes=60)
    assert state2.id == state.id
    assert state2.scan_interval_minutes == 45  # Keeps existing interval


@pytest.mark.asyncio
async def test_scan_state_lifecycle_transitions(db_session: AsyncSession) -> None:
    """Verify state transitions: IDLE -> RUNNING -> FAILED -> RUNNING -> SUCCESS."""
    comp_repo = CompanyRepository(db_session)
    state_repo = ScanStateRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Beta Systems",
            career_url="https://beta.com/jobs",
            platform="lever",
        )
    )

    # 1. Mark Running
    t0 = datetime.now(UTC)
    state = await state_repo.mark_running(company.id, started_at=t0)
    assert state is not None
    assert state.status == "RUNNING"
    assert state.total_scans == 1
    assert state.last_scan_started_at is not None

    # 2. Mark Failure (simulating first scan failed)
    next_t = t0 + timedelta(minutes=15)
    state_fail = await state_repo.mark_failure(
        company.id,
        error="Connection refused: 502",
        completed_at=datetime.now(UTC),
        next_scan_at=next_t,
        is_rate_limited=False,
    )
    assert state_fail is not None
    assert state_fail.status == "FAILED"
    assert state_fail.consecutive_failures == 1
    assert state_fail.total_errors == 1
    assert state_fail.last_error == "Connection refused: 502"
    assert state_fail.next_scan_at is not None
    assert state_fail.next_scan_at.replace(tzinfo=UTC) == next_t

    # 3. Mark Rate Limited
    state_rl = await state_repo.mark_failure(
        company.id,
        error="Rate limit exceeded: 429",
        is_rate_limited=True,
    )
    assert state_rl is not None
    assert state_rl.status == "RATE_LIMITED"
    assert state_rl.consecutive_failures == 2
    assert state_rl.total_errors == 2

    # 4. Mark Success: Resets consecutive failures and clears last_error
    next_success = datetime.now(UTC) + timedelta(minutes=30)
    state_success = await state_repo.mark_success(
        company.id,
        completed_at=datetime.now(UTC),
        next_scan_at=next_success,
    )
    assert state_success is not None
    assert state_success.status == "IDLE"
    assert state_success.consecutive_failures == 0
    assert state_success.last_error is None
    assert state_success.next_scan_at is not None
    assert state_success.next_scan_at.replace(tzinfo=UTC) == next_success


@pytest.mark.asyncio
async def test_recover_stale_running_scans(db_session: AsyncSession) -> None:
    """Verify recovery of scans stuck in RUNNING status past the timeout threshold."""
    comp_repo = CompanyRepository(db_session)
    state_repo = ScanStateRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Stale Inc",
            career_url="https://stale.com/jobs",
            platform="greenhouse",
        )
    )

    # Simulate scan started 30 minutes ago and stuck in RUNNING
    stale_time = datetime.now(UTC) - timedelta(minutes=30)
    state = await state_repo.get_or_create(company.id)
    state.status = "RUNNING"
    state.last_scan_started_at = stale_time
    db_session.add(state)
    await db_session.commit()

    # Recover with 15-minute threshold (900 seconds)
    recovered_count = await state_repo.recover_stale_running(stale_threshold_seconds=900)
    assert recovered_count == 1

    refreshed = await state_repo.get_by_company_id(company.id)
    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert "Recovered from stale RUNNING state" in (refreshed.last_error or "")


@pytest.mark.asyncio
async def test_scan_state_company_cascade_and_uniqueness(
    db_session: AsyncSession,
) -> None:
    """Verify 1-to-1 uniqueness and cascade deletion when company is deleted."""
    comp_repo = CompanyRepository(db_session)
    state_repo = ScanStateRepository(db_session)

    company = await comp_repo.create(
        CompanyCreate(
            name="Cascade Co",
            career_url="https://cascade.com/jobs",
            platform="generic",
        )
    )
    company_id = company.id

    await state_repo.get_or_create(company_id)

    # Duplicate insertion for same company must raise IntegrityError
    dup_state = CompanyScanState(
        company_id=company_id,
        scan_interval_minutes=30,
        status="IDLE",
    )
    db_session.add(dup_state)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Deleting the company cascades to delete the scan state
    await comp_repo.delete(company_id)
    assert await state_repo.get_by_company_id(company_id) is None
