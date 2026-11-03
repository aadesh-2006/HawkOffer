"""Comprehensive test suite for M16 Web Push Notifications & Background Dispatch."""

import importlib.util
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.core.vapid import (
    generate_vapid_keypair,
    get_vapid_claims,
    get_vapid_private_key,
    get_vapid_public_key,
    validate_production_vapid,
)
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.push import NotificationPushDelivery, PushSubscription
from app.models.user import User
from app.schemas.notification import DeliveryStatus, NotificationStatus
from app.services.push_dispatcher import WebPushDispatcher, WebPushDispatchError


@pytest.fixture
async def push_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an HTTPX AsyncClient with Web Push routers mounted."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.scheduler.running = True
    mock_orchestrator.is_lifecycle_gate_open = True
    mock_orchestrator._is_shutting_down = False
    mock_orchestrator._is_starting = False

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
async def sample_users(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[User, User, str, str]:
    """Create two test users (User A and User B) with auth tokens."""
    async with session_factory() as session:
        user_a = User(
            email="user_a@example.com",
            hashed_password=hash_password("PasswordA123!"),
            is_active=True,
            is_verified=True,
        )
        user_b = User(
            email="user_b@example.com",
            hashed_password=hash_password("PasswordB123!"),
            is_active=True,
            is_verified=True,
        )
        session.add_all([user_a, user_b])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)

        token_a, _ = create_access_token(user_id=user_a.id, email=user_a.email)
        token_b, _ = create_access_token(user_id=user_b.id, email=user_b.email)

        return user_a, user_b, token_a, token_b


@pytest.fixture
async def sample_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> Job:
    """Create a company and job for testing."""
    async with session_factory() as session:
        company = Company(
            name="Starlight Aerospace",
            career_url="https://starlight.example.com/careers",
            platform="greenhouse",
            is_active=True,
            is_curated=True,
        )
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            external_id="sl-999",
            title="Principal Flight Systems Engineer",
            application_url="https://starlight.example.com/jobs/999",
            location="Boca Chica, TX",
            fingerprint_hash="starlight-999-fp",
            content_hash="starlight-999-ch",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            status="ACTIVE",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


# ---------------------------------------------------------------------------
# 1. VAPID CRYPTOGRAPHY & CONFIGURATION TESTS
# ---------------------------------------------------------------------------


def test_vapid_keypair_generation_and_loading() -> None:
    """Verify ECDSA P-256 keypair generation, DER formats, and claim construction."""
    pub_key, priv_key = generate_vapid_keypair()
    assert isinstance(pub_key, str)
    assert isinstance(priv_key, str)
    assert len(pub_key) >= 80  # Base64url 65-byte uncompressed point
    assert len(priv_key) >= 40  # Base64url 32-byte scalar

    # Ephemeral dev fallback
    resolved_pub = get_vapid_public_key()
    assert len(resolved_pub) >= 80
    resolved_priv = get_vapid_private_key()
    assert len(resolved_priv) >= 40

    claims = get_vapid_claims()
    assert "sub" in claims
    assert claims["sub"].startswith("mailto:")


def test_validate_production_vapid_strictness() -> None:
    """Verify that production environment requires explicitly configured VAPID keys."""
    # Under test/dev environment with unset keys, validate_production_vapid flags False
    with patch.object(settings, "vapid_public_key", None):
        is_valid, msg = validate_production_vapid()
        assert is_valid is False
        assert "not configured" in msg

    # Under configured keys
    pub, priv = generate_vapid_keypair()
    with (
        patch.object(settings, "vapid_public_key", pub),
        patch.object(settings, "vapid_private_key", priv),
    ):
        is_valid, msg = validate_production_vapid()
        assert is_valid is True
        assert msg == "Valid"

    # In production mode, get_vapid_public_key raises RuntimeError if unconfigured
    with (
        patch.object(settings, "env", "production"),
        patch.object(settings, "vapid_public_key", None),
        patch.object(settings, "vapid_private_key", None),
        pytest.raises(RuntimeError, match="VAPID keys are missing in production"),
    ):
        get_vapid_public_key()


# ---------------------------------------------------------------------------
# 2. VAPID PUBLIC KEY ENDPOINT TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vapid_public_key_endpoint(push_client: httpx.AsyncClient) -> None:
    """Verify GET /api/v1/push/vapid-public-key returns the public key without leakage."""
    resp = await push_client.get("/push/vapid-public-key")
    assert resp.status_code == 200
    data = resp.json()
    assert "public_key" in data
    assert len(data["public_key"]) >= 80
    assert "private" not in data
    assert "private_key" not in data


