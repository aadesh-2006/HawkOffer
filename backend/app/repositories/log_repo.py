"""MonitoringLog repository implementation."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log import MonitoringLog
from app.repositories.base import BaseRepository
from app.schemas.log import MonitoringLogCreate


class MonitoringLogRepository(BaseRepository[MonitoringLog, MonitoringLogCreate, Any]):
    """Data access repository for MonitoringLog entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MonitoringLog, session)

    async def list_by_company(self, company_id: int, limit: int = 50) -> list[MonitoringLog]:
        """List audit logs for a specific company."""
        result = await self.session.execute(
            select(MonitoringLog)
            .where(MonitoringLog.company_id == company_id)
            .order_by(MonitoringLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent(self, limit: int = 50) -> list[MonitoringLog]:
        """List recent audit logs across all companies."""
        result = await self.session.execute(
            select(MonitoringLog).order_by(MonitoringLog.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
