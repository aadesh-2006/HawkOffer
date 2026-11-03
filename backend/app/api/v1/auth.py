"""Authentication endpoints for registration, login, token refresh, and logout."""

import logging
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    normalize_email,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from app.models.token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LogoutRequest,
    TokenRefreshRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register_user(
    payload: UserRegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> User:
    """Register a new user account with secure Argon2 password hashing.

    Validates password strength and ensures email uniqueness.
    """
    clean_email = normalize_email(payload.email)

    # 1. Validate password strength
    is_valid, err_msg = validate_password_strength(payload.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=err_msg or "Password does not meet strength requirements",
        )

    # 2. Check email uniqueness
    existing_res = await session.execute(select(User).where(User.email == clean_email))
    if existing_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
        )

    # 3. Hash password using Argon2id
    hashed = hash_password(payload.password)

    # 4. Create and persist user entity
    new_user = User(
        email=clean_email,
        hashed_password=hashed,
        full_name=payload.full_name.strip() if payload.full_name else None,
        role="user",
        is_active=True,
        is_verified=False,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    logger.info("New user registered successfully: user_id=%d", new_user.id)
    return new_user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive JWT access and refresh tokens",
)
async def login(
    payload: UserLoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate credentials and issue short-lived access and long-lived refresh tokens."""
    clean_email = normalize_email(payload.email)

    res = await session.execute(select(User).where(User.email == clean_email))
    user = res.scalar_one_or_none()

    if user is None:
        # Perform constant-time dummy verify to prevent timing-based user enumeration
        verify_dummy_password(payload.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    # Issue tokens
    access_token, _ = create_access_token(user_id=user.id, email=user.email, role=user.role)
    refresh_token, refresh_jti, refresh_exp = create_refresh_token(user_id=user.id)

    # Persist refresh token state for cryptographic revocation and rotation tracking
    db_refresh_token = RefreshToken(
        user_id=user.id,
        token_jti=refresh_jti,
        revoked=False,
        expires_at=refresh_exp,
    )
    session.add(db_refresh_token)
    await session.commit()

    logger.info("User authenticated successfully: user_id=%d", user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue a new access token",
)
async def refresh_tokens(
    payload: TokenRefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Validate refresh token, verify non-revoked state, rotate refresh token,
    and issue access token.
    """
    invalid_token_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        decoded = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError:
        raise invalid_token_exc from None

    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type: refresh token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = decoded.get("jti")
    user_id_str = decoded.get("sub")
    if not jti or not user_id_str or not user_id_str.isdigit():
        raise invalid_token_exc

    user_id = int(user_id_str)

    # Check database token revocation state
    res = await session.execute(
        select(RefreshToken).where(
            RefreshToken.token_jti == jti,
            RefreshToken.user_id == user_id,
        )
    )
    token_db = res.scalar_one_or_none()
    if token_db is None or token_db.revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked or is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(UTC)
    expires_at = token_db.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check user active status
    user_res = await session.execute(select(User).where(User.id == user_id))
    user = user_res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or does not exist",
        )

    # Rotate refresh token: revoke current token and issue new pair
    token_db.revoked = True

    new_access_token, _ = create_access_token(user_id=user.id, email=user.email, role=user.role)
    new_refresh_token, new_jti, new_exp = create_refresh_token(user_id=user.id)

    session.add(
        RefreshToken(
            user_id=user.id,
            token_jti=new_jti,
            revoked=False,
            expires_at=new_exp,
        )
    )
    await session.commit()

    logger.info("Rotated refresh token successfully for user_id=%d", user.id)
    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke refresh token and terminate active session",
)
async def logout(
    payload: LogoutRequest,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Revoke refresh token in the database to prevent further session rotation."""
    if payload.refresh_token:
        try:
            decoded = decode_token(payload.refresh_token)
            jti = decoded.get("jti")
            if jti:
                res = await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.token_jti == jti,
                        RefreshToken.user_id == current_user.id,
                    )
                )
                db_token = res.scalar_one_or_none()
                if db_token:
                    db_token.revoked = True
                    await session.commit()
        except jwt.PyJWTError:
            # Token already invalid or expired; no-op
            pass

    logger.info("User logged out successfully: user_id=%d", current_user.id)
    return MessageResponse(message="Successfully logged out")


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get current authenticated user profile",
)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Return the profile details of the currently authenticated user."""
    return current_user
