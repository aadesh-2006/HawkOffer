"""Pydantic schemas for Company."""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Company name")
    career_url: str = Field(..., min_length=1, max_length=2048, description="URL of career page")
    platform: str = Field(
        default="generic",
        max_length=50,
        description="Scraper platform (greenhouse, lever, ashby, workday, generic)",
    )
    is_active: bool = Field(default=True, description="Whether monitoring is active")

    @field_validator("career_url")
    @classmethod
    def validate_career_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("career_url must be a valid HTTP or HTTPS URL")
        return v


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    career_url: str | None = Field(None, min_length=1, max_length=2048)
    platform: str | None = Field(None, max_length=50)
    is_active: bool | None = None

    @field_validator("career_url")
    @classmethod
    def validate_career_url(cls, v: str | None) -> str | None:
        if v is not None:
            parsed = urlparse(v)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("career_url must be a valid HTTP or HTTPS URL")
        return v


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
