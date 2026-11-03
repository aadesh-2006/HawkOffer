"""Pydantic schemas for PushSubscription entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PushSubscriptionCreate(BaseModel):
    """Schema for registering a client Web Push subscription."""

    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None = None


class PushSubscriptionRead(BaseModel):
    """Schema for serializing a stored push subscription."""

    id: int
    user_id: int
    endpoint: str
    failure_count: int
    created_at: datetime
    last_used_at: datetime

    model_config = ConfigDict(from_attributes=True)
