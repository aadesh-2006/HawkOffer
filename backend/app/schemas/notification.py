"""Pydantic schemas for Notification."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationBase(BaseModel):
    job_id: int
    profile_id: int | None = None
    notification_type: str = Field(default="desktop_toast", max_length=50)
    status: str = Field(default="PENDING", max_length=50)
    delivered_at: datetime | None = None


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(BaseModel):
    status: str | None = Field(None, max_length=50)
    delivered_at: datetime | None = None


class NotificationRead(NotificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
