"""System diagnostic and health check API endpoints."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_orchestrator
from app.core.config import settings
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.common import HealthResponse, SystemStatusResponse
from app.schemas.notification import NotificationStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["System"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Returns connectivity status of core application subsystems.",
)
async def health_check(
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> HealthResponse:
    """Perform health checks on database and background scheduler."""
    db_status = "connected"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        db_status = "disconnected"

    scheduler_running = bool(orchestrator and orchestrator.scheduler.running)
    scheduler_status = "running" if scheduler_running else "stopped"
    overall_status = "healthy" if db_status == "connected" and scheduler_running else "degraded"

    return HealthResponse(
        status=overall_status,
        database=db_status,
        scheduler=scheduler_status,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="System runtime status",
    description="Returns aggregate entity counts and detailed orchestrator pipeline metrics.",
)
async def system_status(
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> SystemStatusResponse:
    """Retrieve full system metrics and pipeline status."""
    # Count metrics
    comp_total = (await session.execute(select(func.count()).select_from(Company))).scalar() or 0
    comp_active = (
        await session.execute(
            select(func.count()).select_from(Company).where(Company.is_active.is_(True))
        )
    ).scalar() or 0
    jobs_total = (await session.execute(select(func.count()).select_from(Job))).scalar() or 0
    unread_notifs = (
        await session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.status == NotificationStatus.UNREAD)
        )
    ).scalar() or 0

    scheduler_running = bool(orchestrator and orchestrator.scheduler.running)
    is_open = bool(orchestrator and orchestrator.is_lifecycle_gate_open)
    pipeline_status = (
        orchestrator.get_pipeline_status()
        if (orchestrator and orchestrator.scheduler.running)
        else {}
    )

    return SystemStatusResponse(
        app_name=settings.app_name,
        env=settings.env,
        is_running=scheduler_running,
        is_lifecycle_gate_open=is_open,
        companies_count=comp_total,
        active_companies_count=comp_active,
        total_jobs_count=jobs_total,
        unread_notifications_count=unread_notifs,
        pipeline_status=pipeline_status,
        timestamp=datetime.now(UTC),
    )
