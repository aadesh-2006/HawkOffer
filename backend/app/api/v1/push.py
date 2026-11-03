"""Web Push subscription management and public VAPID endpoints."""

import logging
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db
from app.core.vapid import get_vapid_public_key
from app.database.base import utc_now
from app.models.push import PushSubscription
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.push import (
    PushSubscriptionCreate,
    PushSubscriptionRead,
    PushUnsubscribeRequest,
    VapidPublicKeyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["Web Push"])


def redact_endpoint(endpoint: str) -> str:
    """Redact sensitive push token from gateway endpoint for safe logging."""
    try:
        parsed = urlparse(endpoint)
        token_suffix = endpoint[-12:] if len(endpoint) > 12 else endpoint
        return f"{parsed.scheme}://{parsed.netloc}/...{token_suffix}"
    except Exception:
        return "[REDACTED_ENDPOINT]"


@router.get(
    "/vapid-public-key",
    response_model=VapidPublicKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get public VAPID key for browser push subscription",
    description="Returns the base64url-encoded P-256 public key for pushManager.subscribe().",
)
async def get_public_vapid_key() -> VapidPublicKeyResponse:
    """Return the server's public VAPID key. Never exposes private keys."""
    try:
        pub_key = get_vapid_public_key()
        return VapidPublicKeyResponse(public_key=pub_key)
    except Exception as exc:
        logger.error("Failed to retrieve public VAPID key: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VAPID Web Push is not configured on the server",
        ) from exc


@router.post(
    "/subscribe",
    response_model=PushSubscriptionRead,
    status_code=status.HTTP_200_OK,
    summary="Register or update a browser Web Push subscription",
    description=(
        "Registers a Web Push subscription endpoint and encryption keys for the "
        "authenticated user. Upserts idempotently for the same user; rejects takeover."
    ),
)
async def register_push_subscription(
    data: PushSubscriptionCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PushSubscription:
    """Register or refresh an RFC 8291 Web Push subscription for the active user."""
    now = utc_now()
    redacted = redact_endpoint(data.endpoint)

    # 1. Look up existing subscription by unique endpoint
    stmt = select(PushSubscription).where(PushSubscription.endpoint == data.endpoint)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        # Prevent cross-user endpoint hijacking
        if existing.user_id != current_user.id:
            logger.warning(
                "Cross-user push subscription takeover rejected: endpoint %s owned by user %d, "
                "attempted by user %d",
                redacted,
                existing.user_id,
                current_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Subscription endpoint is already registered by another account",
            )

        # Idempotent key update for the same user
        existing.p256dh = data.keys.p256dh
        existing.auth = data.keys.auth
        existing.user_agent = data.user_agent
        existing.failure_count = 0
        existing.last_used_at = now
        await session.commit()
        await session.refresh(existing)
        logger.info(
            "Updated Web Push subscription %d for user %d (device: %s, endpoint: %s)",
            existing.id,
            current_user.id,
            (data.user_agent or "unknown")[:30],
            redacted,
        )
        return existing

    # 2. Create new subscription with race-condition handling
    new_sub = PushSubscription(
        user_id=current_user.id,
        endpoint=data.endpoint,
        p256dh=data.keys.p256dh,
        auth=data.keys.auth,
        user_agent=data.user_agent,
        failure_count=0,
        created_at=now,
        last_used_at=now,
    )
    session.add(new_sub)

    try:
        await session.commit()
        await session.refresh(new_sub)
        logger.info(
            "Registered new Web Push subscription %d for user %d (endpoint: %s)",
            new_sub.id,
            current_user.id,
            redacted,
        )
        return new_sub
    except IntegrityError:
        # Concurrent duplicate registration race won by another request
        await session.rollback()
        conflict_res = await session.execute(stmt)
        winner = conflict_res.scalar_one_or_none()
        if winner:
            if winner.user_id != current_user.id:
                logger.warning(
                    "Concurrent push takeover rejected: endpoint %s owned by user %d, "
                    "attempted by user %d",
                    redacted,
                    winner.user_id,
                    current_user.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Subscription endpoint is already registered by another account",
                ) from None
            # Same user won the race; perform idempotent update
            winner.p256dh = data.keys.p256dh
            winner.auth = data.keys.auth
            winner.user_agent = data.user_agent
            winner.failure_count = 0
            winner.last_used_at = now
            await session.commit()
            await session.refresh(winner)
            return winner
        raise


@router.get(
    "/subscriptions",
    response_model=list[PushSubscriptionRead],
    status_code=status.HTTP_200_OK,
    summary="List all Web Push subscriptions for the current user",
)
async def list_push_subscriptions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[PushSubscription]:
    """Retrieve all active push subscriptions belonging strictly to the current user."""
    stmt = (
        select(PushSubscription)
        .where(PushSubscription.user_id == current_user.id)
        .order_by(PushSubscription.id.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.post(
    "/unsubscribe",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Unregister a browser Web Push subscription",
)
async def unregister_push_subscription(
    data: PushUnsubscribeRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Unsubscribe a specific browser endpoint for the authenticated user."""
    stmt = select(PushSubscription).where(
        PushSubscription.endpoint == data.endpoint,
        PushSubscription.user_id == current_user.id,
    )
    res = await session.execute(stmt)
    sub = res.scalar_one_or_none()

    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Push subscription not found or not owned by current user",
        )

    sub_id = sub.id
    redacted = redact_endpoint(sub.endpoint)
    await session.delete(sub)
    await session.commit()
    logger.info(
        "Unregistered Web Push subscription %d for user %d (endpoint: %s)",
        sub_id,
        current_user.id,
        redacted,
    )
    return MessageResponse(message="Push subscription removed successfully")
