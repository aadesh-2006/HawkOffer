"""Milestone 19: Deployment Validation & Production Hardening Tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.deps import get_orchestrator
from app.core.config import Settings
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
