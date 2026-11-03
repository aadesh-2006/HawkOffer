"""Unit tests for Milestone 13: Multi-tenant Data Model & Dual-Dialect Foundation."""

import tempfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import create_mock_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings
from app.database.base import Base
from app.database.compat import (
    get_dialect_name,
    get_upsert_insert,
)
from app.models.application import JobApplication
from app.models.company import Company
from app.models.job import Job
from app.models.outbox import SystemDrainState
from app.models.preference import PreferenceProfile
from app.models.push import PushSubscription
from app.models.subscription import UserCompanySubscription
from app.models.user import User


@pytest_asyncio.fixture
async def m13_db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory async SQLite database fixture for M13 schema testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_creation_and_defaults(m13_db_session: AsyncSession) -> None:
    """Verify User model creation with default attributes and timestamps."""
    user = User(
        email="founder@example.com",
        hashed_password="hashed_pw_argon2_secret",
        full_name="Sarah Founder",
    )
    m13_db_session.add(user)
    await m13_db_session.commit()

    res = await m13_db_session.execute(select(User).where(User.email == "founder@example.com"))
    saved_user = res.scalar_one()

    assert saved_user.id is not None
    assert saved_user.role == "user"
    assert saved_user.is_active is True
    assert saved_user.is_verified is False
    assert saved_user.verification_token is None
    assert isinstance(saved_user.created_at, datetime)
    assert isinstance(saved_user.updated_at, datetime)
    assert "<User" in repr(saved_user)


@pytest.mark.asyncio
async def test_user_email_uniqueness_constraint(m13_db_session: AsyncSession) -> None:
    """Verify that multiple users cannot be registered with the same email."""
    user1 = User(email="duplicate@example.com", hashed_password="pw1")
    user2 = User(email="duplicate@example.com", hashed_password="pw2")

    m13_db_session.add(user1)
    await m13_db_session.commit()

    m13_db_session.add(user2)
    with pytest.raises(IntegrityError):
        await m13_db_session.commit()
    await m13_db_session.rollback()


@pytest.mark.asyncio
async def test_user_company_subscriptions_unique_constraint(
    m13_db_session: AsyncSession,
) -> None:
    """Verify that a user cannot subscribe to the same company twice, but multiple users can."""
    user1 = User(email="alice@example.com", hashed_password="pw")
    user2 = User(email="bob@example.com", hashed_password="pw")
    company = Company(name="Stripe", career_url="https://stripe.com/jobs", platform="greenhouse")

    m13_db_session.add_all([user1, user2, company])
    await m13_db_session.commit()
    company_id = company.id
    user1_id = user1.id
    user2_id = user2.id

    # User 1 subscribes to Stripe
    sub1 = UserCompanySubscription(user_id=user1_id, company_id=company_id)
    m13_db_session.add(sub1)
    await m13_db_session.commit()

    # User 2 subscribes to the same Stripe company (1-to-N central monitoring)
    sub2 = UserCompanySubscription(user_id=user2_id, company_id=company_id)
    m13_db_session.add(sub2)
    await m13_db_session.commit()

    # Attempt duplicate subscription for User 1 to Stripe
    sub1_dup = UserCompanySubscription(user_id=user1_id, company_id=company_id)
    m13_db_session.add(sub1_dup)
    with pytest.raises(IntegrityError):
        await m13_db_session.commit()
    await m13_db_session.rollback()

    # Verify both subscriptions exist
    subs = (
        (
            await m13_db_session.execute(
                select(UserCompanySubscription).where(
                    UserCompanySubscription.company_id == company_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(subs) == 2


@pytest.mark.asyncio
async def test_user_preference_profile_relationship_and_isolation(
    m13_db_session: AsyncSession,
) -> None:
    """Verify that preference profiles can be scoped to users while maintaining independence."""
    user = User(email="engineer@example.com", hashed_password="pw")
    m13_db_session.add(user)
    await m13_db_session.commit()

    profile = PreferenceProfile(
        user_id=user.id,
        name="Backend SWE Profile",
        preferred_roles=["Backend Engineer", "Systems Engineer"],
        preferred_skills=["Python", "FastAPI", "PostgreSQL"],
    )
    m13_db_session.add(profile)
    await m13_db_session.commit()

    res = await m13_db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.user_id == user.id)
    )
    user_profiles = res.scalars().all()
    assert len(user_profiles) == 1
    assert user_profiles[0].user_id == user.id
    assert "FastAPI" in user_profiles[0].preferred_skills


@pytest.mark.asyncio
async def test_legacy_profile_compatibility_without_user(
    m13_db_session: AsyncSession,
) -> None:
    """Verify backward compatibility: profiles can exist without user_id (desktop mode)."""
    desktop_profile = PreferenceProfile(
        name="Desktop Local Profile",
        preferred_roles=["Data Engineer"],
    )
    m13_db_session.add(desktop_profile)
    await m13_db_session.commit()

    res = await m13_db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.name == "Desktop Local Profile")
    )
    loaded = res.scalar_one()
    assert loaded.user_id is None
    assert loaded.is_active is True


