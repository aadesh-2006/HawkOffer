"""CompanyScanState repository implementation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.scan_state import CompanyScanState
from app.repositories.base import BaseRepository
from app.schemas.scan_state import ScanStateCreate, ScanStateUpdate


class ScanStateRepository(BaseRepository[CompanyScanState, ScanStateCreate, ScanStateUpdate]):
    """Data access repository for CompanyScanState entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(CompanyScanState, session)

    async def get_by_company_id(self, company_id: int) -> CompanyScanState | None:
        """Fetch the persistent scan state for a specific company."""
        stmt = select(CompanyScanState).where(CompanyScanState.company_id == company_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_create(
        self, company_id: int, scan_interval_minutes: int = 30
    ) -> CompanyScanState:
        """Fetch existing scan state or initialize a default idle state."""
        state = await self.get_by_company_id(company_id)
        if state is None:
            state = await self.create(
                ScanStateCreate(
                    company_id=company_id,
                    scan_interval_minutes=scan_interval_minutes,
                    status="IDLE",
                )
            )
        return state

    async def list_active_states(self) -> list[CompanyScanState]:
        """Fetch scan states belonging to actively monitored companies."""
        stmt = (
            select(CompanyScanState)
            .join(Company, Company.id == CompanyScanState.company_id)
            .where(Company.is_active.is_(True))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_running(
        self, company_id: int, started_at: datetime | None = None
    ) -> CompanyScanState | None:
        """Mark a company's scan as currently active/running."""
        state = await self.get_or_create(company_id)
        now = started_at or datetime.now(UTC)
        state.status = "RUNNING"
        state.last_scan_started_at = now
        state.total_scans += 1
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return state

    async def mark_success(
        self,
        company_id: int,
        completed_at: datetime | None = None,
        next_scan_at: datetime | None = None,
    ) -> CompanyScanState | None:
        """Mark a scan run as successful, resetting consecutive failure counts."""
        state = await self.get_or_create(company_id)
        now = completed_at or datetime.now(UTC)
        state.status = "IDLE"
        state.last_scan_completed_at = now
        state.next_scan_at = next_scan_at
        state.consecutive_failures = 0
        state.last_error = None
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return state

    async def mark_failure(
        self,
        company_id: int,
        error: str,
        completed_at: datetime | None = None,
        next_scan_at: datetime | None = None,
        is_rate_limited: bool = False,
    ) -> CompanyScanState | None:
        """Mark a scan run as failed or rate-limited with failure increment."""
        state = await self.get_or_create(company_id)
        now = completed_at or datetime.now(UTC)
        state.status = "RATE_LIMITED" if is_rate_limited else "FAILED"
        state.last_scan_completed_at = now
        state.next_scan_at = next_scan_at
        state.consecutive_failures += 1
        state.total_errors += 1
        state.last_error = error
        self.session.add(state)
        await self.session.flush()
        await self.session.refresh(state)
        return state

    async def recover_stale_running(self, stale_threshold_seconds: int = 900) -> int:
        """Recover any scans stuck in RUNNING status past the stale threshold."""
        threshold = datetime.now(UTC) - timedelta(seconds=stale_threshold_seconds)
        stmt = (
            update(CompanyScanState)
            .where(
                CompanyScanState.status == "RUNNING",
                CompanyScanState.last_scan_started_at < threshold,
            )
            .values(
                status="FAILED",
                last_error=(
                    f"Recovered from stale RUNNING state "
                    f"(exceeded {stale_threshold_seconds}s timeout)"
                ),
                last_scan_completed_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return int(rowcount)
