"""Preference profile and eligibility rules API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_session_factory
from app.models.preference import PreferenceProfile
from app.repositories.preference_repo import PreferenceProfileRepository
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.preference import (
    PreferenceProfileCreate,
    PreferenceProfileRead,
    PreferenceProfileUpdate,
)
from app.services.profile_service import ProfileUpdateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["Preference Profiles"])


@router.post(
    "",
    response_model=PreferenceProfileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create preference profile",
    description="Creates a new preference profile with keywords, roles, and match thresholds.",
)
async def create_profile(
    payload: PreferenceProfileCreate,
    session: AsyncSession = Depends(get_db),
) -> PreferenceProfileRead:
    """Create a new preference profile."""
    repo = PreferenceProfileRepository(session)
    profile = await repo.create(payload)
    return PreferenceProfileRead.model_validate(profile)


@router.get(
    "",
    response_model=PaginatedResponse[PreferenceProfileRead],
    status_code=status.HTTP_200_OK,
    summary="List preference profiles",
    description="Returns a paginated list of preference profiles.",
)
async def list_profiles(
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PreferenceProfileRead]:
    """List preference profiles with pagination."""
    repo = PreferenceProfileRepository(session)
    items = await repo.list_all(skip=offset, limit=limit)
    total = (
        await session.execute(select(func.count()).select_from(PreferenceProfile))
    ).scalar() or 0

    return PaginatedResponse[PreferenceProfileRead](
        items=[PreferenceProfileRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{profile_id}",
    response_model=PreferenceProfileRead,
    status_code=status.HTTP_200_OK,
    summary="Get preference profile details",
    description="Retrieves a specific preference profile including current revision.",
)
async def get_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_db),
) -> PreferenceProfileRead:
    """Retrieve preference profile details by primary key."""
    repo = PreferenceProfileRepository(session)
    profile = await repo.get_by_id(profile_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preference profile with ID {profile_id} was not found",
        )
    return PreferenceProfileRead.model_validate(profile)


@router.patch(
    "/{profile_id}",
    response_model=PreferenceProfileRead,
    status_code=status.HTTP_200_OK,
    summary="Update preference profile",
    description=(
        "Updates filtering preferences. Meaningful preference changes automatically increment "
        "profile revision, invalidate obsolete queued backlog items, and enqueue a historical "
        "reevaluation task."
    ),
)
async def update_profile(
    profile_id: int,
    payload: PreferenceProfileUpdate,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> PreferenceProfileRead:
    """Update profile preferences through ProfileUpdateService to enforce M5 revision semantics."""
    service = ProfileUpdateService(session_factory=session_factory)
    try:
        updated_profile, task = await service.update_profile(profile_id, payload)
    except Exception as exc:
        logger.error("Failed to update profile %d: %s", profile_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update profile: {exc}",
        ) from exc

    if not updated_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preference profile with ID {profile_id} was not found",
        )

    if task:
        logger.info(
            "Profile %d updated to revision %d; enqueued historical reevaluation task %d",
            profile_id,
            updated_profile.revision,
            task.id,
        )

    return PreferenceProfileRead.model_validate(updated_profile)


@router.post(
    "/{profile_id}/activate",
    response_model=PreferenceProfileRead,
    status_code=status.HTTP_200_OK,
    summary="Activate preference profile",
    description="Activates a preference profile for job filtering and notification generation.",
)
async def activate_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_db),
) -> PreferenceProfileRead:
    """Activate a preference profile."""
    repo = PreferenceProfileRepository(session)
    updated = await repo.update(profile_id, {"is_active": True})
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preference profile with ID {profile_id} was not found",
        )
    return PreferenceProfileRead.model_validate(updated)


@router.post(
    "/{profile_id}/deactivate",
    response_model=PreferenceProfileRead,
    status_code=status.HTTP_200_OK,
    summary="Deactivate preference profile",
    description="Deactivates a preference profile. Inactive profiles are skipped during discovery.",
)
async def deactivate_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_db),
) -> PreferenceProfileRead:
    """Deactivate a preference profile."""
    repo = PreferenceProfileRepository(session)
    updated = await repo.update(profile_id, {"is_active": False})
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preference profile with ID {profile_id} was not found",
        )
    return PreferenceProfileRead.model_validate(updated)


@router.delete(
    "/{profile_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete preference profile",
    description="Deletes a preference profile and cascades to associated backlog candidates.",
)
async def delete_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Delete a preference profile."""
    repo = PreferenceProfileRepository(session)
    deleted = await repo.delete(profile_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preference profile with ID {profile_id} was not found",
        )
    return MessageResponse(message=f"Preference profile {profile_id} deleted successfully")
