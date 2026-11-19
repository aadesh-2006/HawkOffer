"""Milestone 23.1: Production Fail-Closed Security Hardening Tests.

Verifies:
- Production configuration enforces HAWKOFFER_AUTH_REQUIRED=true.
- Production configuration rejects SQLite (explicit or fallback) and requires PostgreSQL.
- Development / desktop / test behavior remains completely unchanged.
- Application startup fails safely through the production validation lifecycle.
- Zero secret or credential leakage in validation messages or exceptions.
"""

from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.main import create_app

# ==============================================================================
# Authentication Hardening Tests
# ==============================================================================


def test_production_auth_required_false_reports_validation_error() -> None:
    """Production configuration must reject auth_required=False."""
    s = Settings(
        env="production",
        auth_required=False,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/hawkoffer",
    )
    is_valid, reason = s.validate_production_auth()
    assert not is_valid
    assert "HAWKOFFER_AUTH_REQUIRED must be set to 'true' in production" in reason

    errors = s.validate_production_configuration()
    assert any("HAWKOFFER_AUTH_REQUIRED" in err for err in errors)


def test_production_auth_required_true_passes() -> None:
    """Production configuration must accept auth_required=True."""
    s = Settings(
        env="production",
        auth_required=True,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/hawkoffer",
    )
    is_valid, reason = s.validate_production_auth()
    assert is_valid
    assert reason == "Valid"


def test_development_auth_required_false_preserved() -> None:
    """Development and desktop mode preserve auth_required=False without error."""
    s = Settings(
        env="development",
        auth_required=False,
    )
    assert not s.auth_required
    assert not s.is_production
    assert not s.is_saas_mode
    # SQLite fallback is active in development
    assert "sqlite+aiosqlite:///" in s.get_database_url()


# ==============================================================================
# Database Hardening Tests
# ==============================================================================


def test_production_missing_database_url_reports_validation_error() -> None:
    """Production configuration must reject missing database URL."""
    s = Settings(
        env="production",
        auth_required=True,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url=None,
    )
    is_valid, reason = s.validate_production_database()
    assert not is_valid
    assert "HAWKOFFER_DATABASE_URL is not configured" in reason
    assert "PostgreSQL" in reason

    errors = s.validate_production_configuration()
    assert any("HAWKOFFER_DATABASE_URL is not configured" in err for err in errors)

    # Calling get_database_url directly in production must raise ValueError
    with pytest.raises(ValueError, match="HAWKOFFER_DATABASE_URL.*not configured"):
        s.get_database_url()


@pytest.mark.parametrize(
    "sqlite_url",
    [
        "sqlite:///hawkoffer.db",
        "sqlite+aiosqlite:///hawkoffer.db",
        "sqlite:///:memory:",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_production_sqlite_url_rejected(sqlite_url: str) -> None:
    """Production configuration must strictly reject SQLite connection strings."""
    s = Settings(
        env="production",
        auth_required=True,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url=sqlite_url,
    )
    is_valid, reason = s.validate_production_database()
    assert not is_valid
    assert "SQLite is not permitted in production" in reason

    errors = s.validate_production_configuration()
    assert any("SQLite is not permitted in production" in err for err in errors)

    # Calling get_database_url directly must also raise ValueError
    with pytest.raises(ValueError, match="SQLite is not permitted in production"):
        s.get_database_url()


@pytest.mark.parametrize(
    "pg_url",
    [
        "postgresql://user:pass@aws-0.pooler.supabase.com:6543/postgres?sslmode=require",
        "postgres://user:pass@aws-0.pooler.supabase.com:5432/postgres?sslmode=require",
        "postgresql+asyncpg://user:pass@localhost:5432/hawkoffer?ssl=require",
    ],
)
def test_production_valid_postgres_url_passes(pg_url: str) -> None:
    """Production configuration accepts valid PostgreSQL URLs."""
    s = Settings(
        env="production",
        auth_required=True,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url=pg_url,
    )
    is_valid, reason = s.validate_production_database()
    assert is_valid
    assert reason == "Valid"

    normalized = s.get_database_url()
    assert normalized.startswith("postgresql+asyncpg://")
    assert "ssl=require" in normalized


def test_development_sqlite_url_supported() -> None:
    """Development mode fully supports SQLite (default fallback and explicit URL)."""
    # Default fallback
    s_default = Settings(env="development", database_url=None)
    assert s_default.get_database_url().startswith("sqlite+aiosqlite:///")

    # Explicit SQLite URL
    s_explicit = Settings(env="development", database_url="sqlite:///custom.db")
    assert s_explicit.get_database_url() == "sqlite+aiosqlite:///custom.db"


def test_production_malformed_postgres_url_does_not_leak_credentials() -> None:
    """Malformed PostgreSQL URLs in production report errors without leaking secrets."""
    secret_pass = "super_classified_pw_99!"
    malformed_url = f"postgresql://appuser:{secret_pass}@localhost:not_a_port/broken?sslmode=require"

    s = Settings(
        env="production",
        auth_required=True,
        cron_secret="c" * 32,
        jwt_secret_key="j" * 32,
        database_url=malformed_url,
    )
    is_valid, reason = s.validate_production_database()
    assert not is_valid
    assert secret_pass not in reason
    assert "appuser" not in reason
    assert "***:***@" in reason


# ==============================================================================
# Lifecycle Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_production_lifespan_aborts_on_disabled_auth() -> None:
    """Lifespan aborts on startup when in production with auth_required=False."""
    with patch("app.main.settings") as mock_settings:
        mock_settings.is_production = True
        mock_settings.validate_production_configuration.return_value = [
            "HAWKOFFER_AUTH_REQUIRED must be set to 'true' in production"
        ]

        app = create_app(start_scheduler=False, init_database=False)
        with pytest.raises(
            RuntimeError,
            match="Production configuration validation failed.*HAWKOFFER_AUTH_REQUIRED",
        ):
            async with app.router.lifespan_context(app):
                pass


@pytest.mark.asyncio
async def test_production_lifespan_aborts_on_sqlite_or_missing_db() -> None:
    """Lifespan aborts on startup when in production with missing or SQLite database."""
    with patch("app.main.settings") as mock_settings:
        mock_settings.is_production = True
        mock_settings.validate_production_configuration.return_value = [
            "SQLite is not permitted in production; a PostgreSQL database URL is required"
        ]

        app = create_app(start_scheduler=False, init_database=False)
        with pytest.raises(
            RuntimeError,
            match="Production configuration validation failed.*SQLite is not permitted",
        ):
            async with app.router.lifespan_context(app):
                pass
