"""Milestone 19: Deployment Validation & Production Hardening Tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.deps import get_orchestrator
from app.core.config import Settings
from app.database.wait_for_db import sanitize_db_url_for_logging, wait_for_database
from app.main import create_app


def test_cors_origins_prohibits_wildcard() -> None:
    """Verify that wildcard '*' CORS origin is strictly rejected to prevent credential leaks."""
    with pytest.raises(ValidationError, match="Wildcard '\\*' CORS origin is strictly prohibited"):
        Settings(
            cors_origins="*, https://app.hawkoffer.dev",
            cron_secret="a" * 32,
            jwt_secret_key="b" * 32,
        )

    with pytest.raises(ValidationError, match="Wildcard '\\*' CORS origin is strictly prohibited"):
        Settings(
            cors_origins=["*"],
            cron_secret="a" * 32,
            jwt_secret_key="b" * 32,
        )


def test_validate_production_jwt_secret() -> None:
    """Verify that production JWT secret validation rejects missing, short, or default keys."""
    # Dev default
    s_default = Settings(
        env="production",
        jwt_secret_key="hawkoffer-dev-secret-key-change-in-production-min-32-chars",
    )
    is_valid, reason = s_default.validate_production_jwt_secret()
    assert not is_valid
    assert "known insecure development default" in reason

    # Too short
    s_short = Settings(
        env="production",
        jwt_secret_key="short-jwt-key",
    )
    is_valid, reason = s_short.validate_production_jwt_secret()
    assert not is_valid
    assert "at least 32 characters" in reason

    # Strong valid secret
    strong_key = "a" * 32
    s_valid = Settings(
        env="production",
        jwt_secret_key=strong_key,
    )
    is_valid, reason = s_valid.validate_production_jwt_secret()
    assert is_valid
    assert reason == "Valid"


def test_validate_production_configuration() -> None:
    """Verify validate_production_configuration aggregates all configuration errors."""
    s_insecure = Settings(
        env="production",
        cron_secret="insecure",
        jwt_secret_key="short",
    )
    errors = s_insecure.validate_production_configuration()
    assert len(errors) == 2

    s_secure = Settings(
        env="production",
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        cors_origins=["https://hawkoffer.pages.dev"],
    )
    errors = s_secure.validate_production_configuration()
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_production_lifespan_aborts_on_insecure_configuration() -> None:
    """Verify that application lifespan aborts on startup if in production with weak secrets."""
    with patch("app.main.settings") as mock_settings:
        mock_settings.is_production = True
        mock_settings.validate_production_configuration.return_value = [
            "HAWKOFFER_JWT_SECRET_KEY must be at least 32 characters in production",
            "HAWKOFFER_CRON_SECRET is not configured",
        ]

        app = create_app(start_scheduler=False, init_database=False)
        with pytest.raises(RuntimeError, match="Production configuration validation failed"):
            async with app.router.lifespan_context(app):
                pass


@pytest.mark.asyncio
async def test_orchestrator_available_for_cron_when_internal_scheduler_disabled() -> None:
    """Verify orchestrator is instantiated without starting scheduler when disabled."""
    mock_instance = MagicMock()
    mock_instance.start = AsyncMock()
    mock_instance.shutdown = AsyncMock()
    mock_instance.scheduler.running = False

    with patch("app.main.MonitoringOrchestrator", return_value=mock_instance), patch(
        "app.main.settings.start_internal_scheduler", False
    ):
        app = create_app(
            orchestrator=None,
            start_scheduler=True,
            init_database=False,
        )
        async with app.router.lifespan_context(app):
            # Orchestrator instance should be created and attached to app.state
            assert app.state.orchestrator is mock_instance
            # But its internal scheduler should NOT be started
            mock_instance.start.assert_not_awaited()

            # Dependency injection provider retrieves it for internal cron endpoint
            req = MagicMock()
            req.app = app
            assert get_orchestrator(req) is mock_instance


def test_sanitize_db_url_for_logging() -> None:
    """Verify credentials in database connection URLs are masked in logs."""
    url = "postgresql+asyncpg://hawk_user:super_secret_pw@ep-cool.neon.tech:5432/neondb"
    sanitized = sanitize_db_url_for_logging(url)
    assert "super_secret_pw" not in sanitized
    assert "hawk_user" not in sanitized
    assert sanitized == "postgresql+asyncpg://***:***@ep-cool.neon.tech:5432/neondb"

    sqlite_url = "sqlite+aiosqlite:///C:/data/hawkoffer.db"
    assert sanitize_db_url_for_logging(sqlite_url) == sqlite_url


@pytest.mark.asyncio
async def test_wait_for_database_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify wait_for_database returns True immediately on SQLite databases."""
    monkeypatch.setattr("app.database.wait_for_db.settings.database_url", "sqlite+aiosqlite:///test.db")
    success = await wait_for_database(timeout_seconds=5)
    assert success is True


