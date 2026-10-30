"""Pydantic schemas for MonitoringLog."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MonitoringLogBase(BaseModel):
    company_id: int
    status: str = Field(
        ...,
        max_length=50,
        description="SUCCESS, PARTIAL, FAILED, RATE_LIMITED, SKIPPED",
    )
    jobs_found: int = Field(default=0, ge=0)
    error_message: str | None = None
    execution_time_ms: int = Field(default=0, ge=0)


class MonitoringLogCreate(MonitoringLogBase):
    pass


class MonitoringLogRead(MonitoringLogBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
