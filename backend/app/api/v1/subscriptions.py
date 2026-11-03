"""User company subscription API endpoints strictly isolated to the authenticated user."""

import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db
from app.models.company import Company
from app.models.subscription import UserCompanySubscription
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.subscription import SubscriptionCreate, SubscriptionRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


@router.get(
    "",
    response_model=list[SubscriptionRead],
    summary="List companies subscribed to by current user",
)
async def list_subscriptions(
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> Sequence[UserCompanySubscription]:
    """Return all company subscriptions belonging strictly to the authenticated user."""
    stmt = (
        select(UserCompanySubscription)
        .where(UserCompanySubscription.user_id == current_user.id)
        .order_by(UserCompanySubscription.created_at.desc())
    )
    res = await session.execute(stmt)
    return res.scalars().all()


@router.post(
    "",
    response_model=SubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe current user to a monitored company",
)
async def create_subscription(
    payload: SubscriptionCreate,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> UserCompanySubscription:
    """Subscribe authenticated user to a monitored company career portal."""
    # Verify company exists
    comp_res = await session.execute(select(Company.id).where(Company.id == payload.company_id))
    if comp_res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {payload.company_id} not found",
        )

    # Check for existing subscription
    existing_res = await session.execute(
        select(UserCompanySubscription).where(
            UserCompanySubscription.user_id == current_user.id,
            UserCompanySubscription.company_id == payload.company_id,
        )
    )
    if existing_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already subscribed to this company",
        )

    sub = UserCompanySubscription(
        user_id=current_user.id,
        company_id=payload.company_id,
    )
    session.add(sub)
    try:
        await session.commit()
        await session.refresh(sub)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate subscription to company",
        ) from None

    logger.info("User %d subscribed to company %d", current_user.id, payload.company_id)
    return sub


@router.delete(
    "/{company_id}",
    response_model=MessageResponse,
    summary="Unsubscribe current user from a company",
)
async def delete_subscription(
    company_id: int,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Unsubscribe authenticated user from a company."""
    stmt = select(UserCompanySubscription).where(
        UserCompanySubscription.user_id == current_user.id,
        UserCompanySubscription.company_id == company_id,
    )
    res = await session.execute(stmt)
    sub = res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription for company {company_id} not found",
        )

    await session.delete(sub)
    await session.commit()
    logger.info("User %d unsubscribed from company %d", current_user.id, company_id)
    return MessageResponse(message=f"Successfully unsubscribed from company {company_id}")
