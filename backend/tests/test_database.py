"""Tests for database engine, configuration, and session lifecycle."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.database.session import create_engine_instance, init_db


@pytest.mark.asyncio
async def test_cross_platform_config(tmp_path: Path) -> None:
    """Test platform-independent directory resolution and env overrides."""
    # Custom data_dir override (ensure_exists=False does not create dir)
    custom_dir = tmp_path / "custom_data"
    cfg = Settings(data_dir=custom_dir)
    resolved_dir = cfg.get_data_dir(create=False)
    assert resolved_dir == custom_dir.resolve()
    # Path should NOT exist until explicitly created
    assert not resolved_dir.exists()

    cfg.ensure_data_dir()
    assert resolved_dir.exists()

    # URL generation uses forward slashes on all platforms
    db_url = cfg.get_database_url()
    assert db_url.startswith("sqlite+aiosqlite:///")
    assert "\\" not in db_url

    sync_url = cfg.get_sync_database_url()
    assert sync_url.startswith("sqlite:///")
    assert "aiosqlite" not in sync_url


@pytest.mark.asyncio
async def test_no_filesystem_side_effects_on_import(tmp_path: Path) -> None:
    """Verify that resolving database URL and importing modules does not create directories."""
    isolated_dir = tmp_path / "isolated_appdata"
    test_settings = Settings(data_dir=isolated_dir)
    # Resolving URL must not mutate filesystem
    _ = test_settings.get_database_url()
    assert not isolated_dir.exists()


@pytest.mark.asyncio
async def test_sqlite_pragmas(test_engine: AsyncEngine) -> None:
    """Verify that SQLite pragmas (foreign keys, WAL mode) are activated on connect."""
    async with test_engine.connect() as conn:
        fk_result = await conn.execute(text("PRAGMA foreign_keys;"))
        assert fk_result.scalar() == 1

        journal_result = await conn.execute(text("PRAGMA journal_mode;"))
        journal_mode = journal_result.scalar()
        # In temporary files WAL mode will be returned
        assert isinstance(journal_mode, str) and journal_mode.lower() == "wal"


@pytest.mark.asyncio
async def test_init_db(tmp_path: Path) -> None:
    """Verify init_db initializes all tables properly."""
    db_file = tmp_path / "init_test.db"
    engine = create_engine_instance(f"sqlite+aiosqlite:///{db_file.as_posix()}")

    await init_db(engine)

    async with engine.connect() as conn:
        tables_res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table';"))
        tables = [row[0] for row in tables_res.fetchall()]

    assert "companies" in tables
    assert "jobs" in tables
    assert "preference_profiles" in tables
    assert "notifications" in tables
    assert "monitoring_logs" in tables

    await engine.dispose()


@pytest.mark.asyncio
async def test_session_rollback(db_session: AsyncSession) -> None:
    """Verify rollback discards uncommitted session changes."""
    from app.models.company import Company

    company = Company(
        name="Rollback Inc",
        career_url="https://example.com/careers",
        platform="generic",
    )
    db_session.add(company)
    await db_session.flush()

    # Verify present before rollback
    assert company.id is not None

    await db_session.rollback()

    # Query after rollback
    res = await db_session.execute(text("SELECT count(*) FROM companies WHERE name='Rollback Inc'"))
    assert res.scalar() == 0