@pytest.mark.asyncio
async def test_wait_for_database_postgres_retry_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify wait_for_database retries transient failures and returns True on success."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()

    # Fail on first attempt, succeed on second
    attempts = 0

    class MockConnectContext:
        async def __aenter__(self) -> AsyncMock:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionRefusedError("Database initializing")
            return mock_conn

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    mock_engine.connect.return_value = MockConnectContext()
    mock_engine.dispose = AsyncMock()

    monkeypatch.setattr(
        "app.database.wait_for_db.settings.database_url",
        "postgresql+asyncpg://user:pass@host:5432/db",
    )
    with patch("app.database.wait_for_db.create_async_engine", return_value=mock_engine):
        success = await wait_for_database(timeout_seconds=5, interval_seconds=0.05)
        assert success is True
        assert attempts == 2
        mock_engine.dispose.assert_awaited()


@pytest.mark.asyncio
async def test_wait_for_database_postgres_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify wait_for_database returns False when connection attempts exceed timeout."""
    mock_engine = MagicMock()

    class MockFailingConnectContext:
        async def __aenter__(self) -> AsyncMock:
            raise ConnectionRefusedError("Database down")

        async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
            pass

    mock_engine.connect.return_value = MockFailingConnectContext()
    mock_engine.dispose = AsyncMock()

    monkeypatch.setattr(
        "app.database.wait_for_db.settings.database_url",
        "postgresql+asyncpg://user:pass@host:5432/db",
    )
    with patch("app.database.wait_for_db.create_async_engine", return_value=mock_engine):
        success = await wait_for_database(timeout_seconds=0.2, interval_seconds=0.05)
        assert success is False
        mock_engine.dispose.assert_awaited()


def test_docker_entrypoint_script_wait_for_db() -> None:
    """Verify docker-entrypoint.sh executes wait_for_db and supports arbitrary commands."""
    entrypoint_path = (
        Path(__file__).resolve().parent.parent.parent
        / "backend"
        / "scripts"
        / "docker-entrypoint.sh"
    )
    content = entrypoint_path.read_text(encoding="utf-8")
    assert "app.database.wait_for_db" in content
    assert 'exec "$@"' in content


def test_smoke_test_runner_url_derivation_and_recording() -> None:
    """Verify SmokeTestRunner normalizes URLs and records structured test outcomes."""
    import sys
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.smoke_test import SmokeTestRunner

    # 1. Test URL derivation
    r1 = SmokeTestRunner(api_url="https://api.hawkoffer.app/api/v1", web_url="https://hawkoffer.pages.dev/")
    assert r1.api_url == "https://api.hawkoffer.app/api/v1"
    assert r1.root_url == "https://api.hawkoffer.app"
    assert r1.web_url == "https://hawkoffer.pages.dev"

    r2 = SmokeTestRunner(api_url="https://api.hawkoffer.app")
    assert r2.api_url == "https://api.hawkoffer.app/api/v1"
    assert r2.root_url == "https://api.hawkoffer.app"
    assert r2.web_url is None

    # 2. Test record and summary logic
    r1.record("Test Step 1", "GET /test", "PASS", "HTTP 200", 12.5)
    r1.record("Test Step 2", "POST /test", "SKIP", "Not configured", 0.0)
    assert len(r1.results) == 2
    assert r1.print_summary() is True

    r1.record("Test Step 3", "DELETE /test", "FAIL", "HTTP 500", 50.0)
    assert r1.print_summary() is False
