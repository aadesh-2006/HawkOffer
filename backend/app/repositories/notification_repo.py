"""Notification repository implementation with multi-tenant scoping support."""

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
    """Data access repository for Notification entities with user isolation."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Notification, session)

    async def get_by_id_and_user(
        self, notification_id: int, user_id: int | None = None
    ) -> Notification | None:
        """Fetch notification by ID scoped strictly to user_id if provided."""
        stmt = select(Notification).where(Notification.id == notification_id)
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_notifications(
        self,
        profile_id: int | None = None,
        status: str | None = None,
        include_dismissed: bool = False,
        limit: int = 50,
        offset: int = 0,
        user_id: int | None = None,
    ) -> list[Notification]:
        """List notifications ordered by newest first with user, profile and status filters."""
        query = select(Notification)

        if user_id is not None:
            query = query.where(Notification.user_id == user_id)

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

    async def count_notifications(
        self,
        profile_id: int | None = None,
        status: str | None = None,
        include_dismissed: bool = False,
        user_id: int | None = None,
    ) -> int:
        """Count notifications matching the given filters."""
        query = select(func.count(Notification.id))

        if user_id is not None:
            query = query.where(Notification.user_id == user_id)

        if profile_id is not None:
            query = query.where(Notification.profile_id == profile_id)

        if status is not None:
            query = query.where(Notification.status == status)
        elif not include_dismissed:
            query = query.where(Notification.status != NotificationStatus.DISMISSED)

        result = await self.session.execute(query)
        count = result.scalar()
        return count or 0

    async def get_unread_count(
        self, profile_id: int | None = None, user_id: int | None = None
    ) -> int:
        """Count unread notifications, optionally filtered by profile and user."""
        query = select(func.count(Notification.id)).where(
            Notification.status == NotificationStatus.UNREAD
        )
        if user_id is not None:
            query = query.where(Notification.user_id == user_id)
        if profile_id is not None:
            query = query.where(Notification.profile_id == profile_id)

        result = await self.session.execute(query)
        count = result.scalar()
        return count or 0

    async def mark_as_read(
        self, notification_id: int, user_id: int | None = None
    ) -> Notification | None:
        """Transition notification from UNREAD to READ with read_at timestamp (idempotent)."""
        notif = await self.get_by_id_and_user(notification_id, user_id=user_id)
        if not notif:
            return None

        if notif.status == NotificationStatus.UNREAD:
            notif.status = NotificationStatus.READ
            notif.read_at = datetime.now(UTC)
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def mark_as_unread(
        self, notification_id: int, user_id: int | None = None
    ) -> Notification | None:
        """Transition notification from READ to UNREAD, resetting read_at timestamp (idempotent)."""
        notif = await self.get_by_id_and_user(notification_id, user_id=user_id)
        if not notif:
            return None

        if notif.status == NotificationStatus.READ:
            notif.status = NotificationStatus.UNREAD
            notif.read_at = None
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def dismiss(
        self, notification_id: int, user_id: int | None = None
    ) -> Notification | None:
        """Transition notification from UNREAD/READ to DISMISSED (idempotent)."""
        notif = await self.get_by_id_and_user(notification_id, user_id=user_id)
        if not notif:
            return None

        if notif.status in (NotificationStatus.UNREAD, NotificationStatus.READ):
            notif.status = NotificationStatus.DISMISSED
            notif.dismissed_at = datetime.now(UTC)
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def restore(
        self, notification_id: int, user_id: int | None = None
    ) -> Notification | None:
        """Restore a dismissed notification back to READ (if previously read) or UNREAD."""
        notif = await self.get_by_id_and_user(notification_id, user_id=user_id)
        if not notif:
            return None

        if notif.status == NotificationStatus.DISMISSED:
            notif.status = (
                NotificationStatus.READ if notif.read_at is not None else NotificationStatus.UNREAD
            )
            notif.dismissed_at = None
            self.session.add(notif)
            await self.session.flush()
            await self.session.refresh(notif)
        return notif

    async def mark_batch_read(
        self, notification_ids: list[int], user_id: int | None = None
    ) -> int:
        """Batch transition multiple notifications from UNREAD to READ scoped to user."""
        if not notification_ids:
            return 0

        now = datetime.now(UTC)
        stmt = update(Notification).where(
            Notification.id.in_(notification_ids),
            Notification.status == NotificationStatus.UNREAD,
        )
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)

        stmt = stmt.values(status=NotificationStatus.READ, read_at=now)
        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return int(rowcount)

    async def dismiss_batch(
        self, notification_ids: list[int], user_id: int | None = None
    ) -> int:
        """Batch transition multiple notifications to DISMISSED scoped to user."""
        if not notification_ids:
            return 0

        now = datetime.now(UTC)
        stmt = update(Notification).where(
            Notification.id.in_(notification_ids),
            Notification.status.in_([NotificationStatus.UNREAD, NotificationStatus.READ]),
        )
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)

        stmt = stmt.values(status=NotificationStatus.DISMISSED, dismissed_at=now)
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
        types_to_match: list[str]
        if notification_type == NotificationType.JOB_ALERT:
            types_to_match = [NotificationType.JOB_ALERT, NotificationType.DESKTOP_TOAST]
        elif notification_type == NotificationType.DESKTOP_TOAST:
            types_to_match = [NotificationType.DESKTOP_TOAST, NotificationType.JOB_ALERT]
        else:
            types_to_match = [notification_type]

        query = select(Notification).where(
            Notification.job_id == job_id,
            Notification.notification_type.in_(types_to_match),
        )

        if profile_id is not None:
            query = query.where(Notification.profile_id == profile_id)
        else:
            query = query.where(Notification.profile_id.is_(None))

        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_job_user_type(
        self,
        job_id: int,
        user_id: int,
        notification_type: str = NotificationType.JOB_ALERT,
    ) -> Notification | None:
        """Retrieve existing notification matching (job_id, user_id, notification_type)."""
        types_to_match: list[str]
        if notification_type in (NotificationType.JOB_ALERT, NotificationType.DESKTOP_TOAST):
            types_to_match = [NotificationType.JOB_ALERT, NotificationType.DESKTOP_TOAST]
        else:
            types_to_match = [notification_type]

        query = select(Notification).where(
            Notification.job_id == job_id,
            Notification.user_id == user_id,
            Notification.notification_type.in_(types_to_match),
        )
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
