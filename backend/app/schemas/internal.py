"""Pydantic schemas for internal and scheduled cron operations."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class CronMonitoringResponse(BaseModel):
    """Execution telemetry for a centralized scheduled monitoring cycle."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str = Field(description="Run outcome status: completed, partial_failure, or failed")
    message: str = Field(description="Human-readable summary message")
    duration_ms: int = Field(description="Total cycle execution time in milliseconds")
    companies_targeted: int = Field(description="Number of eligible companies targeted")
    companies_scraped: int = Field(description="Number of companies successfully scraped")
    companies_failed: int = Field(description="Number of company scans that failed")
    jobs_new: int = Field(description="Newly inserted jobs discovered during cycle")
    jobs_updated: int = Field(description="Existing jobs updated with modified content")
    jobs_existing: int = Field(description="Existing jobs verified with unchanged content")
    discovery_evaluated: int = Field(description="Total jobs evaluated against subscriber profiles")
    candidates_created: int = Field(description="Backlog candidates generated for subscribers")
    notifications_promoted: int = Field(description="User notifications promoted into outbox")
    pushes_attempted: int = Field(default=0, description="Web Push messages attempted")
    pushes_delivered: int = Field(default=0, description="Web Push messages successfully delivered")
    errors: list[str] = Field(default_factory=list, description="Diagnostic error messages")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
