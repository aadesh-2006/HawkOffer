"""Internal administrative and headless cron API endpoints."""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.deps import get_orchestrator
from app.core.config import settings
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.internal import CronMonitoringResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal Cron"])


def verify_cron_secret(
    x_cron_secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> bool:
    """Authenticate internal cron triggers using a dedicated server-side secret.

    Accepts credentials via:
    1. 'X-Cron-Secret: <secret>' header
    2. 'Authorization: Bearer <secret>' header

    Validates using constant-time comparison. Rejects weak secrets in production.
    Never logs secret content.
    """
    if settings.is_production:
        is_valid, reason = settings.validate_production_cron_secret()
        if not is_valid:
            logger.error("Cron endpoint rejected: %s", reason)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Cron endpoint is disabled: {reason}",
            )

    secret = settings.cron_secret
    if not secret:
        logger.error("Cron authentication rejected: HAWKOFFER_CRON_SECRET not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cron secret is not configured on the server",
        )

    provided: str | None = None
    if x_cron_secret:
        provided = x_cron_secret.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not secrets.compare_digest(provided, secret):
        logger.warning("Rejected cron trigger with invalid or missing credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing cron authentication credentials",
        )

    return True


@router.post(
    "/cron/monitor",
    response_model=CronMonitoringResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger scheduled headless monitoring run",
    description=(
        "Executes a centralized multi-tenant monitoring cycle: scrapes subscribed "
        "active companies, fans out newly discovered jobs to subscribed user profiles, "
        "and promotes candidates into user-scoped notifications. Protected by dedicated "
        "server-side cron secret."
    ),
)
async def trigger_cron_monitor(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
    authenticated: Annotated[bool, Depends(verify_cron_secret)],
) -> CronMonitoringResponse:
    """Execute scheduled headless monitoring cycle with cron authentication."""
    if not orchestrator:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    result = await orchestrator.run_centralized_monitoring_cycle()
    if result.status == "skipped_overlapping":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A monitoring cycle is currently in progress",
        )

    return CronMonitoringResponse(
        status=result.status,
        message=result.message,
        duration_ms=result.duration_ms,
        companies_targeted=result.companies_targeted,
        companies_scraped=result.companies_scraped,
        companies_failed=result.companies_failed,
        jobs_new=result.jobs_new,
        jobs_updated=result.jobs_updated,
        jobs_existing=result.jobs_existing,
        discovery_evaluated=result.discovery_evaluated,
        candidates_created=result.candidates_created,
        notifications_promoted=result.notifications_promoted,
        errors=result.errors,
        timestamp=datetime.now(UTC),
    )
