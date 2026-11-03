"""Pydantic schemas for User entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    """Base schema for User attributes."""

    email: str = Field(..., max_length=255, description="User email address")
    full_name: str | None = None
    role: str = "user"
    is_active: bool = True


class UserCreate(UserBase):
    """Schema for creating a new User."""

    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    """Schema for updating an existing User."""

    email: str | None = Field(default=None, max_length=255)
    full_name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    is_active: bool | None = None


class UserRead(UserBase):
    """Schema for serializing a User."""

    id: int
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
