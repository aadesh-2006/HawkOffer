"""Comprehensive test suite for M16.1 Web Push Reliability & Security Hardening."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.core.vapid import (
    generate_vapid_keypair,
    get_effective_vapid_keys,
    verify_vapid_keypair_match,
)
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.push import NotificationPushDelivery, PushSubscription
from app.models.user import User
from app.schemas.notification import DeliveryStatus, NotificationStatus
from app.schemas.push import PushDeliveryStatus
from app.services.push_dispatcher import PushDispatchResult, WebPushDispatcher, WebPushDispatchError


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
            name="Quantum Systems",
            career_url="https://quantum.example.com/careers",
            platform="greenhouse",
            is_active=True,
            is_curated=True,
        )
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            external_id="qs-700",
            title="Lead Quantum Engineer",
            application_url="https://quantum.example.com/jobs/700",
            location="Remote",
            fingerprint_hash="qs-700-fp",
            content_hash="qs-700-ch",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            status="ACTIVE",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


# ---------------------------------------------------------------------------
# 1. SUBSCRIPTION OWNERSHIP & ENDPOINT SECURITY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_endpoint_takeover_rejection(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify that User B cannot take over an endpoint registered by User A (HTTP 409)."""
    user_a, user_b, token_a, token_b = sample_users
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    endpoint = "https://fcm.googleapis.com/fcm/send/shared-token-xyz"
    sub_data = {
        "endpoint": endpoint,
        "keys": {"p256dh": "key-alpha", "auth": "auth-alpha"},
        "user_agent": "Mozilla/5.0 UserA Device",
    }

    # User A registers initial subscription
    resp_a = await push_client.post("/push/subscribe", json=sub_data, headers=headers_a)
    assert resp_a.status_code == 200
    sub_a_id = resp_a.json()["id"]

    # User B attempts to register the exact same endpoint -> MUST BE REJECTED WITH 409 CONFLICT
    sub_data_b = {
        "endpoint": endpoint,
        "keys": {"p256dh": "key-beta-hijack", "auth": "auth-beta-hijack"},
        "user_agent": "Mozilla/5.0 UserB Device",
    }
    resp_b = await push_client.post("/push/subscribe", json=sub_data_b, headers=headers_b)
    assert resp_b.status_code == 409
    assert "already registered by another account" in resp_b.json()["detail"]

    # Verify User A's subscription remains unmodified
    list_a = await push_client.get("/push/subscriptions", headers=headers_a)
    subs = list_a.json()
    assert len(subs) == 1
    assert subs[0]["id"] == sub_a_id
    assert subs[0]["user_id"] == user_a.id


