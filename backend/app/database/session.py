"""Database session and connection management for HawkOffer."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.database.base import Base

logger = logging.getLogger(__name__)


def create_engine_instance(db_url: str | None = None, echo: bool | None = None) -> AsyncEngine:
    """Create a configured AsyncEngine instance with SQLite pragmas."""
    url = db_url or settings.get_database_url()
    is_echo = echo if echo is not None else settings.db_echo

    engine = create_async_engine(
        url,
        echo=is_echo,
        future=True,
    )

    # Attach pragmas if using SQLite
    if "sqlite" in url:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON;")
                # WAL mode cannot be enabled on in-memory databases
                if ":memory:" not in url:
                    cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.close()
            except Exception as exc:
                logger.warning("Could not execute SQLite pragma: %s", exc)

    return engine


# Default engine and session maker
engine: AsyncEngine = create_engine_instance()
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db(target_engine: AsyncEngine | None = None) -> None:
    """Initialize database tables and ensure storage directory exists."""
    settings.ensure_data_dir()
    eng = target_engine or engine
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for obtaining an asynchronous database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
