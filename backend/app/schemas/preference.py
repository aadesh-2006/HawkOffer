"""Pydantic schemas for PreferenceProfile."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PreferenceProfileBase(BaseModel):
    name: str = Field(default="Default Profile", min_length=1, max_length=255)
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    minimum_match_score: float = Field(default=0.0, ge=0.0, le=100.0)
    is_active: bool = Field(default=True)


class PreferenceProfileCreate(PreferenceProfileBase):
    pass


class PreferenceProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    preferred_roles: list[str] | None = None
    preferred_locations: list[str] | None = None
    preferred_skills: list[str] | None = None
    minimum_match_score: float | None = Field(None, ge=0.0, le=100.0)
    is_active: bool | None = None


class PreferenceProfileRead(PreferenceProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
