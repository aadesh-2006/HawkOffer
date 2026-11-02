"""API router for Notification resources."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.repositories.notification_repo import NotificationRepository
from app.schemas.common import (
    BatchActionRequest,
    BatchActionResponse,
    PaginatedResponse,
    UnreadCountResponse,
)
from app.schemas.notification import NotificationRead

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=PaginatedResponse[NotificationRead])
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    profile_id: Annotated[int | None, Query(description="Filter by preference profile ID")] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="Filter by status (UNREAD, READ, DISMISSED)")
    ] = None,
    include_dismissed: Annotated[
        bool, Query(description="Include dismissed notifications when no status is given")
    ] = False,
    limit: Annotated[int, Query(description="Max items to return", ge=1, le=500)] = 50,
    offset: Annotated[int, Query(description="Pagination offset", ge=0)] = 0,
) -> PaginatedResponse[NotificationRead]:
    """Retrieve paginated notifications with optional profile and status filtering."""
    repo = NotificationRepository(db)
    items = await repo.list_notifications(
        profile_id=profile_id,
        status=status_filter,
        include_dismissed=include_dismissed,
        limit=limit,
        offset=offset,
    )
    total = await repo.count_notifications(
        profile_id=profile_id,
        status=status_filter,
        include_dismissed=include_dismissed,
    )
    return PaginatedResponse[NotificationRead](
        items=[NotificationRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: Annotated[AsyncSession, Depends(get_db)],
    profile_id: Annotated[int | None, Query(description="Filter by profile ID")] = None,
) -> UnreadCountResponse:
    """Get the current count of unread notifications."""
    repo = NotificationRepository(db)
    count = await repo.get_unread_count(profile_id=profile_id)
    return UnreadCountResponse(unread_count=count, profile_id=profile_id)


@router.post("/batch/read", response_model=BatchActionResponse)
async def batch_mark_read(
    payload: BatchActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BatchActionResponse:
    """Mark multiple notifications as READ in a single operation."""
    repo = NotificationRepository(db)
    count = await repo.mark_batch_read(payload.ids)
    await db.commit()
    return BatchActionResponse(
        affected_count=count,
        message=f"Marked {count} notifications as read",
    )


@router.post("/batch/dismiss", response_model=BatchActionResponse)
async def batch_dismiss(
    payload: BatchActionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BatchActionResponse:
    """Batch dismiss multiple notifications in a single operation."""
    repo = NotificationRepository(db)
    count = await repo.dismiss_batch(payload.ids)
    await db.commit()
    return BatchActionResponse(
        affected_count=count,
        message=f"Dismissed {count} notifications",
    )


@router.get("/{notification_id}", response_model=NotificationRead)
async def get_notification(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationRead:
    """Retrieve full details of a notification by its ID."""
    repo = NotificationRepository(db)
    item = await repo.get_by_id(notification_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with ID {notification_id} not found",
        )
    return NotificationRead.model_validate(item)


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationRead:
    """Mark a notification as READ."""
    repo = NotificationRepository(db)
    item = await repo.mark_as_read(notification_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with ID {notification_id} not found",
        )
    await db.commit()
    return NotificationRead.model_validate(item)


@router.post("/{notification_id}/unread", response_model=NotificationRead)
async def mark_notification_unread(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationRead:
    """Mark a notification as UNREAD."""
    repo = NotificationRepository(db)
    item = await repo.mark_as_unread(notification_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with ID {notification_id} not found",
        )
    await db.commit()
    return NotificationRead.model_validate(item)


@router.post("/{notification_id}/dismiss", response_model=NotificationRead)
async def dismiss_notification(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationRead:
    """Dismiss a notification."""
    repo = NotificationRepository(db)
    item = await repo.dismiss(notification_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with ID {notification_id} not found",
        )
    await db.commit()
    return NotificationRead.model_validate(item)


@router.post("/{notification_id}/restore", response_model=NotificationRead)
async def restore_notification(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationRead:
    """Restore a dismissed notification to READ/UNREAD."""
    repo = NotificationRepository(db)
    item = await repo.restore(notification_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with ID {notification_id} not found",
        )
    await db.commit()
    return NotificationRead.model_validate(item)
