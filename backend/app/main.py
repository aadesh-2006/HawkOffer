"""HawkOffer FastAPI Application Entry Point and Runtime Lifecycle."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import api_v1_router
from app.core.config import settings
from app.database.session import (
    async_session_factory as default_session_factory,
)
from app.database.session import (
    engine as default_engine,
)
from app.database.session import (
    init_db,
)
from app.scheduler.orchestrator import MonitoringOrchestrator

logger = logging.getLogger("hawkoffer")

# Restrict CORS origins strictly to Tauri and local Vite frontend environments
CORS_ALLOWED_ORIGINS: list[str] = [
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
]


def create_app(
    orchestrator: MonitoringOrchestrator | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    start_scheduler: bool = True,
    init_database: bool = True,
) -> FastAPI:
    """FastAPI application factory with configurable runtime lifecycle dependencies."""
    effective_session_factory = session_factory or default_session_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("HawkOffer application runtime starting up...")

        # 1. Initialize database tables if requested
        if init_database:
            try:
                await init_db()
                logger.info("Database schemas verified during startup")
            except Exception as exc:
                logger.error("Failed to initialize database during startup: %s", exc)
                raise

        # 2. Start scheduler orchestrator if requested
        if start_scheduler:
            if app.state.orchestrator is None:
                app.state.orchestrator = MonitoringOrchestrator(
                    session_factory=app.state.session_factory
                )
            try:
                logger.info("Starting MonitoringOrchestrator...")
                await app.state.orchestrator.start()
                logger.info("MonitoringOrchestrator started successfully")
            except Exception as exc:
                logger.error("Failed to start MonitoringOrchestrator: %s", exc)
                raise

        yield

        # 3. Graceful shutdown sequence
        logger.info("HawkOffer application runtime shutting down...")
        orch: MonitoringOrchestrator | None = getattr(app.state, "orchestrator", None)
        if orch is not None and orch.scheduler.running:
            try:
                logger.info("Shutting down MonitoringOrchestrator...")
                await orch.shutdown()
                logger.info("MonitoringOrchestrator shut down successfully")
            except Exception as exc:
                logger.warning("Error during orchestrator shutdown: %s", exc)

        # 4. Dispose database engine pool
        try:
            await default_engine.dispose()
            logger.info("Database engine connections disposed")
        except Exception as exc:
            logger.warning("Error disposing database engine: %s", exc)

    app = FastAPI(
        title="HawkOffer API",
        description="Local desktop job monitoring, ingestion, filtering, and alert pipeline.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.session_factory = effective_session_factory
    app.state.orchestrator = orchestrator

    # Attach CORS middleware with narrow desktop app origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API v1 router
    app.include_router(api_v1_router)

    # Standardized exception handlers
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        logger.warning("Validation / ValueError on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("IntegrityError on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Database integrity constraint violation"},
        )

    @app.get("/", tags=["Root"])
    async def root() -> dict[str, Any]:
        """Root API metadata endpoint."""
        return {
            "name": settings.app_name,
            "version": "0.1.0",
            "environment": settings.env,
            "status": "running",
            "docs_url": "/docs",
        }

    return app


# Default ASGI application instance for uvicorn
app = create_app()
