"""FastAPI dependency injection providers including auth and tenant isolation."""

from collections.abc import AsyncGenerator, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import decode_token
from app.database.session import async_session_factory as default_session_factory
from app.models.user import User
from app.scheduler.orchestrator import MonitoringOrchestrator

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Retrieve the application session factory, supporting test overrides."""
    return getattr(request.app.state, "session_factory", default_session_factory)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an asynchronous database session with automatic commit/rollback."""
    session_factory = get_session_factory(request)
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_orchestrator(request: Request) -> MonitoringOrchestrator | None:
    """Retrieve the MonitoringOrchestrator instance from application state if available."""
    return getattr(request.app.state, "orchestrator", None)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Validate Bearer JWT access token and return authenticated User entity.

    Raises 401 Unauthorized if token is missing, expired, invalid, or wrong type.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError:
        raise credentials_exception from None

    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type: access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str or not user_id_str.isdigit():
        raise credentials_exception

    user_id = int(user_id_str)
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the authenticated user account is active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    return current_user


async def get_optional_user(
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User | None:
    """Return authenticated User if Bearer token is provided.

    If no token is provided:
    - If settings.auth_required is True (SaaS production mode): raises 401.
    - If settings.auth_required is False (desktop mode): returns None.
    """
    if not token:
        if settings.auth_required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    return await get_current_user(token=token, session=session)


def require_role(required_role: str) -> Callable[[User], User]:
    """Factory creating dependency to enforce role-based access control (RBAC)."""

    def role_checker(user: User = Depends(get_current_active_user)) -> User:
        if user.role != required_role and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires '{required_role}' privilege",
            )
        return user

    return role_checker
