"""Pydantic schemas for Notification."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NotificationStatus(StrEnum):
    """User-facing lifecycle status."""

    UNREAD = "UNREAD"
    READ = "READ"
    DISMISSED = "DISMISSED"


class DeliveryStatus(StrEnum):
    """External delivery dispatch status."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


class NotificationType(StrEnum):
    """Supported notification categories and delivery channels."""

    JOB_ALERT = "job_alert"
    DESKTOP_TOAST = "desktop_toast"
    EMAIL = "email"


class NotificationBase(BaseModel):
    job_id: int
    profile_id: int | None = None
    notification_type: str = Field(default=NotificationType.JOB_ALERT, max_length=50)
    status: NotificationStatus | str = Field(default=NotificationStatus.UNREAD, max_length=50)
    delivery_status: DeliveryStatus | str = Field(default=DeliveryStatus.PENDING, max_length=50)
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    delivered_at: datetime | None = None


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(BaseModel):
    status: NotificationStatus | str | None = Field(None, max_length=50)
    delivery_status: DeliveryStatus | str | None = Field(None, max_length=50)
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    delivered_at: datetime | None = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    profile_id: int | None
    notification_type: str
    status: str
    delivery_status: str
    created_at: datetime
    read_at: datetime | None
    dismissed_at: datetime | None
    delivered_at: datetime | None
