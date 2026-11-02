"""API router for Scheduler and Background Pipeline Monitoring."""

import dataclasses
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_orchestrator
from app.scheduler.orchestrator import (
    BACKLOG_DRAIN_JOB_ID,
    DISCOVERY_JOB_ID,
    REEVALUATION_JOB_ID,
    MonitoringOrchestrator,
)
from app.schemas.common import (
    MessageResponse,
    MonitoringStatusResponse,
    PauseResumeRequest,
    WorkerTriggerResponse,
)

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

JOB_NAME_ALIASES: dict[str, str] = {
    "discovery": DISCOVERY_JOB_ID,
    "job_discovery": DISCOVERY_JOB_ID,
    "reevaluation": REEVALUATION_JOB_ID,
    "profile_reevaluation": REEVALUATION_JOB_ID,
    "drain": BACKLOG_DRAIN_JOB_ID,
    "backlog_drain": BACKLOG_DRAIN_JOB_ID,
}


@router.get("/status", response_model=MonitoringStatusResponse)
async def get_monitoring_status(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
) -> MonitoringStatusResponse:
    """Retrieve runtime status of scheduler, company scans, and M5 pipeline jobs."""
    if not orchestrator:
        return MonitoringStatusResponse(
            is_running=False,
            is_lifecycle_gate_open=False,
            pipeline_status={},
            timestamp=datetime.now(UTC),
        )

    is_running = bool(orchestrator.scheduler.running)
    is_open = not orchestrator._is_shutting_down and not orchestrator._is_starting
    pipeline_status = orchestrator.get_pipeline_status() if is_running else {}

    return MonitoringStatusResponse(
        is_running=is_running,
        is_lifecycle_gate_open=is_open,
        pipeline_status=pipeline_status,
        timestamp=datetime.now(UTC),
    )


@router.post("/discovery/trigger", response_model=WorkerTriggerResponse)
async def trigger_discovery(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
) -> WorkerTriggerResponse:
    """Manually trigger an immediate JobDiscoveryWorker outbox processing run."""
    if not orchestrator or not orchestrator.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    result = await orchestrator.trigger_discovery_now()
    now = datetime.now(UTC)
    if result is None:
        return WorkerTriggerResponse(
            worker_name="job_discovery",
            status="rejected",
            message="Discovery trigger was rejected because orchestrator is shutting down",
            result=None,
            timestamp=now,
        )

    return WorkerTriggerResponse(
        worker_name="job_discovery",
        status="completed",
        message="Discovery worker executed successfully",
        result=dataclasses.asdict(result),
        timestamp=now,
    )


@router.post("/reevaluation/trigger", response_model=WorkerTriggerResponse)
async def trigger_reevaluation(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
) -> WorkerTriggerResponse:
    """Manually trigger an immediate ProfileReevaluationWorker backlog generation run."""
    if not orchestrator or not orchestrator.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    result = await orchestrator.trigger_reevaluation_now()
    now = datetime.now(UTC)
    if result is None:
        return WorkerTriggerResponse(
            worker_name="profile_reevaluation",
            status="rejected",
            message="Reevaluation trigger was rejected because orchestrator is shutting down",
            result=None,
            timestamp=now,
        )

    return WorkerTriggerResponse(
        worker_name="profile_reevaluation",
        status="completed",
        message="Profile reevaluation worker executed successfully",
        result=dataclasses.asdict(result),
        timestamp=now,
    )


@router.post("/drain/trigger", response_model=WorkerTriggerResponse)
async def trigger_drain(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
) -> WorkerTriggerResponse:
    """Manually trigger an immediate BacklogDrainWorker notification promotion run."""
    if not orchestrator or not orchestrator.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    result = await orchestrator.trigger_backlog_drain_now()
    now = datetime.now(UTC)
    if result is None:
        return WorkerTriggerResponse(
            worker_name="backlog_drain",
            status="rejected",
            message="Backlog drain trigger was rejected because orchestrator is shutting down",
            result=None,
            timestamp=now,
        )

    return WorkerTriggerResponse(
        worker_name="backlog_drain",
        status="completed",
        message="Backlog drain worker executed successfully",
        result=dataclasses.asdict(result),
        timestamp=now,
    )


@router.post("/pause", response_model=MessageResponse)
async def pause_monitoring(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
    payload: PauseResumeRequest | None = None,
    job_name: Annotated[
        str | None,
        Query(description="Worker name: 'discovery', 'reevaluation', 'backlog_drain'"),
    ] = None,
    company_id: Annotated[int | None, Query(description="Company ID to pause scan for")] = None,
) -> MessageResponse:
    """Pause a pipeline background job or a scheduled company scan."""
    if not orchestrator or not orchestrator.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    target_job = payload.job_name if payload and payload.job_name else job_name
    target_company = payload.company_id if payload and payload.company_id else company_id

    if not target_job and target_company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'job_name' or 'company_id' must be specified to pause",
        )

    if target_company is not None:
        success = orchestrator.pause_company(target_company)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company scan job for company_id={target_company} not found",
            )
        return MessageResponse(
            message=f"Scheduled scan for company {target_company} paused successfully"
        )

    if target_job is not None:
        canonical_id = JOB_NAME_ALIASES.get(target_job.lower())
        if not canonical_id:
            valid_names = ", ".join(sorted(JOB_NAME_ALIASES.keys()))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown job_name '{target_job}'. Valid options: {valid_names}",
            )
        success = orchestrator.pause_pipeline_job(canonical_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Pipeline job '{target_job}' ({canonical_id}) is not registered in scheduler"
                ),
            )
        return MessageResponse(message=f"Pipeline job '{target_job}' paused successfully")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target")


@router.post("/resume", response_model=MessageResponse)
async def resume_monitoring(
    orchestrator: Annotated[MonitoringOrchestrator | None, Depends(get_orchestrator)],
    payload: PauseResumeRequest | None = None,
    job_name: Annotated[
        str | None,
        Query(description="Worker name: 'discovery', 'reevaluation', 'backlog_drain'"),
    ] = None,
    company_id: Annotated[int | None, Query(description="Company ID to resume scan for")] = None,
) -> MessageResponse:
    """Resume a paused pipeline background job or a scheduled company scan."""
    if not orchestrator or not orchestrator.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler orchestrator is not running",
        )

    target_job = payload.job_name if payload and payload.job_name else job_name
    target_company = payload.company_id if payload and payload.company_id else company_id

    if not target_job and target_company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'job_name' or 'company_id' must be specified to resume",
        )

    if target_company is not None:
        success = orchestrator.resume_company(target_company)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company scan job for company_id={target_company} not found",
            )
        return MessageResponse(
            message=f"Scheduled scan for company {target_company} resumed successfully"
        )

    if target_job is not None:
        canonical_id = JOB_NAME_ALIASES.get(target_job.lower())
        if not canonical_id:
            valid_names = ", ".join(sorted(JOB_NAME_ALIASES.keys()))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown job_name '{target_job}'. Valid options: {valid_names}",
            )
        success = orchestrator.resume_pipeline_job(canonical_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Pipeline job '{target_job}' ({canonical_id}) is not registered in scheduler"
                ),
            )
        return MessageResponse(message=f"Pipeline job '{target_job}' resumed successfully")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target")
