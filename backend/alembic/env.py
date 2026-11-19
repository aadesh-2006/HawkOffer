import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure backend root is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import app.models  # noqa: E402, F401
from app.core.config import redact_db_url, sanitize_exception_text, settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import is_transaction_pooler_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Ensure storage directory exists before running migrations
settings.ensure_data_dir()

# Resolve database URL
x_args = context.get_x_argument(as_dictionary=True)
if "db_url" in x_args:
    db_url = x_args["db_url"]
else:
    main_url = config.get_main_option("sqlalchemy.url")
    if main_url and main_url not in ("sqlite:///hawkoffer.db", ""):
        db_url = main_url
    else:
        db_url = settings.get_database_url()

# Normalize to async driver if sync driver scheme was provided
if db_url.startswith("sqlite://"):
    db_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
elif not db_url.startswith("sqlite+aiosqlite://"):
    try:
        url_obj = make_url(db_url)
        driver = url_obj.drivername
        if driver.startswith(("postgres", "postgresql")):
            query_dict = dict(url_obj.query)
            if "sslmode" in query_dict:
                ssl_val = query_dict.pop("sslmode")
                if "ssl" not in query_dict:
                    query_dict["ssl"] = ssl_val
            url_obj = url_obj.set(drivername="postgresql+asyncpg", query=query_dict)
            db_url = url_obj.render_as_string(hide_password=False)
    except Exception as exc:
        if db_url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            safe_url = redact_db_url(db_url)
            safe_exc = sanitize_exception_text(exc)
            raise ValueError(
                f"Invalid or malformed PostgreSQL database URL in Alembic: {safe_url}. "
                f"Could not normalize: {safe_exc}"
            ) from exc
        raise

# Escape % as %% to protect ConfigParser from interpolation syntax errors on URL-encoded passwords
config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite column alterations
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite column alterations
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an AsyncEngine."""
    connect_args: dict[str, Any] = {}
    # Supabase PgBouncer transaction pooler (port 6543) does not support prepared statement caches
    if is_transaction_pooler_url(db_url):
        connect_args["statement_cache_size"] = 0

    connectable = create_async_engine(
        db_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
