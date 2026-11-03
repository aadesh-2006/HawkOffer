"""PreferenceProfile repository implementation."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.preference import PreferenceProfile
from app.repositories.base import BaseRepository
from app.schemas.preference import PreferenceProfileCreate, PreferenceProfileUpdate


class PreferenceProfileRepository(
    BaseRepository[PreferenceProfile, PreferenceProfileCreate, PreferenceProfileUpdate]
):
    """Data access repository for PreferenceProfile entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(PreferenceProfile, session)

    async def get_active_profile(self, user_id: int | None = None) -> PreferenceProfile | None:
        """Retrieve the primary active user preference profile."""
        query = select(PreferenceProfile).where(PreferenceProfile.is_active.is_(True))
        if user_id is not None:
            query = query.where(PreferenceProfile.user_id == user_id)
        result = await self.session.execute(
            query.order_by(PreferenceProfile.id.asc())
        )
        return result.scalars().first()

    async def list_by_user(
        self, user_id: int | None = None, skip: int = 0, limit: int = 100
    ) -> list[PreferenceProfile]:
        """Fetch profiles optionally filtered by owning user."""
        query = select(PreferenceProfile)
        if user_id is not None:
            query = query.where(PreferenceProfile.user_id == user_id)
        query = query.order_by(PreferenceProfile.id.asc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_by_user(self, user_id: int | None = None) -> int:
        """Count profiles optionally filtered by owning user."""
        query = select(func.count()).select_from(PreferenceProfile)
        if user_id is not None:
            query = query.where(PreferenceProfile.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalar() or 0

