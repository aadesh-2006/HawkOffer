"""Standalone executable entry point for HawkOffer bundled Python backend daemon.

Handles:
- PyInstaller frozen environment path resolution (sys._MEIPASS).
- Database migrations and table initialization.
- Single-instance PID management and stale lock recovery.
- Starting the FastAPI application via Uvicorn bound strictly to 127.0.0.1.
- Graceful shutdown and PID lock release on SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from alembic.config import Config

from alembic import command
from app.core.config import settings
from app.database.session import engine, init_db
from app.desktop_runtime import DesktopRuntimeManager
from app.main import create_app

logger = logging.getLogger("hawkoffer.bundle_entry")


def get_base_dir() -> Path:
    """Resolve base directory whether running as a PyInstaller bundle or from source."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent.parent


def run_database_migrations(db_sync_url: str | None = None) -> bool:
    """Execute Alembic database migrations up to head."""
    base_dir = get_base_dir()
    ini_path = base_dir / "alembic.ini"
    alembic_dir = base_dir / "alembic"

    if not ini_path.exists() or not alembic_dir.exists():
        logger.info("Alembic configuration not found at %s; relying on init_db()", ini_path)
        return False

    try:
        cfg = Config(str(ini_path))
        target_url = db_sync_url or settings.get_sync_database_url()
        cfg.set_main_option("sqlalchemy.url", target_url)
        cfg.set_main_option("script_location", str(alembic_dir))
        cfg.set_main_option("version_locations", str(alembic_dir / "versions"))
        command.upgrade(cfg, "head")
        logger.info("Alembic database migrations applied successfully to 'head'")
        return True
    except Exception as exc:
        logger.warning("Alembic migration upgrade encountered notice/error: %s", exc)
        return False


def main() -> int:
    """CLI and frozen binary entry point for HawkOffer backend."""
    parser = argparse.ArgumentParser(
        description="HawkOffer Bundled Desktop Backend Daemon",
    )
    parser.add_argument(
        "--host",
        default=settings.api_host,
        help="Host address to bind (strictly 127.0.0.1 for desktop security)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.api_port,
        help=f"Port number to bind (default: {settings.api_port})",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Optional override for application data directory",
    )
    parser.add_argument(
        "--no-scheduler",
        action="store_true",
        help="Disable automatic APScheduler startup",
    )
    parser.add_argument(
        "--action",
        choices=["serve", "status", "stop", "readiness"],
        default="serve",
        help="Runtime supervisor action to execute",
    )

    args = parser.parse_args()

    # 1. Enforce strict loopback binding
    host = args.host.strip()
    if host not in ("127.0.0.1", "localhost"):
        logger.error("Security violation: Refusing to bind to non-loopback host '%s'", host)
        sys.stderr.write(
            f"ERROR: HawkOffer backend can only bind to 127.0.0.1 for security. Got: {host}\n"
        )
        return 1

    # 2. Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 3. Apply data-dir override if supplied
    custom_data_dir: Path | None = None
    if args.data_dir:
        custom_data_dir = Path(args.data_dir).resolve()
        settings.data_dir = custom_data_dir
        os.environ["HAWKOFFER_DATA_DIR"] = str(custom_data_dir)

    manager = DesktopRuntimeManager(
        host=host,
        port=args.port,
        data_dir=custom_data_dir,
    )

    # 4. Handle secondary actions
    if args.action == "status":
        info = manager.status()
        import json

        print(json.dumps(info, indent=2))
        return 0 if info["ready"] else 1

    if args.action == "stop":
        success, msg = manager.stop()
        print(msg)
        return 0 if success else 1

    if args.action == "readiness":
        res = manager.check_readiness()
        print(f"Ready: {res.ready} | Status: {res.status_code} | {res.message}")
        return 0 if res.ready else 1

    # 5. Action: serve (main backend daemon)
    # Check if an existing backend is already alive and ready
    existing_pid = manager.read_pid()
    if existing_pid is not None:
        if manager.is_process_alive(existing_pid):
            readiness = manager.check_readiness(timeout_seconds=0.5)
            if readiness.ready:
                logger.info(
                    "Backend already running and healthy (PID %d on %s:%d)",
                    existing_pid,
                    host,
                    args.port,
                )
                print(f"ALREADY_RUNNING PID={existing_pid}")
                return 0
            logger.warning(
                "Process %d is running but not responding to health checks", existing_pid
            )
        else:
            logger.info("Cleaning up stale PID %d before startup", existing_pid)
            manager.remove_pid()

    # 6. Record current PID
    current_pid = os.getpid()
    manager.write_pid(current_pid)

    # Register exit cleanup
    def _cleanup_pid() -> None:
        if manager.read_pid() == current_pid:
            manager.remove_pid()

    atexit.register(_cleanup_pid)

    # Handle termination signals
    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received termination signal %d, exiting...", signum)
        _cleanup_pid()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):
        pass

    # 7. Run database migrations and table schemas
    import asyncio

    logger.info("Initializing database schemas and migrations...")
    run_database_migrations()
    asyncio.run(init_db(target_engine=engine))

    # 8. Create FastAPI app with configured lifespan
    start_scheduler = not args.no_scheduler
    app = create_app(
        start_scheduler=start_scheduler,
        init_database=False,  # Already initialized above
    )

    # 9. Start Uvicorn HTTP server
    import uvicorn

    logger.info("Starting HawkOffer backend daemon on http://%s:%d...", host, args.port)
    print(f"HAWKOFFER_BACKEND_READY PID={current_pid} HOST={host} PORT={args.port}")

    config = uvicorn.Config(
        app=app,
        host=host,
        port=args.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    finally:
        _cleanup_pid()

    return 0


if __name__ == "__main__":
    sys.exit(main())