# ---------------------------------------------------------------------------
# 3. SUBSCRIPTION REGISTRATION & MULTI-TENANT ISOLATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_registration_and_upsert(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify subscription registration and upsert when same endpoint is re-registered."""
    user_a, _, token_a, _ = sample_users
    headers = {"Authorization": f"Bearer {token_a}"}

    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/device-token-12345",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QT9ic1",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
        "user_agent": "Mozilla/5.0 TestBrowser/1.0",
    }

    # Register initial subscription
    resp1 = await push_client.post("/push/subscribe", json=payload, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["endpoint"] == payload["endpoint"]
    sub_id_1 = data1["id"]

    # Re-register same endpoint with updated keys -> should upsert in place
    updated_payload = dict(payload)
    updated_payload["keys"] = {
        "p256dh": "UPDATED_KEY_BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA",
        "auth": "UPDATED_AUTH_tBHItJI5svbpez",
    }
    resp2 = await push_client.post("/push/subscribe", json=updated_payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["id"] == sub_id_1  # Reused same record
    assert data2["endpoint"] == payload["endpoint"]


@pytest.mark.asyncio
async def test_subscription_tenant_isolation(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify strict tenant isolation between User A and User B subscriptions."""
    user_a, user_b, token_a, token_b = sample_users
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A registers sub A
    await push_client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://push.services.mozilla.com/v1/sub-a",
            "keys": {"p256dh": "key-a", "auth": "auth-a"},
        },
        headers=headers_a,
    )

    # User B registers sub B
    await push_client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://push.services.mozilla.com/v1/sub-b",
            "keys": {"p256dh": "key-b", "auth": "auth-b"},
        },
        headers=headers_b,
    )

    # User A lists subscriptions -> only sees sub A
    list_a = await push_client.get("/push/subscriptions", headers=headers_a)
    assert list_a.status_code == 200
    subs_a = list_a.json()
    assert len(subs_a) == 1
    assert subs_a[0]["endpoint"] == "https://push.services.mozilla.com/v1/sub-a"

    # User B lists subscriptions -> only sees sub B
    list_b = await push_client.get("/push/subscriptions", headers=headers_b)
    assert list_b.status_code == 200
    subs_b = list_b.json()
    assert len(subs_b) == 1
    assert subs_b[0]["endpoint"] == "https://push.services.mozilla.com/v1/sub-b"

    # User A attempts to unsubscribe User B's endpoint -> 404 Not Found (not owned)
    unsub_resp = await push_client.post(
        "/push/unsubscribe",
        json={"endpoint": "https://push.services.mozilla.com/v1/sub-b"},
        headers=headers_a,
    )
    assert unsub_resp.status_code == 404

    # Confirm User B's subscription is still active
    list_b_after = await push_client.get("/push/subscriptions", headers=headers_b)
    assert len(list_b_after.json()) == 1


# ---------------------------------------------------------------------------
# 4. DISPATCHER LEASE & CONCURRENCY ATOMICITY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_claim_leases_and_atomicity(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify that WebPushDispatcher claims batches atomically with non-overlapping leases."""
    user_a, _, _, _ = sample_users

    # Create 3 distinct jobs and 3 pending notifications
    async with session_factory() as session:
        comp = Company(
            name="Test Co", career_url="https://test.com", platform="ashby", is_active=True
        )
        session.add(comp)
        await session.flush()

        for i in range(3):
            j = Job(
                company_id=comp.id,
                external_id=f"job-{i}",
                title=f"Engineer {i}",
                application_url=f"https://test.com/jobs/{i}",
                fingerprint_hash=f"fp-{i}",
                content_hash=f"ch-{i}",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                status="ACTIVE",
            )
            session.add(j)
            await session.flush()

            notif = Notification(
                user_id=user_a.id,
                job_id=j.id,
                status=NotificationStatus.UNREAD,
                delivery_status=DeliveryStatus.PENDING,
            )
            session.add(notif)
        await session.commit()

    dispatcher1 = WebPushDispatcher(session_factory=session_factory, batch_size=2)
    dispatcher2 = WebPushDispatcher(session_factory=session_factory, batch_size=2)

    async with session_factory() as s1:
        claimed1, token1 = await dispatcher1._claim_pending_notifications(s1)

    async with session_factory() as s2:
        claimed2, token2 = await dispatcher2._claim_pending_notifications(s2)

    # Claim sets must be disjoint
    assert len(claimed1) == 2
    assert len(claimed2) == 1
    assert set(claimed1).isdisjoint(set(claimed2))
    assert token1 != token2


# ---------------------------------------------------------------------------
# 5. ZERO SUBSCRIPTIONS HANDLING (NOT_REQUIRED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_zero_subscriptions_marks_not_required(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify that when a user has no push subscriptions, notification becomes NOT_REQUIRED.

    Core Invariant: The in-app notification remains UNREAD and available for web dashboard.
    """
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)
    result = await dispatcher.run_once()

    assert result.notifications_claimed == 1
    assert result.completed_notifications == 1
    assert result.successful_pushes == 0

    # Verify notification in DB
    async with session_factory() as session:
        saved = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved.delivery_status == DeliveryStatus.NOT_REQUIRED
        assert saved.status == NotificationStatus.UNREAD  # IN-APP NOTIFICATION INTACT!
        assert saved.delivery_lease_token is None


# ---------------------------------------------------------------------------
# 6. SUCCESSFUL WEB PUSH DISPATCH & IN-APP INVARIANT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_web_push_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify successful Web Push delivery, per-device delivery tracking, and in-app invariant."""
    user_a, _, _, _ = sample_users

    # Add 1 subscription for user_a
    async with session_factory() as session:
        sub = PushSubscription(
            user_id=user_a.id,
            endpoint="https://fcm.googleapis.com/fcm/send/success-device-1",
            p256dh="test-p256dh",
            auth="test-auth",
        )
        session.add(sub)
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id
        sub_id = sub.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)

    # Mock the synchronous _send_webpush to succeed (simulate HTTP 201 Created from FCM)
    with patch.object(dispatcher, "_send_webpush", return_value=201) as mock_send:
        result = await dispatcher.run_once()

        assert result.notifications_claimed == 1
        assert result.successful_pushes == 1
        assert result.completed_notifications == 1
        assert mock_send.call_count == 1

    # Verify database state
    async with session_factory() as session:
        saved_notif = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved_notif.delivery_status == DeliveryStatus.SENT
        assert saved_notif.delivered_at is not None
        assert saved_notif.status == NotificationStatus.UNREAD  # IN-APP NOTIFICATION INTACT!

        # Check per-device delivery record
        delivery_stmt = select(NotificationPushDelivery).where(
            NotificationPushDelivery.notification_id == notif_id,
            NotificationPushDelivery.subscription_id == sub_id,
        )
        delivery = (await session.execute(delivery_stmt)).scalar_one()
        assert delivery.status == "DELIVERED"
        assert delivery.delivered_at is not None


# ---------------------------------------------------------------------------
# 7. MULTI-DEVICE PARTIAL SUCCESS & IDEMPOTENCY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_device_partial_success_and_retry_idempotency(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify that when Device 1 succeeds and Device 2 fails transiently,

    Device 1 is not sent duplicate pushes on retry.
    """
    user_a, _, _, _ = sample_users

    # Register Device 1 and Device 2
    async with session_factory() as session:
        dev1 = PushSubscription(
            user_id=user_a.id,
            endpoint="https://push.example.com/device-1",
            p256dh="key-1",
            auth="auth-1",
        )
        dev2 = PushSubscription(
            user_id=user_a.id,
            endpoint="https://push.example.com/device-2",
            p256dh="key-2",
            auth="auth-2",
        )
        session.add_all([dev1, dev2])
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        await session.refresh(dev1)
        await session.refresh(dev2)
        notif_id = notif.id
        dev1_id = dev1.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)

    # First run: dev1 succeeds, dev2 fails transiently (HTTP 500)
    def mock_send_partial(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        if "device-1" in sub_info["endpoint"]:
            return 201
        raise WebPushDispatchError(status_code=500, message="Internal Server Error")

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_partial):
        res1 = await dispatcher.run_once()

    assert res1.attempted_pushes == 2
    assert res1.successful_pushes == 1
    assert res1.transient_failures == 1
    assert res1.retry_notifications == 1

    # Verify notification is marked RETRY
    async with session_factory() as session:
        notif_mid = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert notif_mid.delivery_status == DeliveryStatus.RETRY
        assert notif_mid.delivery_attempts == 1
        # Reset next_delivery_attempt_at to now for immediate retry test
        notif_mid.next_delivery_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        # Check deliveries: Dev 1 is DELIVERED, Dev 2 is FAILED
        d1 = (
            await session.execute(
                select(NotificationPushDelivery).where(
                    NotificationPushDelivery.notification_id == notif_id,
                    NotificationPushDelivery.subscription_id == dev1_id,
                )
            )
        ).scalar_one()
        assert d1.status == "DELIVERED"

    # Second run: Dev 2 now succeeds. Dev 1 MUST NOT BE CALLED AGAIN!
    called_endpoints: list[str] = []

    def mock_send_retry(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        called_endpoints.append(sub_info["endpoint"])
        return 201

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_retry):
        res2 = await dispatcher.run_once()

    assert res2.attempted_pushes == 1
    assert "https://push.example.com/device-2" in called_endpoints
    assert (
        "https://push.example.com/device-1" not in called_endpoints
    )  # IDEMPOTENT: Device 1 was skipped!
    assert res2.completed_notifications == 1

    # Verify final notification status
    async with session_factory() as session:
        notif_final = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert notif_final.delivery_status == DeliveryStatus.SENT
        assert notif_final.status == NotificationStatus.UNREAD  # IN-APP INTACT!


# ---------------------------------------------------------------------------
# 8. HTTP 410 / 404 DEAD SUBSCRIPTION PRUNING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_410_dead_subscription_pruning(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify that HTTP 410 / 404 subscriptions are pruned immediately from the database."""
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        sub = PushSubscription(
            user_id=user_a.id,
            endpoint="https://updates.push.services.mozilla.com/wpush/v2/dead-endpoint",
            p256dh="test-key",
            auth="test-auth",
        )
        session.add(sub)
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(sub)
        await session.refresh(notif)
        sub_id = sub.id
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)

    def mock_send_410(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        raise WebPushDispatchError(
            status_code=410, message="Push subscription has expired or unsubscribed"
        )

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_410):
        result = await dispatcher.run_once()

    assert result.attempted_pushes == 1
    assert result.pruned_subscriptions == 1
    assert result.completed_notifications == 1  # Completed as NOT_REQUIRED since all subs were dead

    # Verify subscription was deleted from DB
    async with session_factory() as session:
        dead_sub = (
            await session.execute(select(PushSubscription).where(PushSubscription.id == sub_id))
        ).scalar_one_or_none()
        assert dead_sub is None

        # Verify notification remains intact in database
        saved_notif = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved_notif.status == NotificationStatus.UNREAD  # IN-APP INTACT!
        assert saved_notif.delivery_status == DeliveryStatus.NOT_REQUIRED


# ---------------------------------------------------------------------------
# 9. EXPONENTIAL BACKOFF & RETRY EXHAUSTION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_exhaustion_preserves_in_app_notification(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify that exhausting max delivery attempts marks delivery_status=FAILED

    without deleting or corrupting the in-app notification.
    """
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        sub = PushSubscription(
            user_id=user_a.id,
            endpoint="https://fcm.googleapis.com/fcm/send/flaky-device",
            p256dh="key",
            auth="auth",
        )
        session.add(sub)
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
            delivery_attempts=2,  # Already attempted twice
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory, max_attempts=3)

    def mock_send_503(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        raise WebPushDispatchError(status_code=503, message="Push service temporarily unavailable")

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_503):
        result = await dispatcher.run_once()

    assert result.attempted_pushes == 1
    assert result.failed_notifications == 1

    # Verify notification state
    async with session_factory() as session:
        saved_notif = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved_notif.delivery_status == DeliveryStatus.FAILED
        assert saved_notif.delivery_attempts == 3
        assert "Max delivery attempts" in (saved_notif.last_delivery_error or "")
        # Core Invariant: User still has their unread in-app notification!
        assert saved_notif.status == NotificationStatus.UNREAD


# ---------------------------------------------------------------------------
# 10. ALEMBIC MIGRATION FILE VERIFICATION
# ---------------------------------------------------------------------------


def test_m16_alembic_migration_metadata() -> None:
    """Verify Alembic migration revision ID and down_revision chain."""
    migration_file = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "e6f7a8b9c0d1_m16_web_push_delivery.py"
    )
    assert migration_file.exists(), f"Migration file not found at {migration_file}"
    spec = importlib.util.spec_from_file_location("m16_migration", migration_file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "e6f7a8b9c0d1"
    assert mod.down_revision == "d5e6f7a8b9c0"