@pytest.mark.asyncio
async def test_job_application_tracker_constraints(m13_db_session: AsyncSession) -> None:
    """Verify user job application tracking and uniqueness per user and job."""
    user1 = User(email="app1@example.com", hashed_password="pw")
    user2 = User(email="app2@example.com", hashed_password="pw")
    company = Company(name="Figma", career_url="https://figma.com/jobs", platform="greenhouse")
    m13_db_session.add_all([user1, user2, company])
    await m13_db_session.commit()

    job = Job(
        company_id=company.id,
        title="Software Engineer",
        application_url="https://figma.com/jobs/123",
        fingerprint_hash="hash_swe_123",
        content_hash="content_swe_123",
    )
    m13_db_session.add(job)
    await m13_db_session.commit()

    # User 1 tracks application
    app1 = JobApplication(
        user_id=user1.id,
        job_id=job.id,
        status="APPLIED",
        notes="Applied via referral",
        applied_at=datetime.now(UTC),
    )
    m13_db_session.add(app1)
    await m13_db_session.commit()

    # User 2 also tracks application for the same job independently
    app2 = JobApplication(
        user_id=user2.id,
        job_id=job.id,
        status="SAVED",
    )
    m13_db_session.add(app2)
    await m13_db_session.commit()

    # User 1 cannot create duplicate application for the same job
    app1_dup = JobApplication(user_id=user1.id, job_id=job.id, status="INTERVIEWING")
    m13_db_session.add(app1_dup)
    with pytest.raises(IntegrityError):
        await m13_db_session.commit()
    await m13_db_session.rollback()


@pytest.mark.asyncio
async def test_push_subscription_model_and_uniqueness(m13_db_session: AsyncSession) -> None:
    """Verify PushSubscription model creation and endpoint uniqueness."""
    user = User(email="pushuser@example.com", hashed_password="pw")
    m13_db_session.add(user)
    await m13_db_session.commit()

    push_sub = PushSubscription(
        user_id=user.id,
        endpoint="https://fcm.googleapis.com/fcm/send/device_token_xyz",
        p256dh="key_p256dh_sample",
        auth="key_auth_sample",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    )
    m13_db_session.add(push_sub)
    await m13_db_session.commit()

    assert push_sub.id is not None
    assert push_sub.failure_count == 0

    # Duplicate endpoint should fail
    push_dup = PushSubscription(
        user_id=user.id,
        endpoint="https://fcm.googleapis.com/fcm/send/device_token_xyz",
        p256dh="diff_key",
        auth="diff_auth",
    )
    m13_db_session.add(push_dup)
    with pytest.raises(IntegrityError):
        await m13_db_session.commit()
    await m13_db_session.rollback()


@pytest.mark.asyncio
async def test_company_curated_and_creator_attributes(m13_db_session: AsyncSession) -> None:
    """Verify Company curation flag and created_by_user_id attribution."""
    user = User(email="curator@example.com", hashed_password="pw")
    m13_db_session.add(user)
    await m13_db_session.commit()

    curated_comp = Company(
        name="Anthropic",
        career_url="https://anthropic.com/careers",
        is_curated=True,
    )
    user_comp = Company(
        name="Stealth AI",
        career_url="https://stealth.ai/jobs",
        is_curated=False,
        created_by_user_id=user.id,
    )
    m13_db_session.add_all([curated_comp, user_comp])
    await m13_db_session.commit()

    res = await m13_db_session.execute(select(Company).where(Company.is_curated.is_(True)))
    curated_list = res.scalars().all()
    assert len(curated_list) == 1
    assert curated_list[0].name == "Anthropic"

    res_user = await m13_db_session.execute(
        select(Company).where(Company.created_by_user_id == user.id)
    )
    user_list = res_user.scalars().all()
    assert len(user_list) == 1
    assert user_list[0].name == "Stealth AI"


