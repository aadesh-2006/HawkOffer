"""FastAPI dependency injection providers."""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import async_session_factory as default_session_factory
from app.scheduler.orchestrator import MonitoringOrchestrator


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
