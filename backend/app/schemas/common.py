"""Common response schemas for HawkOffer REST API."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard pagination wrapper for list endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    total: int = Field(..., ge=0, description="Total matching items count")
    limit: int = Field(..., ge=1, description="Page item limit")
    offset: int = Field(..., ge=0, description="Page item offset")


class MessageResponse(BaseModel):
    """Standard message response schema."""

    message: str
    detail: str | None = None


class BatchActionRequest(BaseModel):
    """Request payload for batch operations on IDs."""

    ids: list[int] = Field(..., min_length=1, max_length=500, description="List of resource IDs")


class BatchActionResponse(BaseModel):
    """Response payload for batch operations."""

    affected_count: int = Field(..., ge=0, description="Number of items affected")
    message: str


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(..., description="Overall health status: healthy / degraded / unhealthy")
    database: str = Field(..., description="Database connectivity status: connected / disconnected")
    scheduler: str = Field(..., description="Scheduler runtime status: running / stopped")
    timestamp: datetime = Field(..., description="Current server UTC timestamp")


class SystemStatusResponse(BaseModel):
    """Comprehensive system status response schema."""

    app_name: str
    env: str
    is_running: bool
    is_lifecycle_gate_open: bool
    companies_count: int
    active_companies_count: int
    total_jobs_count: int
    unread_notifications_count: int
    pipeline_status: dict[str, Any]
    timestamp: datetime


class ScanTriggerResponse(BaseModel):
    """Response payload when a company scan is triggered."""

    company_id: int
    status: str
    message: str
    stats: dict[str, Any] | None = None


class WorkerTriggerResponse(BaseModel):
    """Response payload when a background pipeline worker is manually triggered."""

    worker_name: str
    status: str
    message: str
    result: dict[str, Any] | None = None
    timestamp: datetime


class UnreadCountResponse(BaseModel):
    """Response payload for unread notifications count."""

    unread_count: int = Field(..., ge=0, description="Total unread notifications count")
    profile_id: int | None = Field(None, description="Filtered profile ID, if applicable")


class MonitoringStatusResponse(BaseModel):
    """Scheduler and pipeline status response."""

    is_running: bool
    is_lifecycle_gate_open: bool
    pipeline_status: dict[str, Any]
    timestamp: datetime


class PauseResumeRequest(BaseModel):
    """Payload for pause/resume actions."""

    job_name: str | None = Field(
        None,
        description="Pipeline worker job name ('discovery', 'reevaluation', 'backlog_drain')",
    )
    company_id: int | None = Field(None, description="Company ID to pause or resume")
