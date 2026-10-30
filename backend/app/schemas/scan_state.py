"""Pydantic schemas for CompanyScanState."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScanStateBase(BaseModel):
    company_id: int
    scan_interval_minutes: int = Field(default=30, ge=1, le=1440)
    status: str = Field(
        default="IDLE",
        max_length=50,
        description="Current state: IDLE, RUNNING, FAILED, RATE_LIMITED",
    )
    last_scan_started_at: datetime | None = None
    last_scan_completed_at: datetime | None = None
    next_scan_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: str | None = None
    total_scans: int = Field(default=0, ge=0)
    total_errors: int = Field(default=0, ge=0)


class ScanStateCreate(ScanStateBase):
    pass


class ScanStateUpdate(BaseModel):
    scan_interval_minutes: int | None = Field(None, ge=1, le=1440)
    status: str | None = Field(None, max_length=50)
    last_scan_started_at: datetime | None = None
    last_scan_completed_at: datetime | None = None
    next_scan_at: datetime | None = None
    consecutive_failures: int | None = Field(None, ge=0)
    last_error: str | None = None
    total_scans: int | None = Field(None, ge=0)
    total_errors: int | None = Field(None, ge=0)


class ScanStateRead(ScanStateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
