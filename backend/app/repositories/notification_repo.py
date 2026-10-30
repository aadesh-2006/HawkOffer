"""Notification repository implementation."""

from datetime import UTC, datetime

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories.base import BaseRepository
from app.schemas.notification import (
    DeliveryStatus,
    NotificationCreate,
    NotificationStatus,
    NotificationType,
    NotificationUpdate,
)


class NotificationRepository(BaseRepository[Notification, NotificationCreate, NotificationUpdate]):
    """Data access repository for Notification entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Notification, session)

    async def list_notifications(
        self,
        profile_id: int | None = None,
        status: str | None = None,
        include_dismissed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        """List notifications ordered by newest first with profile and status filters."""
        query = select(Notification)

        if profile_id is not None:
            query = query.where(Notification.profile_id == profile_id)

        if status is not None:
            query = query.where(Notification.status == status)
        elif not include_dismissed:
            query = query.where(Notification.status != NotificationStatus.DISMISSED)

        query = query.order_by(Notification.created_at.desc(), Notification.id.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_unread_count(self, profile_id: int | None = None) -> int:
        """Count unread notifications, optionally filtered by profile."""
        query = select(func.count(Notification.id)).where(
            Notification.status == NotificationStatus.UNREAD
        )
        if profile_id is not None:
            query = query.where(Notification.profile_id == profile_id)

        result = await self.session.execute(query)
        count = result.scalar()
        return count or 0

    async def mark_as_read(self, notification_id: int) -> Notification | None:
        """Transition notification to READ with read_at timestamp (idempotent)."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        if notif.status != NotificationStatus.READ:
            notif.status = NotificationStatus.READ
            notif.read_at = datetime.now(UTC)
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def mark_as_unread(self, notification_id: int) -> Notification | None:
        """Transition notification back to UNREAD, resetting read_at timestamp (idempotent)."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        if notif.status != NotificationStatus.UNREAD:
            notif.status = NotificationStatus.UNREAD
            notif.read_at = None
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def dismiss(self, notification_id: int) -> Notification | None:
        """Transition notification to DISMISSED with dismissed_at timestamp (idempotent)."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        if notif.status != NotificationStatus.DISMISSED:
            notif.status = NotificationStatus.DISMISSED
            notif.dismissed_at = datetime.now(UTC)
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def restore(self, notification_id: int) -> Notification | None:
        """Restore a dismissed notification back to READ (if read_at exists) or UNREAD."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        if notif.status == NotificationStatus.DISMISSED:
            notif.status = NotificationStatus.READ if notif.read_at else NotificationStatus.UNREAD
            notif.dismissed_at = None
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def mark_batch_read(self, notification_ids: list[int]) -> int:
        """Batch transition multiple notifications to READ."""
        if not notification_ids:
            return 0

        now = datetime.now(UTC)
        stmt = (
            update(Notification)
            .where(
                Notification.id.in_(notification_ids),
                Notification.status != NotificationStatus.READ,
            )
            .values(status=NotificationStatus.READ, read_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return int(rowcount)

    async def dismiss_batch(self, notification_ids: list[int]) -> int:
        """Batch transition multiple notifications to DISMISSED."""
        if not notification_ids:
            return 0

        now = datetime.now(UTC)
        stmt = (
            update(Notification)
            .where(
                Notification.id.in_(notification_ids),
                Notification.status != NotificationStatus.DISMISSED,
            )
            .values(status=NotificationStatus.DISMISSED, dismissed_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return int(rowcount)

    async def get_by_job_profile_type(
        self,
        job_id: int,
        profile_id: int | None,
        notification_type: str = NotificationType.JOB_ALERT,
    ) -> Notification | None:
        """Retrieve existing notification matching (job_id, profile_id, notification_type)."""
        query = select(Notification).where(
            Notification.job_id == job_id,
            Notification.notification_type == notification_type,
        )
        if profile_id is None:
            query = query.where(Notification.profile_id.is_(None))
        else:
            query = query.where(Notification.profile_id == profile_id)

        result = await self.session.execute(query)
        return result.scalars().first()

    async def list_pending(self, limit: int = 50) -> list[Notification]:
        """List notifications queued for dispatch."""
        result = await self.session.execute(
            select(Notification)
            .where(Notification.delivery_status == DeliveryStatus.PENDING)
            .order_by(Notification.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_delivered(self, notification_id: int) -> Notification | None:
        """Mark a notification as successfully dispatched/delivered."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        notif.delivery_status = DeliveryStatus.DELIVERED
        notif.delivered_at = datetime.now(UTC)
        self.session.add(notif)
        await self.session.flush()
        await self.session.refresh(notif)
        return notif

    async def mark_delivery_failed(self, notification_id: int) -> Notification | None:
        """Mark a notification as delivery failed without altering user-facing status."""
        notif = await self.get_by_id(notification_id)
        if not notif:
            return None

        notif.delivery_status = DeliveryStatus.FAILED
        self.session.add(notif)
        await self.session.flush()
        await self.session.refresh(notif)
        return notif
