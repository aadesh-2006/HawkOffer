"""PreferenceProfile repository implementation."""

from sqlalchemy import select
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

    async def get_active_profile(self) -> PreferenceProfile | None:
        """Retrieve the primary active user preference profile."""
        result = await self.session.execute(
            select(PreferenceProfile)
            .where(PreferenceProfile.is_active.is_(True))
            .order_by(PreferenceProfile.id.asc())
        )
        return result.scalars().first()
