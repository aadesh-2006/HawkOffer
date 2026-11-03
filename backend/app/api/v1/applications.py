"""Job application tracker API endpoints strictly isolated to the authenticated user."""

import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db
from app.models.application import JobApplication
from app.models.job import Job
from app.models.user import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from app.schemas.common import MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Applications"])


@router.get(
    "",
    response_model=list[ApplicationRead],
    summary="List all tracked job applications for current user",
)
async def list_applications(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> Sequence[JobApplication]:
    """Return all job applications belonging strictly to the authenticated user."""
    stmt = (
        select(JobApplication)
        .where(JobApplication.user_id == current_user.id)
        .order_by(JobApplication.created_at.desc())
    )
    res = await session.execute(stmt)
    return res.scalars().all()


@router.post(
    "",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Track a job application for current user",
)
async def create_application(
    payload: ApplicationCreate,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> JobApplication:
    """Create a new job application tracking record scoped to the authenticated user."""
    # Verify target job exists
    job_res = await session.execute(select(Job.id).where(Job.id == payload.job_id))
    if job_res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {payload.job_id} not found",
        )

    # Check for duplicate
    existing_res = await session.execute(
        select(JobApplication).where(
            JobApplication.user_id == current_user.id,
            JobApplication.job_id == payload.job_id,
        )
    )
    if existing_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already tracking an application for this job",
        )

    app_record = JobApplication(
        user_id=current_user.id,
        job_id=payload.job_id,
        status=payload.status,
        notes=payload.notes,
        applied_at=payload.applied_at,
    )
    session.add(app_record)
    try:
        await session.commit()
        await session.refresh(app_record)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate application tracking record",
        ) from None

    return app_record


@router.get(
    "/{application_id}",
    response_model=ApplicationRead,
    summary="Get application details by ID (tenant-scoped)",
)
async def get_application(
    application_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> JobApplication:
    """Retrieve an application tracking record by ID, verifying tenant ownership."""
    stmt = select(JobApplication).where(
        JobApplication.id == application_id,
        JobApplication.user_id == current_user.id,
    )
    res = await session.execute(stmt)
    app_record = res.scalar_one_or_none()
    if app_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application {application_id} not found",
        )
    return app_record


@router.patch(
    "/{application_id}",
    response_model=ApplicationRead,
    summary="Update application status or notes (tenant-scoped)",
)
async def update_application(
    application_id: int,
    payload: ApplicationUpdate,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> JobApplication:
    """Update an application status or notes, verifying tenant ownership."""
    stmt = select(JobApplication).where(
        JobApplication.id == application_id,
        JobApplication.user_id == current_user.id,
    )
    res = await session.execute(stmt)
    app_record = res.scalar_one_or_none()
    if app_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application {application_id} not found",
        )

    if payload.status is not None:
        app_record.status = payload.status
    if payload.notes is not None:
        app_record.notes = payload.notes
    if payload.applied_at is not None:
        app_record.applied_at = payload.applied_at

    await session.commit()
    await session.refresh(app_record)
    return app_record


@router.delete(
    "/{application_id}",
    response_model=MessageResponse,
    summary="Delete application tracking record (tenant-scoped)",
)
async def delete_application(
    application_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Delete an application tracking record, verifying tenant ownership."""
    stmt = select(JobApplication).where(
        JobApplication.id == application_id,
        JobApplication.user_id == current_user.id,
    )
    res = await session.execute(stmt)
    app_record = res.scalar_one_or_none()
    if app_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application {application_id} not found",
        )

    await session.delete(app_record)
    await session.commit()
    return MessageResponse(message=f"Application {application_id} deleted successfully")