@pytest.mark.asyncio
async def test_cascade_deletion_on_user_removal(m13_db_session: AsyncSession) -> None:
    """Verify that deleting a User cascades and cleans up all owned profiles and subscriptions."""
    user = User(email="delete_me@example.com", hashed_password="pw")
    company = Company(name="Acme Corp", career_url="https://acme.org/jobs")
    m13_db_session.add_all([user, company])
    await m13_db_session.commit()

    profile = PreferenceProfile(user_id=user.id, name="Test Profile")
    sub = UserCompanySubscription(user_id=user.id, company_id=company.id)
    push = PushSubscription(
        user_id=user.id,
        endpoint="https://push.example.com/sub_delete",
        p256dh="k1",
        auth="a1",
    )
    m13_db_session.add_all([profile, sub, push])
    await m13_db_session.commit()

    # Delete the user
    await m13_db_session.delete(user)
    await m13_db_session.commit()

    # Verify owned child records are deleted
    assert (
        await m13_db_session.execute(
            select(PreferenceProfile).where(PreferenceProfile.user_id == user.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await m13_db_session.execute(
            select(UserCompanySubscription).where(UserCompanySubscription.user_id == user.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await m13_db_session.execute(
            select(PushSubscription).where(PushSubscription.user_id == user.id)
        )
    ).scalar_one_or_none() is None

    # Company itself should remain
    assert (
        await m13_db_session.execute(select(Company).where(Company.id == company.id))
    ).scalar_one() is not None


def test_database_url_normalization_in_settings() -> None:
    """Verify configuration URL normalization across SQLite and PostgreSQL formats."""
    # Default SQLite URL
    settings_default = Settings(database_url=None)
    assert settings_default.get_database_url().startswith("sqlite+aiosqlite:///")
    assert settings_default.get_sync_database_url().startswith("sqlite:///")

    # postgres:// normalized to postgresql+asyncpg://
    settings_pg_short = Settings(database_url="postgres://user:pass@host:5432/db")
    assert settings_pg_short.get_database_url() == "postgresql+asyncpg://user:pass@host:5432/db"
    assert settings_pg_short.get_sync_database_url() == "postgresql://user:pass@host:5432/db"

    # postgresql:// normalized to postgresql+asyncpg://
    settings_pg_standard = Settings(database_url="postgresql://user:pass@host:5432/db")
    assert settings_pg_standard.get_database_url() == "postgresql+asyncpg://user:pass@host:5432/db"
    assert settings_pg_standard.get_sync_database_url() == "postgresql://user:pass@host:5432/db"


def test_postgresql_ddl_compilation_via_mock_engine() -> None:
    """Verify that the entire metadata schema compiles without error under PostgreSQL dialect."""
    statements: list[str] = []

    def mock_executor(sql: object, *multiparams: object, **params: object) -> None:
        statements.append(str(sql.compile(dialect=mock_engine.dialect)))  # type: ignore

    mock_engine = create_mock_engine("postgresql+asyncpg://", mock_executor)
    Base.metadata.create_all(mock_engine, checkfirst=False)

    assert len(statements) > 0
    full_ddl = "\n".join(statements)

    assert "CREATE TABLE users" in full_ddl
    assert "CREATE TABLE user_company_subscriptions" in full_ddl
    assert "CREATE TABLE job_applications" in full_ddl
    assert "CREATE TABLE push_subscriptions" in full_ddl
    assert "uq_user_company_subscription UNIQUE" in full_ddl
    assert "uq_user_job_application UNIQUE" in full_ddl


def test_compat_helpers_dialect_detection() -> None:
    """Verify dialect detection and insert generator for sqlite and postgresql."""
    assert get_dialect_name("sqlite") == "sqlite"
    assert get_dialect_name(None) == "sqlite"

    sqlite_ins = get_upsert_insert(SystemDrainState, "sqlite")
    assert "INSERT INTO system_drain_state" in str(sqlite_ins)

    pg_ins = get_upsert_insert(SystemDrainState, "postgresql")
    assert "INSERT INTO system_drain_state" in str(pg_ins)


def test_alembic_full_migration_lifecycle() -> None:
    """Verify Alembic runs up to m13 head, downgrades, and upgrades cleanly on SQLite file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        clean_path = Path(db_path).as_posix()
        url = f"sqlite:///{clean_path}"

        alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", url)

        # 1. Upgrade to head (includes m13)
        command.upgrade(alembic_cfg, "head")

        # 2. Downgrade by 1 (rolls back m13 to m10)
        command.downgrade(alembic_cfg, "-1")

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")

    finally:
        if Path(db_path).exists():
            Path(db_path).unlink()
