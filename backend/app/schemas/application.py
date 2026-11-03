"""Pydantic schemas for JobApplication tracking entity."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job import JobRead


class ApplicationBase(BaseModel):
    """Base schema for job application status tracking."""

    status: str = Field(default="SAVED")  # SAVED, APPLIED, INTERVIEWING, OFFER, REJECTED
    notes: str | None = None
    applied_at: datetime | None = None


class ApplicationCreate(ApplicationBase):
    """Schema for creating a new job application record."""

    job_id: int


class ApplicationUpdate(BaseModel):
    """Schema for updating an existing job application status or notes."""

    status: str | None = None
    notes: str | None = None
    applied_at: datetime | None = None


class ApplicationRead(ApplicationBase):
    """Schema for serializing a job application."""

    id: int
    user_id: int
    job_id: int
    created_at: datetime
    updated_at: datetime
    job: JobRead | None = None

    model_config = ConfigDict(from_attributes=True)
