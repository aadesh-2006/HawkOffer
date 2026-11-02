"""Company repository implementation."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.repositories.base import BaseRepository
from app.schemas.company import CompanyCreate, CompanyUpdate


class CompanyRepository(BaseRepository[Company, CompanyCreate, CompanyUpdate]):
    """Data access repository for Company entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Company, session)

    async def get_by_name(self, name: str) -> Company | None:
        """Look up a company by exact name."""
        result = await self.session.execute(select(Company).where(Company.name == name))
        return result.scalars().first()

    async def get_by_career_url(self, career_url: str) -> Company | None:
        """Look up a company by exact career URL."""
        result = await self.session.execute(select(Company).where(Company.career_url == career_url))
        return result.scalars().first()

    async def list_active(self) -> list[Company]:
        """List all active companies configured for monitoring."""
        result = await self.session.execute(select(Company).where(Company.is_active.is_(True)))
        return list(result.scalars().all())

    async def list_paginated(
        self, skip: int = 0, limit: int = 100, is_active: bool | None = None
    ) -> list[Company]:
        """List companies with optional active filter and stable ordering."""
        stmt = select(Company)
        if is_active is not None:
            stmt = stmt.where(Company.is_active == is_active)
        stmt = stmt.order_by(Company.name.asc(), Company.id.asc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_companies(self, is_active: bool | None = None) -> int:
        """Count companies with optional active filter."""
        stmt = select(func.count()).select_from(Company)
        if is_active is not None:
            stmt = stmt.where(Company.is_active == is_active)
        result = await self.session.execute(stmt)
        return result.scalar() or 0
