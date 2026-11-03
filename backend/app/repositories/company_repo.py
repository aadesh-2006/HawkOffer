"""Company repository implementation."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.subscription import UserCompanySubscription
from app.models.user import User
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

    async def list_active_monitored_targets(self) -> list[Company]:
        """Retrieve distinct active companies with active monitoring demand.

        Semantics:
        1. Desktop mode (auth_required=False):
           If zero subscriptions exist in the entire database, returns all active
           companies to preserve single-user standalone desktop behavior.
        2. Multi-tenant SaaS mode (auth_required=True or subscriptions present):
           - Never falls back to scraping all active companies.
           - Targets companies with active subscriptions from active users (is_active=True).
           - Curated companies without subscribers are included only if
             settings.monitor_curated_without_subscribers is True.
        """
        # Desktop fallback: only activates when auth is NOT required and 0 subscriptions exist
        if not settings.auth_required:
            sub_count_res = await self.session.execute(
                select(func.count(UserCompanySubscription.id))
            )
            has_subscriptions = (sub_count_res.scalar() or 0) > 0
            if not has_subscriptions:
                return await self.list_active()

        # Active subscriptions from active users only
        active_sub_company_ids = (
            select(UserCompanySubscription.company_id)
            .join(User, User.id == UserCompanySubscription.user_id)
            .where(User.is_active.is_(True))
        )

        conditions = [Company.id.in_(active_sub_company_ids)]
        if settings.monitor_curated_without_subscribers:
            conditions.append(Company.is_curated.is_(True))

        stmt = (
            select(Company)
            .where(Company.is_active.is_(True))
            .where(or_(*conditions))
            .distinct()
            .order_by(Company.name.asc(), Company.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
