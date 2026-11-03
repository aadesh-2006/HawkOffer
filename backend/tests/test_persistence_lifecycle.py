"""Test suite for HawkOffer persistent data, upgrade, and uninstall safety.

Verifies:
1. Application data directory is resolved outside the installation directory.
2. Existing SQLite database and user data survive simulated application upgrades.
3. Database migrations execute safely on startup without overwriting user data.
4. Simulated application uninstallation preserves user database.
5. Fresh installation reconnects to existing database seamlessly.
6. Schema migrations are idempotent on restart.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.database.session import create_engine_instance, init_db
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.preference import PreferenceProfile
from app.schemas.notification import NotificationStatus


def test_data_dir_resolution_outside_installation_directory() -> None:
    """Verify default data directory resolves to user data directory, not Program Files."""
    s = Settings()
    data_dir = s.get_data_dir()
    data_str = str(data_dir).lower()

    # Must not be inside program files or install directory
    assert "program files" not in data_str
    assert "program files (x86)" not in data_str
    assert "hawkoffer" in data_str


@pytest.mark.asyncio
async def test_database_persistence_and_reconnection_across_upgrades(tmp_path: Path) -> None:
    """Verify user database persists across simulated version upgrades and reconnects cleanly."""
    persistent_data_dir = tmp_path / "persistent_hawkoffer_data"
    persistent_data_dir.mkdir(parents=True, exist_ok=True)
    db_file = persistent_data_dir / "hawkoffer.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    # =========================================================================
    # VERSION 1.0.0: Initial Installation and Data Creation
    # =========================================================================
    engine_v1 = create_engine_instance(db_url=db_url)
    await init_db(target_engine=engine_v1)

    session_factory_v1 = async_sessionmaker(
        bind=engine_v1,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory_v1() as session:
        # 1. User adds monitored company
        company = Company(
            name="V1 Target Corp",
            career_url="https://boards.greenhouse.io/v1target",
            platform="greenhouse",
            is_active=True,
        )
        session.add(company)
        await session.flush()
        company_id = company.id

        # 2. User sets preference profile
        profile = PreferenceProfile(
            name="Systems Architect",
            is_active=True,
            minimum_match_score=45.0,
            preferred_roles=["Systems Engineer", "Backend Architect"],
            preferred_skills=["Rust", "Python", "Linux"],
            preferred_locations=["Remote"],
            preferred_employment_types=["Full-time"],
            required_employment_types=[],
            preferred_workplace_types=["Remote"],
            excluded_keywords=["Sales"],
            excluded_title_keywords=[],
            excluded_content_keywords=[],
            required_roles=[],
            required_locations=[],
            require_graduation_year_match=False,
        )
        session.add(profile)
        await session.flush()
        profile_id = profile.id

        # 3. Discovery worker inserts job and notification
        job = Job(
            company_id=company_id,
            title="Senior Distributed Systems Architect",
            application_url="https://boards.greenhouse.io/v1target/jobs/101",
            location="Remote",
            fingerprint_hash="a" * 64,
            content_hash="b" * 64,
            status="ACTIVE",
            employment_type="Full-time",
            job_type="full_time",
        )
        session.add(job)
        await session.flush()

        notification = Notification(
            job_id=job.id,
            profile_id=profile_id,
            notification_type="job_alert",
            status=NotificationStatus.UNREAD,
        )
        session.add(notification)
        await session.commit()

    # Close V1 runtime engine cleanly
    await engine_v1.dispose()

    # =========================================================================
    # SIMULATED UNINSTALL & REINSTALL (Version 1.1.0)
    # =========================================================================
    # Executables are replaced; persistent data directory remains untouched
    assert db_file.exists()
    assert db_file.stat().st_size > 0

    # Start V1.1.0 application instance pointing to same persistent database
    engine_v2 = create_engine_instance(db_url=db_url)
    # Re-running init_db must be idempotent and preserve existing tables
    await init_db(target_engine=engine_v2)

    session_factory_v2 = async_sessionmaker(
        bind=engine_v2,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory_v2() as session:
        # Verify company intact
        comp = await session.get(Company, company_id)
        assert comp is not None
        assert comp.name == "V1 Target Corp"
        assert comp.is_active is True

        # Verify profile intact
        prof = await session.get(PreferenceProfile, profile_id)
        assert prof is not None
        assert prof.name == "Systems Architect"
        assert prof.revision == 1

        # Verify job intact
        jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].fingerprint_hash == "a" * 64

        # Verify notification intact and mark as read
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].status == NotificationStatus.UNREAD

        notifs[0].status = NotificationStatus.READ
        await session.commit()

    await engine_v2.dispose()

    # =========================================================================
    # VERIFY SYNC CONNECTION (e.g. Alembic schema inspects existing database)
    # =========================================================================
    sync_conn = sqlite3.connect(str(db_file))
    cursor = sync_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    sync_conn.close()

    assert "companies" in tables
    assert "jobs" in tables
    assert "preference_profiles" in tables
    assert "notifications" in tables
    assert "job_discovery_outbox" in tables
    assert "notification_backlog" in tables
