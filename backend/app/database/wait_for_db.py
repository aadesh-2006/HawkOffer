"""Database connectivity and readiness verification helper.

Used by the container entrypoint to verify database availability before running
migrations and launching the web server, preventing startup crashes during database
cold-starts or Docker Compose initialization.
"""

import asyncio
import logging
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

logger = logging.getLogger("wait_for_db")


def sanitize_db_url_for_logging(url: str) -> str:
    """Mask credentials in database URL for safe diagnostic logging."""
    if "@" in url:
        # url format: scheme://user:password@host:port/dbname
        scheme_prefix = url.split("://")[0] + "://" if "://" in url else ""
        host_and_db = url.split("@")[-1]
        return f"{scheme_prefix}***:***@{host_and_db}"
    return url


async def wait_for_database(
    timeout_seconds: float = 30.0,
    interval_seconds: float = 2.0,
) -> bool:
    """Attempt connecting to the configured database until available or timeout.

    Args:
        timeout_seconds: Maximum total seconds to wait for database connection.
        interval_seconds: Delay between connection attempts.

    Returns:
        True if database connection succeeded within timeout, False otherwise.
    """
    db_url = settings.get_database_url()

    # SQLite is embedded/local; verify directory exists
    if "sqlite" in db_url:
        settings.ensure_data_dir()
        logger.info("SQLite database storage directory verified.")
        return True

    safe_target = sanitize_db_url_for_logging(db_url)
    logger.info(
        "Probing database connectivity at %s (timeout: %.1fs)...",
        safe_target,
        timeout_seconds,
    )

    engine = create_async_engine(db_url, pool_pre_ping=True)
    start_time = time.monotonic()

    try:
        while True:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                logger.info("Database is reachable and accepting queries.")
                return True
            except Exception as exc:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_seconds:
                    logger.error(
                        "Database connection timed out after %d seconds: %s",
                        timeout_seconds,
                        exc,
                    )
                    return False
                logger.warning(
                    "Database not ready yet (%s). Retrying in %.1fs...",
                    exc,
                    interval_seconds,
                )
                await asyncio.sleep(interval_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    """CLI entrypoint for wait-for-db probe."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [wait_for_db] %(message)s",
    )
    timeout = int(os.environ.get("DB_WAIT_TIMEOUT", "30"))
    success = asyncio.run(wait_for_database(timeout_seconds=timeout))
    if not success:
        logger.critical("Fatal: Unable to connect to database within %d seconds.", timeout)
        sys.exit(1)


if __name__ == "__main__":
    main()
