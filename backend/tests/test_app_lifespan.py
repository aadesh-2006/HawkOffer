"""Tests for FastAPI application lifespan, CORS middleware, and global error handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import create_app


@pytest.mark.asyncio
async def test_app_root_metadata() -> None:
    """Verify root GET / returns metadata and documentation pointer."""
    app = create_app(start_scheduler=False, init_database=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["docs_url"] == "/docs"


@pytest.mark.asyncio
async def test_cors_middleware_allowed_origins() -> None:
    """Verify CORS headers are returned for authorized localhost and Tauri origins."""
    app = create_app(start_scheduler=False, init_database=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Authorized origin: localhost:1420
        res1 = await client.get(
            "/",
            headers={"Origin": "http://localhost:1420"},
        )
        assert res1.status_code == 200
        assert res1.headers.get("access-control-allow-origin") == "http://localhost:1420"
        assert res1.headers.get("access-control-allow-credentials") == "true"

        # Authorized origin: tauri://localhost
        res2 = await client.get(
            "/",
            headers={"Origin": "tauri://localhost"},
        )
        assert res2.status_code == 200
        assert res2.headers.get("access-control-allow-origin") == "tauri://localhost"

        # Unauthorized origin: evil.com
        res3 = await client.get(
            "/",
            headers={"Origin": "http://evil.com"},
        )
        assert res3.status_code == 200
        assert "access-control-allow-origin" not in res3.headers


@pytest.mark.asyncio
async def test_custom_exception_handlers() -> None:
    """Verify ValueError returns 400 and IntegrityError returns 409."""
    app = create_app(start_scheduler=False, init_database=False)

    @app.get("/test-value-error")
    async def raise_value_error() -> None:
        raise ValueError("Invalid field configuration")

    @app.get("/test-integrity-error")
    async def raise_integrity_error() -> None:
        raise IntegrityError("INSERT INTO ...", params={}, orig=Exception("unique constraint"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r1 = await client.get("/test-value-error")
        assert r1.status_code == 400
        assert r1.json()["detail"] == "Invalid field configuration"

        r2 = await client.get("/test-integrity-error")
        assert r2.status_code == 409
        assert "integrity" in r2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_app_lifespan_orchestrator_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify application lifespan starts orchestrator on startup and shuts it down on shutdown."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.start = AsyncMock()
    mock_orchestrator.shutdown = AsyncMock()
    mock_orchestrator.scheduler.running = True

    app = create_app(
        orchestrator=mock_orchestrator,
        session_factory=session_factory,
        start_scheduler=True,
        init_database=False,
    )

    async with app.router.lifespan_context(app):
        mock_orchestrator.start.assert_awaited_once()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.get("/")
            assert res.status_code == 200

    mock_orchestrator.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_lifespan_startup_failure_aborts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that an exception during startup (e.g. orchestrator start failure) propagates."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.start = AsyncMock(side_effect=RuntimeError("Scheduler failed to start"))
    mock_orchestrator.scheduler.running = False

    app = create_app(
        orchestrator=mock_orchestrator,
        session_factory=session_factory,
        start_scheduler=True,
        init_database=False,
    )

    with pytest.raises(RuntimeError, match="Scheduler failed to start"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_app_lifespan_init_db_success(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify init_database=True successfully invokes init_db on startup."""
    with patch("app.main.init_db", new_callable=AsyncMock) as mock_init_db:
        app = create_app(
            start_scheduler=False,
            init_database=True,
            session_factory=session_factory,
        )
        async with app.router.lifespan_context(app):
            mock_init_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_lifespan_init_db_failure_propagates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that failure in init_db during startup raises an exception."""
    with patch(
        "app.main.init_db", new_callable=AsyncMock, side_effect=RuntimeError("DB init failed")
    ):
        app = create_app(
            start_scheduler=False,
            init_database=True,
            session_factory=session_factory,
        )
        with pytest.raises(RuntimeError, match="DB init failed"):
            async with app.router.lifespan_context(app):
                pass


@pytest.mark.asyncio
async def test_app_lifespan_shutdown_resilience(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify shutdown logs errors gracefully if orchestrator or engine dispose fails."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.start = AsyncMock()
    mock_orchestrator.shutdown = AsyncMock(side_effect=RuntimeError("Shutdown error"))
    mock_orchestrator.scheduler.running = True

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock(side_effect=RuntimeError("Dispose error"))

    with patch("app.main.default_engine", mock_engine):
        app = create_app(
            orchestrator=mock_orchestrator,
            session_factory=session_factory,
            start_scheduler=True,
            init_database=False,
        )
        async with app.router.lifespan_context(app):
            pass
        mock_orchestrator.shutdown.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_lifespan_default_orchestrator_creation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify default MonitoringOrchestrator is created and started when orchestrator is None."""
    with patch("app.main.MonitoringOrchestrator") as mock_orch_cls:
        mock_instance = MagicMock()
        mock_instance.start = AsyncMock()
        mock_instance.shutdown = AsyncMock()
        mock_instance.scheduler.running = True
        mock_orch_cls.return_value = mock_instance

        app = create_app(
            orchestrator=None,
            session_factory=session_factory,
            start_scheduler=True,
            init_database=False,
        )
        async with app.router.lifespan_context(app):
            mock_instance.start.assert_awaited_once()
        mock_instance.shutdown.assert_awaited_once()
