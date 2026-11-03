"""Comprehensive test suite for M14 Authentication, Security, and Tenant Isolation."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    normalize_email,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.user import User


@pytest.fixture
async def auth_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an HTTPX AsyncClient for auth and isolation testing."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.scheduler.running = True
    mock_orchestrator.is_lifecycle_gate_open = True
    mock_orchestrator._is_shutting_down = False
    mock_orchestrator._is_starting = False
    mock_orchestrator.sync_company_schedules = AsyncMock()

    app = create_app(
        orchestrator=mock_orchestrator,
        session_factory=session_factory,
        start_scheduler=False,
        init_database=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver/api/v1",
    ) as client:
        yield client


@pytest.fixture
async def sample_job(session_factory: async_sessionmaker[AsyncSession]) -> Job:
    """Create a sample company and job for testing."""
    async with session_factory() as session:
        company = Company(
            name="Acme Corp",
            career_url="https://acme.example.com/careers",
            platform="greenhouse",
            is_active=True,
            is_curated=True,
        )
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            external_id="job-acme-101",
            title="Senior Backend Engineer",
            application_url="https://acme.example.com/careers/101",
            location="Remote",
            fingerprint_hash="test-fingerprint-101",
            content_hash="test-content-101",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            status="ACTIVE",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


# ---------------------------------------------------------------------------
# 1. CORE SECURITY TESTS
# ---------------------------------------------------------------------------


def test_password_hashing_and_verification() -> None:
    """Verify Argon2id password hashing and constant-time verification."""
    password = "SecurePassword123!"
    hashed = hash_password(password)

    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False
    assert verify_dummy_password("AnyPassword123!") is False


def test_password_strength_and_email_normalization() -> None:
    """Verify password strength validation rules and email normalization."""
    valid, err = validate_password_strength("ValidPass123!")
    assert valid is True
    assert err is None

    # Too short (<8)
    valid_short, err_short = validate_password_strength("Short1!")
    assert valid_short is False
    assert err_short is not None and "at least 8" in err_short

    # No digit or symbol
    valid_no_digit, err_no_digit = validate_password_strength("AllLettersOnlyPass")
    assert valid_no_digit is False
    assert err_no_digit is not None and "digit or special" in err_no_digit

    # Email normalization
    raw_email = "  John.DOE+test@Example.COM  \n"
    assert normalize_email(raw_email) == "john.doe+test@example.com"


def test_jwt_token_lifecycle() -> None:
    """Verify JWT access and refresh token generation, typing, and decode."""
    user_id = 42
    access_token, _ = create_access_token(user_id=user_id, email="user@example.com")
    payload = decode_token(access_token, expected_type="access")
    assert int(payload["sub"]) == user_id
    assert payload["type"] == "access"

    # Refresh token
    refresh_token, jti, _ = create_refresh_token(user_id=user_id)
    assert len(jti) == 36  # UUID4
    ref_payload = decode_token(refresh_token, expected_type="refresh")
    assert int(ref_payload["sub"]) == user_id
    assert ref_payload["type"] == "refresh"
    assert ref_payload["jti"] == jti

    # Type mismatch rejection
    with pytest.raises(ValueError, match="Invalid token type"):
        decode_token(access_token, expected_type="refresh")


# ---------------------------------------------------------------------------
# 2. AUTHENTICATION ENDPOINTS TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_registration_and_login_flow(auth_client: httpx.AsyncClient) -> None:
    """Verify user registration, duplicate handling, login, me, and logout."""
    email = "engineer@example.com"
    password = "StrongPassword123!"

    # 1. Register
    reg_res = await auth_client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert reg_res.status_code == 201
    user_data = reg_res.json()
    assert user_data["email"] == email
    assert user_data["role"] == "user"
    assert user_data["is_active"] is True
    assert "password" not in user_data
    assert "hashed_password" not in user_data

    # 2. Duplicate registration
    dup_res = await auth_client.post(
        "/auth/register",
        json={"email": email.upper(), "password": password},
    )
    assert dup_res.status_code == 409
    assert "already exists" in dup_res.json()["detail"]

    # 3. Weak password rejection
    weak_res = await auth_client.post(
        "/auth/register",
        json={"email": "other@example.com", "password": "weak"},
    )
    assert weak_res.status_code == 422

    # 4. Login with bad password
    bad_login = await auth_client.post(
        "/auth/login",
        json={"email": email, "password": "WrongPassword123!"},
    )
    assert bad_login.status_code == 401

    # 5. Login with non-existent user (timing-safe check)
    unknown_login = await auth_client.post(
        "/auth/login",
        json={"email": "nonexistent@example.com", "password": password},
    )
    assert unknown_login.status_code == 401

    # 6. Successful login
    login_res = await auth_client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    tokens = login_res.json()
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    assert access_token
    assert refresh_token

    # 7. Access /auth/me with Bearer token
    me_res = await auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == email

    # 8. Token Refresh
    refresh_res = await auth_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_res.status_code == 200
    new_tokens = refresh_res.json()
    new_access_token = new_tokens["access_token"]
    new_refresh_token = new_tokens["refresh_token"]
    assert new_access_token != access_token
    assert new_refresh_token != refresh_token

    # 9. Reuse old refresh token (must be rejected)
    reused_res = await auth_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert reused_res.status_code == 401

    # 10. Logout with current refresh token
    logout_res = await auth_client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {new_access_token}"},
        json={"refresh_token": new_refresh_token},
    )
    assert logout_res.status_code == 200

    # 11. Refresh token after logout should fail
    post_logout_ref = await auth_client.post(
        "/auth/refresh",
        json={"refresh_token": new_refresh_token},
    )
    assert post_logout_ref.status_code == 401


# ---------------------------------------------------------------------------
# 3. TENANT ISOLATION TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation_applications(
    auth_client: httpx.AsyncClient, sample_job: Job
) -> None:
    """Verify that User A's job applications are completely isolated from User B."""
    # Setup User A
    await auth_client.post(
        "/auth/register", json={"email": "alice@tenant.com", "password": "AlicePassword123!"}
    )
    login_a = await auth_client.post(
        "/auth/login", json={"email": "alice@tenant.com", "password": "AlicePassword123!"}
    )
    token_a = login_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Setup User B
    await auth_client.post(
        "/auth/register", json={"email": "bob@tenant.com", "password": "BobPassword123!"}
    )
    login_b = await auth_client.post(
        "/auth/login", json={"email": "bob@tenant.com", "password": "BobPassword123!"}
    )
    token_b = login_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. Alice creates application for sample_job
    create_res = await auth_client.post(
        "/applications",
        headers=headers_a,
        json={"job_id": sample_job.id, "status": "APPLIED", "notes": "Alice's referral"},
    )
    assert create_res.status_code == 201
    app_id = create_res.json()["id"]

    # 2. Bob lists applications -> must be empty
    bob_list = await auth_client.get("/applications", headers=headers_b)
    assert bob_list.status_code == 200
    assert len(bob_list.json()) == 0

    # 3. Bob attempts to GET Alice's application -> 404
    bob_get = await auth_client.get(f"/applications/{app_id}", headers=headers_b)
    assert bob_get.status_code == 404

    # 4. Bob attempts to PATCH Alice's application -> 404
    bob_patch = await auth_client.patch(
        f"/applications/{app_id}",
        headers=headers_b,
        json={"notes": "Bob tampering"},
    )
    assert bob_patch.status_code == 404

    # 5. Bob attempts to DELETE Alice's application -> 404
    bob_del = await auth_client.delete(f"/applications/{app_id}", headers=headers_b)
    assert bob_del.status_code == 404

    # 6. Alice can view and update her application
    alice_get = await auth_client.get(f"/applications/{app_id}", headers=headers_a)
    assert alice_get.status_code == 200
    assert alice_get.json()["notes"] == "Alice's referral"

    alice_patch = await auth_client.patch(
        f"/applications/{app_id}",
        headers=headers_a,
        json={"status": "INTERVIEWING"},
    )
    assert alice_patch.status_code == 200
    assert alice_patch.json()["status"] == "INTERVIEWING"

    # 7. Alice deletes her application
    alice_del = await auth_client.delete(f"/applications/{app_id}", headers=headers_a)
    assert alice_del.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation_subscriptions(
    auth_client: httpx.AsyncClient, sample_job: Job
) -> None:
    """Verify that User A's company subscriptions are completely isolated from User B."""
    # Setup User A
    await auth_client.post(
        "/auth/register", json={"email": "user_a@sub.com", "password": "UserPass123!"}
    )
    login_a = await auth_client.post(
        "/auth/login", json={"email": "user_a@sub.com", "password": "UserPass123!"}
    )
    token_a = login_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Setup User B
    await auth_client.post(
        "/auth/register", json={"email": "user_b@sub.com", "password": "UserPass123!"}
    )
    login_b = await auth_client.post(
        "/auth/login", json={"email": "user_b@sub.com", "password": "UserPass123!"}
    )
    token_b = login_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. User A subscribes to sample_job's company
    sub_res = await auth_client.post(
        "/subscriptions",
        headers=headers_a,
        json={"company_id": sample_job.company_id},
    )
    assert sub_res.status_code == 201

    # 2. Duplicate subscription for User A is 409
    dup_sub = await auth_client.post(
        "/subscriptions",
        headers=headers_a,
        json={"company_id": sample_job.company_id},
    )
    assert dup_sub.status_code == 409

    # 3. User B lists subscriptions -> empty
    b_subs = await auth_client.get("/subscriptions", headers=headers_b)
    assert b_subs.status_code == 200
    assert len(b_subs.json()) == 0

    # 4. User B attempts to unsubscribe from company they did not subscribe to -> 404
    b_unsub = await auth_client.delete(
        f"/subscriptions/{sample_job.company_id}",
        headers=headers_b,
    )
    assert b_unsub.status_code == 404

    # 5. User A unsubscribes -> 200
    a_unsub = await auth_client.delete(
        f"/subscriptions/{sample_job.company_id}",
        headers=headers_a,
    )
    assert a_unsub.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation_preference_profiles(auth_client: httpx.AsyncClient) -> None:
    """Verify that preference profiles are strictly scoped to the owner."""
    # Setup User A
    await auth_client.post(
        "/auth/register", json={"email": "user_p1@test.com", "password": "ProfileUser123!"}
    )
    login_a = await auth_client.post(
        "/auth/login", json={"email": "user_p1@test.com", "password": "ProfileUser123!"}
    )
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    # Setup User B
    await auth_client.post(
        "/auth/register", json={"email": "user_p2@test.com", "password": "ProfileUser123!"}
    )
    login_b = await auth_client.post(
        "/auth/login", json={"email": "user_p2@test.com", "password": "ProfileUser123!"}
    )
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    # User A creates a profile
    create_res = await auth_client.post(
        "/profiles",
        headers=headers_a,
        json={"name": "Alice Backend Profile", "preferred_roles": ["Backend"]},
    )
    assert create_res.status_code == 201
    profile_id = create_res.json()["id"]

    # User B lists profiles -> empty
    b_list = await auth_client.get("/profiles", headers=headers_b)
    assert b_list.status_code == 200
    assert b_list.json()["total"] == 0

    # User B tries to read, update, or delete User A's profile -> 404
    assert (await auth_client.get(f"/profiles/{profile_id}", headers=headers_b)).status_code == 404
    assert (
        await auth_client.patch(
            f"/profiles/{profile_id}", headers=headers_b, json={"name": "Tampered"}
        )
    ).status_code == 404
    assert (
        await auth_client.post(f"/profiles/{profile_id}/activate", headers=headers_b)
    ).status_code == 404
    assert (
        await auth_client.delete(f"/profiles/{profile_id}", headers=headers_b)
    ).status_code == 404

    # User A can read and update
    a_get = await auth_client.get(f"/profiles/{profile_id}", headers=headers_a)
    assert a_get.status_code == 200
    assert a_get.json()["name"] == "Alice Backend Profile"

    # User A deletes profile
    a_del = await auth_client.delete(f"/profiles/{profile_id}", headers=headers_a)
    assert a_del.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation_notifications(
    auth_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    sample_job: Job,
) -> None:
    """Verify that User A's notifications are isolated from User B."""
    # Setup User A
    reg_a = await auth_client.post(
        "/auth/register", json={"email": "notify_a@test.com", "password": "NotifyPass123!"}
    )
    user_a_id = reg_a.json()["id"]
    login_a = await auth_client.post(
        "/auth/login", json={"email": "notify_a@test.com", "password": "NotifyPass123!"}
    )
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    # Setup User B
    await auth_client.post(
        "/auth/register", json={"email": "notify_b@test.com", "password": "NotifyPass123!"}
    )
    login_b = await auth_client.post(
        "/auth/login", json={"email": "notify_b@test.com", "password": "NotifyPass123!"}
    )
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    # Insert notification for User A directly in DB
    async with session_factory() as session:
        notif = Notification(
            job_id=sample_job.id,
            user_id=user_a_id,
            notification_type="new_job",
            status="UNREAD",
            delivery_status="PENDING",
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    # User B lists notifications -> empty
    b_notifs = await auth_client.get("/notifications", headers=headers_b)
    assert b_notifs.status_code == 200
    assert b_notifs.json()["total"] == 0

    # User B unread count -> 0
    b_unread = await auth_client.get("/notifications/unread-count", headers=headers_b)
    assert b_unread.status_code == 200
    assert b_unread.json()["unread_count"] == 0

    # User B tries to mark read User A's notification -> 404
    b_read = await auth_client.post(f"/notifications/{notif_id}/read", headers=headers_b)
    assert b_read.status_code == 404

    # User B tries to dismiss User A's notification -> 404
    b_dismiss = await auth_client.post(f"/notifications/{notif_id}/dismiss", headers=headers_b)
    assert b_dismiss.status_code == 404

    # User A unread count -> 1
    a_unread = await auth_client.get("/notifications/unread-count", headers=headers_a)
    assert a_unread.status_code == 200
    assert a_unread.json()["unread_count"] == 1

    # User A marks read -> 200
    a_read = await auth_client.post(f"/notifications/{notif_id}/read", headers=headers_a)
    assert a_read.status_code == 200
    assert a_read.json()["status"] == "READ"


@pytest.mark.asyncio
async def test_tenant_isolation_custom_companies(auth_client: httpx.AsyncClient) -> None:
    """Verify that regular users cannot modify other users' custom or curated companies."""
    # Setup User A
    await auth_client.post(
        "/auth/register", json={"email": "founder_a@test.com", "password": "Password123!"}
    )
    login_a = await auth_client.post(
        "/auth/login", json={"email": "founder_a@test.com", "password": "Password123!"}
    )
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    # Setup User B
    await auth_client.post(
        "/auth/register", json={"email": "founder_b@test.com", "password": "Password123!"}
    )
    login_b = await auth_client.post(
        "/auth/login", json={"email": "founder_b@test.com", "password": "Password123!"}
    )
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    # 1. User A creates custom company
    create_res = await auth_client.post(
        "/companies",
        headers=headers_a,
        json={"name": "Startup Alpha", "career_url": "https://alpha.example.com/careers"},
    )
    assert create_res.status_code == 201
    comp = create_res.json()
    assert comp["is_curated"] is False
    comp_id = comp["id"]

    # 2. User B tries to update User A's custom company -> 403 Forbidden
    b_patch = await auth_client.patch(
        f"/companies/{comp_id}",
        headers=headers_b,
        json={"name": "Startup Alpha Hack"},
    )
    assert b_patch.status_code == 403

    # 3. User B tries to delete User A's custom company -> 403 Forbidden
    b_del = await auth_client.delete(f"/companies/{comp_id}", headers=headers_b)
    assert b_del.status_code == 403

    # 4. User A can update their own custom company
    a_patch = await auth_client.patch(
        f"/companies/{comp_id}",
        headers=headers_a,
        json={"name": "Startup Alpha Rebranded"},
    )
    assert a_patch.status_code == 200
    assert a_patch.json()["name"] == "Startup Alpha Rebranded"


@pytest.mark.asyncio
async def test_per_user_notification_deduplication(
    session_factory: async_sessionmaker[AsyncSession], sample_job: Job
) -> None:
    """Verify that multiple users can each get a notification for the same job,

    but a single user cannot receive duplicate notifications for the same job.
    """
    async with session_factory() as session:
        # Create User 1 & User 2
        u1 = User(
            email="u1@dedup.com",
            hashed_password=hash_password("Pass123!"),
            role="user",
            is_active=True,
        )
        u2 = User(
            email="u2@dedup.com",
            hashed_password=hash_password("Pass123!"),
            role="user",
            is_active=True,
        )
        session.add_all([u1, u2])
        await session.commit()
        await session.refresh(u1)
        await session.refresh(u2)

        # 1. Notification for User 1 on sample_job
        notif1 = Notification(
            job_id=sample_job.id,
            user_id=u1.id,
            notification_type="new_job",
            status="UNREAD",
            delivery_status="PENDING",
        )
        session.add(notif1)
        await session.commit()

        # 2. Notification for User 2 on the SAME sample_job -> succeeds!
        notif2 = Notification(
            job_id=sample_job.id,
            user_id=u2.id,
            notification_type="new_job",
            status="UNREAD",
            delivery_status="PENDING",
        )
        session.add(notif2)
        await session.commit()

        # 3. Duplicate notification for User 1 on the SAME job and notification_type -> fails!
        dup_notif = Notification(
            job_id=sample_job.id,
            user_id=u1.id,
            notification_type="new_job",
            status="UNREAD",
            delivery_status="PENDING",
        )
        session.add(dup_notif)
        with pytest.raises(IntegrityError):
            await session.commit()


def test_alembic_m14_migration_lifecycle() -> None:
    """Verify Alembic runs up to m14 head, downgrades, and upgrades cleanly."""
    import tempfile
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        clean_path = Path(db_path).as_posix()
        url = f"sqlite:///{clean_path}"

        alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", url)

        # 1. Upgrade to head (includes m14)
        command.upgrade(alembic_cfg, "head")

        # 2. Downgrade by 1 (rolls back m14 to m13)
        command.downgrade(alembic_cfg, "-1")

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")
    finally:
        if Path(db_path).exists():
            Path(db_path).unlink()
