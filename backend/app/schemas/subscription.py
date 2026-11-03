"""Pydantic schemas for UserCompanySubscription entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubscriptionCreate(BaseModel):
    """Schema for subscribing a user to a company."""

    company_id: int


class SubscriptionRead(BaseModel):
    """Schema for serializing a user company subscription."""

    id: int
    user_id: int
    company_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