@pytest.mark.asyncio
async def test_same_user_subscription_idempotent_update(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify that same user registering same endpoint updates keys idempotently."""
    user_a, _, token_a, _ = sample_users
    headers_a = {"Authorization": f"Bearer {token_a}"}

    endpoint = "https://push.services.mozilla.com/v1/user-a-laptop"
    initial_data = {
        "endpoint": endpoint,
        "keys": {"p256dh": "p256-v1", "auth": "auth-v1"},
        "user_agent": "Firefox/120.0",
    }

    resp1 = await push_client.post("/push/subscribe", json=initial_data, headers=headers_a)
    assert resp1.status_code == 200
    sub_id = resp1.json()["id"]

    # Refresh keys for same user
    updated_data = {
        "endpoint": endpoint,
        "keys": {"p256dh": "p256-v2-refreshed", "auth": "auth-v2-refreshed"},
        "user_agent": "Firefox/121.0",
    }
    resp2 = await push_client.post("/push/subscribe", json=updated_data, headers=headers_a)
    assert resp2.status_code == 200
    assert resp2.json()["id"] == sub_id

    # Verify database has only 1 subscription for User A
    list_resp = await push_client.get("/push/subscriptions", headers=headers_a)
    assert len(list_resp.json()) == 1


@pytest.mark.asyncio
async def test_strict_endpoint_url_validation(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify strict URL validation rejecting non-HTTPS, credentials, and private networks."""
    _, _, token_a, _ = sample_users
    headers = {"Authorization": f"Bearer {token_a}"}
    valid_keys = {"p256dh": "key", "auth": "auth"}

    invalid_endpoints = [
        ("http://fcm.googleapis.com/fcm/send/123", "must use secure 'https' scheme"),
        ("https://user:password@fcm.googleapis.com/fcm", "must not include user credentials"),
        ("https://localhost:8080/push", "cannot target localhost"),
        ("https://127.0.0.1:8080/push", "cannot target localhost"),
        ("https://192.168.1.100/push", "cannot target private IP addresses"),
        ("https://10.0.0.1/push", "cannot target private IP addresses"),
        ("https://169.254.169.254/latest/meta-data", "cannot target private IP addresses"),
        ("ftp://push.example.com/send", "must use secure 'https' scheme"),
        ("https://", "must include a valid host"),
    ]

    for bad_url, expected_err in invalid_endpoints:
        resp = await push_client.post(
            "/push/subscribe",
            json={"endpoint": bad_url, "keys": valid_keys},
            headers=headers,
        )
        assert resp.status_code == 422, f"Expected 422 for bad endpoint '{bad_url}'"
        assert expected_err in resp.text


@pytest.mark.asyncio
async def test_user_scoped_unsubscribe_boundary(
    push_client: httpx.AsyncClient,
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify unsubscribe is strictly user-scoped and returns 404 for another user's endpoint."""
    user_a, user_b, token_a, token_b = sample_users
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User B registers endpoint
    endpoint_b = "https://push.services.mozilla.com/v1/user-b-endpoint"
    await push_client.post(
        "/push/subscribe",
        json={"endpoint": endpoint_b, "keys": {"p256dh": "k", "auth": "a"}},
        headers=headers_b,
    )

    # User A attempts to unsubscribe User B's endpoint -> 404 Not Found
    unsub_resp = await push_client.post(
        "/push/unsubscribe",
        json={"endpoint": endpoint_b},
        headers=headers_a,
    )
    assert unsub_resp.status_code == 404
    assert "not owned by current user" in unsub_resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. ATOMIC NOTIFICATION CLAIMING & LEASE FENCING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_dispatch_claim_mutual_exclusion(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
) -> None:
    """Verify two independent dispatchers claiming simultaneously receive mutually disjoint sets."""
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        comp = Company(
            name="Mutex Co", career_url="https://mutex.com", platform="ashby", is_active=True
        )
        session.add(comp)
        await session.flush()

        for i in range(5):
            j = Job(
                company_id=comp.id,
                external_id=f"mutex-job-{i}",
                title=f"Engineer {i}",
                application_url=f"https://mutex.com/{i}",
                fingerprint_hash=f"fp-mutex-{i}",
                content_hash=f"ch-mutex-{i}",
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

    dispatcher1 = WebPushDispatcher(session_factory=session_factory, batch_size=3)
    dispatcher2 = WebPushDispatcher(session_factory=session_factory, batch_size=3)

    async with session_factory() as s1:
        claimed1, token1 = await dispatcher1._claim_pending_notifications(s1)

    async with session_factory() as s2:
        claimed2, token2 = await dispatcher2._claim_pending_notifications(s2)

    assert len(claimed1) == 3
    assert len(claimed2) == 2
    assert set(claimed1).isdisjoint(set(claimed2))
    assert token1 != token2

    # A third claim attempt should find 0 available notifications
    async with session_factory() as s3:
        claimed3, _ = await dispatcher1._claim_pending_notifications(s3)
    assert len(claimed3) == 0


@pytest.mark.asyncio
async def test_abandoned_processing_notification_recovery(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify notifications left in PROCESSING by dead workers recover after TTL expires."""
    user_a, _, _, _ = sample_users

    # Notification in PROCESSING with expired lease
    past_expiration = datetime.now(UTC) - timedelta(seconds=60)
    async with session_factory() as session:
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PROCESSING,
            delivery_lease_token="stale-dead-worker-token",
            delivery_lease_expires_at=past_expiration,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)
    async with session_factory() as s:
        recovered_ids, new_token = await dispatcher._claim_pending_notifications(s)

    assert notif_id in recovered_ids
    assert new_token != "stale-dead-worker-token"

    # Verify notification has updated lease in DB
    async with session_factory() as session:
        saved = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved.delivery_status == DeliveryStatus.PROCESSING
        assert saved.delivery_lease_token == new_token
        expires_at = saved.delivery_lease_expires_at
        assert expires_at is not None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        assert expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_stale_claim_owner_fencing_rejection(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify expired worker cannot finalize state if another worker reclaimed the lease."""
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PROCESSING,
            delivery_lease_token="newer-worker-token-999",  # Active lease owned by worker 2
            delivery_lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)

    # Worker 1 (holding stale token 'stale-token-001') attempts to process notification
    result = PushDispatchResult()
    await dispatcher._process_notification(
        notif_id=notif_id,
        token="stale-token-001",
        vapid_priv="dummy_priv",
        vapid_claims={"sub": "mailto:test@example.com"},
        result=result,
    )

    # State in database must remain unmodified with worker 2's token!
    async with session_factory() as session:
        saved = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved.delivery_lease_token == "newer-worker-token-999"
        assert saved.delivery_status == DeliveryStatus.PROCESSING


# ---------------------------------------------------------------------------
# 3. DEVICE-LEVEL STATUS SEMANTICS & EXHAUSTION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_level_status_semantics_and_exhaustion(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Verify distinct device statuses: DELIVERED, TRANSIENT_FAILURE, and EXHAUSTED."""
    user_a, _, _, _ = sample_users

    async with session_factory() as session:
        dev1 = PushSubscription(
            user_id=user_a.id,
            endpoint="https://push.example.com/device-reliable",
            p256dh="key1",
            auth="auth1",
        )
        dev2 = PushSubscription(
            user_id=user_a.id,
            endpoint="https://push.example.com/device-flaky",
            p256dh="key2",
            auth="auth2",
        )
        session.add_all([dev1, dev2])
        notif = Notification(
            user_id=user_a.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PENDING,
            delivery_attempts=0,
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        await session.refresh(dev1)
        await session.refresh(dev2)
        notif_id = notif.id
        dev1_id = dev1.id
        dev2_id = dev2.id

    dispatcher = WebPushDispatcher(session_factory=session_factory, max_attempts=2)

    # First run: dev1 succeeds, dev2 fails transiently (HTTP 503)
    def mock_send_run1(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        if "device-reliable" in sub_info["endpoint"]:
            return 201
        raise WebPushDispatchError(status_code=503, message="Service Unavailable")

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_run1):
        await dispatcher.run_once()

    # Check device delivery records after run 1
    async with session_factory() as session:
        deliv1 = (
            await session.execute(
                select(NotificationPushDelivery).where(
                    NotificationPushDelivery.notification_id == notif_id,
                    NotificationPushDelivery.subscription_id == dev1_id,
                )
            )
        ).scalar_one()
        assert deliv1.status == PushDeliveryStatus.DELIVERED.value

        deliv2 = (
            await session.execute(
                select(NotificationPushDelivery).where(
                    NotificationPushDelivery.notification_id == notif_id,
                    NotificationPushDelivery.subscription_id == dev2_id,
                )
            )
        ).scalar_one()
        # Device 2 has TRANSIENT_FAILURE (not permanently failed yet)
        assert deliv2.status == PushDeliveryStatus.TRANSIENT_FAILURE.value

        notif_mid = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert notif_mid.delivery_status == DeliveryStatus.RETRY
        assert notif_mid.delivery_attempts == 1
        notif_mid.next_delivery_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    # Second run: dev2 fails again (hitting max_attempts=2) -> becomes EXHAUSTED
    def mock_send_run2(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        raise WebPushDispatchError(status_code=503, message="Service Unavailable")

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_run2):
        await dispatcher.run_once()

    # Check device delivery records after run 2
    async with session_factory() as session:
        deliv2_final = (
            await session.execute(
                select(NotificationPushDelivery).where(
                    NotificationPushDelivery.notification_id == notif_id,
                    NotificationPushDelivery.subscription_id == dev2_id,
                )
            )
        ).scalar_one()
        assert deliv2_final.status == PushDeliveryStatus.EXHAUSTED.value

        notif_final = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert notif_final.delivery_status == DeliveryStatus.FAILED
        assert notif_final.delivery_attempts == 2
        # Core Invariant: IN-APP NOTIFICATION REMAINS UNREAD
        assert notif_final.status == NotificationStatus.UNREAD


# ---------------------------------------------------------------------------
# 4. VAPID CRYPTOGRAPHIC VERIFICATION & MATCHING
# ---------------------------------------------------------------------------


def test_vapid_keypair_verification_and_mismatch_rejection() -> None:
    """Verify verify_vapid_keypair_match approves valid pairs and rejects mismatched keys."""
    pair_a = generate_vapid_keypair()
    pair_b = generate_vapid_keypair()

    # 1. Matching pair -> Valid
    valid_a, msg_a = verify_vapid_keypair_match(pair_a.public_key, pair_a.private_key)
    assert valid_a is True
    assert msg_a == "Valid"

    # 2. Mismatched pair (Public A with Private B) -> Rejected
    valid_mismatch, msg_mismatch = verify_vapid_keypair_match(pair_a.public_key, pair_b.private_key)
    assert valid_mismatch is False
    assert "does not correspond" in msg_mismatch

    # 3. Malformed public key (not 65 bytes or bad base64) -> Rejected
    valid_bad_pub, msg_bad_pub = verify_vapid_keypair_match("invalid_base64!", pair_a.private_key)
    assert valid_bad_pub is False

    # 4. Malformed private key -> Rejected
    valid_bad_priv, msg_bad_priv = verify_vapid_keypair_match(pair_a.public_key, "not_a_pem_key")
    assert valid_bad_priv is False

    # 5. Non-SECP256R1 curve rejected
    secp384_priv = ec.generate_private_key(ec.SECP384R1())
    pem_384 = secp384_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    valid_curve, msg_curve = verify_vapid_keypair_match(pair_a.public_key, pem_384)
    assert valid_curve is False
    assert "NIST P-256" in msg_curve


def test_get_effective_vapid_keys_runtime_rejection() -> None:
    """Verify get_effective_vapid_keys raises RuntimeError when keys are mismatched."""
    pair_a = generate_vapid_keypair()
    pair_b = generate_vapid_keypair()

    with (
        patch.object(settings, "vapid_public_key", pair_a.public_key),
        patch.object(settings, "vapid_private_key", pair_b.private_key),
        pytest.raises(RuntimeError, match="Configured VAPID keypair is invalid or mismatched"),
    ):
        get_effective_vapid_keys()


# ---------------------------------------------------------------------------
# 5. IN-APP NOTIFICATION INVARIANT AUDIT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_app_notification_never_modified_by_push_failures(
    session_factory: async_sessionmaker[AsyncSession],
    sample_users: tuple[User, User, str, str],
    sample_job: Job,
) -> None:
    """Audit that through 0-sub and push failures, in-app status is UNTOUCHED."""
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
    await dispatcher.run_once()

    # User had 0 subs: delivery_status is NOT_REQUIRED, but status is UNREAD
    async with session_factory() as session:
        saved = (
            await session.execute(select(Notification).where(Notification.id == notif_id))
        ).scalar_one()
        assert saved.delivery_status == DeliveryStatus.NOT_REQUIRED
        assert saved.status == NotificationStatus.UNREAD
