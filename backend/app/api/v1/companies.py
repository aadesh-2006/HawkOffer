"""Company management and scan control API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_orchestrator
from app.repositories.company_repo import CompanyRepository
from app.repositories.scan_state_repo import ScanStateRepository
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.common import MessageResponse, PaginatedResponse, ScanTriggerResponse
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate
from app.schemas.scan_state import ScanStateRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["Companies"])


@router.post(
    "",
    response_model=CompanyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create company target",
    description="Registers a new company target for automated or manual monitoring.",
)
async def create_company(
    payload: CompanyCreate,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> CompanyRead:
    """Create a new company target and schedule it if active."""
    company_repo = CompanyRepository(session)
    existing = await company_repo.get_by_career_url(payload.career_url)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Company with career URL '{payload.career_url}' already exists (ID {existing.id})"
            ),
        )

    company = await company_repo.create(payload)

    # Initialize company scan state
    state_repo = ScanStateRepository(session)
    await state_repo.get_or_create(company.id)

    # Sync schedule if active
    if company.is_active and orchestrator:
        try:
            await orchestrator.sync_company_schedules()
        except Exception as exc:
            logger.warning(
                "Failed to sync schedules after creating company %d: %s", company.id, exc
            )

    return CompanyRead.model_validate(company)


@router.get(
    "",
    response_model=PaginatedResponse[CompanyRead],
    status_code=status.HTTP_200_OK,
    summary="List company targets",
    description="Returns a paginated list of configured companies with optional active filter.",
)
async def list_companies(
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[CompanyRead]:
    """List companies with pagination metadata."""
    company_repo = CompanyRepository(session)
    items = await company_repo.list_paginated(skip=offset, limit=limit, is_active=is_active)
    total = await company_repo.count_companies(is_active=is_active)

    return PaginatedResponse[CompanyRead](
        items=[CompanyRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{company_id}",
    response_model=CompanyRead,
    status_code=status.HTTP_200_OK,
    summary="Get company details",
    description="Retrieves configuration details for a single company.",
)
async def get_company(
    company_id: int,
    session: AsyncSession = Depends(get_db),
) -> CompanyRead:
    """Retrieve company details by primary key."""
    company_repo = CompanyRepository(session)
    company = await company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )
    return CompanyRead.model_validate(company)


@router.patch(
    "/{company_id}",
    response_model=CompanyRead,
    status_code=status.HTTP_200_OK,
    summary="Update company configuration",
    description="Updates settings for a company and synchronizes background scheduler jobs.",
)
async def update_company(
    company_id: int,
    payload: CompanyUpdate,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> CompanyRead:
    """Update company settings and refresh scheduler configuration."""
    company_repo = CompanyRepository(session)
    company = await company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    if payload.career_url and payload.career_url != company.career_url:
        existing = await company_repo.get_by_career_url(payload.career_url)
        if existing and existing.id != company_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Another company with URL '{payload.career_url}' "
                    f"already exists (ID {existing.id})"
                ),
            )

    updated = await company_repo.update(company_id, payload)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} could not be updated",
        )

    # Sync schedule changes
    if orchestrator:
        try:
            await orchestrator.sync_company_schedules()
        except Exception as exc:
            logger.warning(
                "Failed to sync schedules after updating company %d: %s", company_id, exc
            )

    return CompanyRead.model_validate(updated)


@router.post(
    "/{company_id}/activate",
    response_model=CompanyRead,
    status_code=status.HTTP_200_OK,
    summary="Activate company monitoring",
    description="Enables automated background scanning for a company.",
)
async def activate_company(
    company_id: int,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> CompanyRead:
    """Activate company monitoring."""
    company_repo = CompanyRepository(session)
    updated = await company_repo.update(company_id, {"is_active": True})
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    if orchestrator:
        try:
            await orchestrator.sync_company_schedules()
        except Exception as exc:
            logger.warning(
                "Failed to sync schedules after activating company %d: %s", company_id, exc
            )

    return CompanyRead.model_validate(updated)


@router.post(
    "/{company_id}/deactivate",
    response_model=CompanyRead,
    status_code=status.HTTP_200_OK,
    summary="Deactivate company monitoring",
    description="Disables automated background scanning for a company.",
)
async def deactivate_company(
    company_id: int,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> CompanyRead:
    """Deactivate company monitoring."""
    company_repo = CompanyRepository(session)
    updated = await company_repo.update(company_id, {"is_active": False})
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    if orchestrator:
        try:
            await orchestrator.sync_company_schedules()
        except Exception as exc:
            logger.warning(
                "Failed to sync schedules after deactivating company %d: %s", company_id, exc
            )

    return CompanyRead.model_validate(updated)


@router.post(
    "/{company_id}/trigger",
    response_model=ScanTriggerResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger immediate company scan",
    description="Runs an immediate on-demand scrape and ingestion pass for a company.",
)
async def trigger_company_scan(
    company_id: int,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> ScanTriggerResponse:
    """Execute an immediate out-of-band scan for a company target."""
    company_repo = CompanyRepository(session)
    company = await company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    if not orchestrator or not orchestrator.is_lifecycle_gate_open:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot trigger scan: monitoring orchestrator is not running or gate is closed",
        )

    result = await orchestrator.trigger_scan_now(company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Scan for company {company_id} is already in progress or could not be triggered"
            ),
        )

    return ScanTriggerResponse(
        company_id=company_id,
        status="completed",
        message=f"Scan completed for {company.name}",
        stats={
            "total_fetched": result.total_fetched,
            "newly_inserted": result.newly_inserted,
            "already_existing": result.already_existing,
            "updated_records": result.updated_records,
            "failed_records": result.failed_records,
            "duration_ms": result.duration_ms,
        },
    )


@router.get(
    "/{company_id}/scan-state",
    response_model=ScanStateRead,
    status_code=status.HTTP_200_OK,
    summary="Get company scan state",
    description="Retrieves telemetry, last run timestamp, and error metrics for a company target.",
)
async def get_company_scan_state(
    company_id: int,
    session: AsyncSession = Depends(get_db),
) -> ScanStateRead:
    """Retrieve persistent scan state metrics for a company."""
    company_repo = CompanyRepository(session)
    company = await company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    state_repo = ScanStateRepository(session)
    state = await state_repo.get_by_company_id(company_id)
    if not state:
        state = await state_repo.get_or_create(company_id)

    return ScanStateRead.model_validate(state)


@router.delete(
    "/{company_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete company target",
    description="Deletes a company and cleans up associated scheduler jobs.",
)
async def delete_company(
    company_id: int,
    session: AsyncSession = Depends(get_db),
    orchestrator: MonitoringOrchestrator | None = Depends(get_orchestrator),
) -> MessageResponse:
    """Delete a company target."""
    company_repo = CompanyRepository(session)
    deleted = await company_repo.delete(company_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} was not found",
        )

    if orchestrator:
        try:
            await orchestrator.sync_company_schedules()
        except Exception as exc:
            logger.warning(
                "Failed to sync schedules after deleting company %d: %s", company_id, exc
            )

    return MessageResponse(message=f"Company {company_id} deleted successfully")
