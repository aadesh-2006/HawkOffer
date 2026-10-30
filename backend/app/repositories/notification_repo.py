"""Notification repository implementation."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories.base import BaseRepository
from app.schemas.notification import NotificationCreate, NotificationUpdate


class NotificationRepository(BaseRepository[Notification, NotificationCreate, NotificationUpdate]):
    """Data access repository for Notification entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Notification, session)

    async def list_pending(self, limit: int = 50) -> list[Notification]:
        """List notifications queued for dispatch."""
        result = await self.session.execute(
            select(Notification)
            .where(Notification.status == "PENDING")
            .order_by(Notification.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_delivered(self, notification_id: int) -> Notification | None:
        """Mark a notification as successfully delivered."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        notif.status = "DELIVERED"
        notif.delivered_at = datetime.now(UTC)
        self.session.add(notif)
        await self.session.flush()
        await self.session.refresh(notif)
        return notif
