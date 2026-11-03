"""Database dialect compatibility and abstraction layer."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def get_dialect_name(session_or_bind: Any) -> str:
    """Extract dialect name ('sqlite', 'postgresql', etc.) from session, connection, or engine."""
    if hasattr(session_or_bind, "get_bind"):
        bind = session_or_bind.get_bind()
        if bind is not None and hasattr(bind, "dialect"):
            return str(bind.dialect.name)
    elif hasattr(session_or_bind, "bind"):
        bind = session_or_bind.bind
        if bind is not None and hasattr(bind, "dialect"):
            return str(bind.dialect.name)
    elif hasattr(session_or_bind, "dialect"):
        return str(session_or_bind.dialect.name)
    return "sqlite"


def get_upsert_insert(target: Any, session_or_bind: Any = None) -> Any:
    """Return dialect-specific insert statement with on_conflict_do_nothing support.

    Works seamlessly across SQLite and PostgreSQL dialects with the same
    `.values(...)` and `.on_conflict_do_nothing(...)` chain.
    """
    dialect_name = get_dialect_name(session_or_bind) if session_or_bind is not None else "sqlite"
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(target)
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(target)


async def acquire_exclusive_write_lock(session: AsyncSession) -> None:
    """Acquire exclusive write lock for SQLite; safe no-op for PostgreSQL MVCC."""
    dialect_name = get_dialect_name(session)
    if dialect_name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
