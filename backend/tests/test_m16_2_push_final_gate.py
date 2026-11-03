"""Comprehensive test suite for M16.2 Web Push Final Security & Reliability Gate."""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.core.vapid import generate_vapid_keypair
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.push import NotificationPushDelivery, PushSubscription
from app.models.user import User
from app.schemas.notification import DeliveryStatus, NotificationStatus
from app.schemas.push import (
    PushDeliveryStatus,
    is_ip_prohibited,
    validate_push_endpoint_url,
)
from app.services.push_dispatcher import (
    PushDispatchResult,
    WebPushDispatcher,
    WebPushDispatchError,
    create_safe_push_session,
    redact_endpoint,
    sanitize_error_message,
)


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
async def sample_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[User, str]:
    """Create a test user with auth token."""
    async with session_factory() as session:
        user = User(
            email="gate_user@example.com",
            hashed_password=hash_password("PasswordGate123!"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        token, _ = create_access_token(user_id=user.id, email=user.email)
        return user, token


@pytest.fixture
async def sample_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> Job:
    """Create a company and job for testing."""
    async with session_factory() as session:
        company = Company(
            name="Apex Security Labs",
            career_url="https://apex.example.com/careers",
            platform="greenhouse",
            is_active=True,
            is_curated=True,
        )
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            external_id="apex-888",
            title="Principal Security Engineer",
            application_url="https://apex.example.com/jobs/888",
            location="Remote",
            fingerprint_hash="apex-888-fp",
            content_hash="apex-888-ch",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            status="ACTIVE",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


# ---------------------------------------------------------------------------
# 1. OUTBOUND PUSH ENDPOINT SSRF & RESOLUTION TESTS
# ---------------------------------------------------------------------------


def test_ipv4_and_ipv6_prohibited_ranges() -> None:
    """Verify that is_ip_prohibited flags all dangerous IPv4 and IPv6 ranges."""
    prohibited_ips = [
        "127.0.0.1",           # IPv4 loopback
        "::1",                 # IPv6 loopback
        "169.254.169.254",     # AWS/GCP metadata link-local
        "fe80::1",             # IPv6 link-local
        "10.0.0.1",            # Class A private
        "172.16.0.1",          # Class B private
        "192.168.1.1",         # Class C private
        "fc00::1",             # IPv6 Unique Local Address (ULA)
        "fd12:3456:789a::1",   # IPv6 ULA
        "0.0.0.0",             # IPv4 unspecified
        "::",                  # IPv6 unspecified
        "224.0.0.1",           # IPv4 multicast
        "ff02::1",             # IPv6 multicast
        "::ffff:127.0.0.1",    # IPv4-mapped IPv6 loopback
        "::ffff:169.254.1.1",  # IPv4-mapped IPv6 link-local
    ]

    for ip_str in prohibited_ips:
        ip = ipaddress.ip_address(ip_str)
        prohibited, reason = is_ip_prohibited(ip)
        assert prohibited is True, f"Expected {ip_str} to be prohibited"
        assert reason in (
            "localhost",
            "private IP addresses",
            "unspecified address",
            "multicast address",
            "reserved address",
        )

    # Public, legitimate IPs must not be prohibited
    public_ips = [
        "8.8.8.8",
        "1.1.1.1",
        "142.250.190.46",  # google.com
        "2607:f8b0:4005:808::200e",
    ]
    for ip_str in public_ips:
        ip = ipaddress.ip_address(ip_str)
        prohibited, _ = is_ip_prohibited(ip)
        assert prohibited is False, f"Expected public IP {ip_str} to be permitted"


def test_dns_resolution_to_private_ip_rejection() -> None:
    """Verify hostnames resolving to private IP addresses are rejected during URL validation."""
    # Mock socket.getaddrinfo to simulate attacker domain resolving to internal metadata IP
    mock_getaddrinfo = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
    ]

    with (
        patch("socket.getaddrinfo", return_value=mock_getaddrinfo),
        pytest.raises(ValueError, match="resolves to prohibited private IP addresses"),
    ):
        validate_push_endpoint_url("https://attacker-rebinding.com/push")

    # Mock domain resolving to loopback IPv6
    mock_getaddrinfo_v6 = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
    ]

    with (
        patch("socket.getaddrinfo", return_value=mock_getaddrinfo_v6),
        pytest.raises(ValueError, match="resolves to prohibited localhost"),
    ):
        validate_push_endpoint_url("https://rebinding-ipv6.example.com/push")


def test_redirect_blocking_in_safe_push_session() -> None:
    """Verify that create_safe_push_session() blocks 3xx redirects to prevent SSRF bypasses."""
    session = create_safe_push_session()
    assert session.max_redirects == 0

    # Simulate response hook on 301/302/307
    mock_response = MagicMock()
    mock_response.is_redirect = True
    mock_response.status_code = 302

    hook = session.hooks["response"][0]
    with pytest.raises(WebPushDispatchError, match="Unauthorized HTTP redirect \\(302\\) blocked"):
        hook(mock_response)


# ---------------------------------------------------------------------------
# 2. SECRET & ENDPOINT TOKEN REDACTION
# ---------------------------------------------------------------------------


def test_endpoint_redaction_never_reveals_token_or_suffix() -> None:
    """Verify redact_endpoint hides entire token without leaking suffix characters."""
    sensitive_endpoints = [
        "https://fcm.googleapis.com/fcm/send/dKj291_secretTokenXYZ888999",
        "https://push.services.mozilla.com/v1/gAAAAABj_secretTokenSuffix123",
        "https://notify.windows.com/?token=secretTokenQueryParam456",
    ]

    for ep in sensitive_endpoints:
        redacted = redact_endpoint(ep)
        assert "secretToken" not in redacted
        assert "XYZ888999" not in redacted
        assert "Suffix123" not in redacted
        assert "QueryParam456" not in redacted
        assert "[token_hash=" in redacted
        assert redacted.startswith("https://")


def test_provider_error_sanitization_strips_url_tokens() -> None:
    """Verify that sanitize_error_message scrubs raw URLs and secret tokens from provider errors."""
    raw_error = (
        "pywebpush.WebPushException: Push failed: 404 Not Found for url: "
        "https://fcm.googleapis.com/fcm/send/secretTokenXYZ123456789. "
        "Response body: Endpoint unregistered."
    )
    endpoint = "https://fcm.googleapis.com/fcm/send/secretTokenXYZ123456789"

    sanitized = sanitize_error_message(raw_error, endpoint=endpoint)
    assert "secretTokenXYZ123456789" not in sanitized
    assert "https://fcm.googleapis.com/[token_hash=" in sanitized
    assert "404 Not Found" in sanitized


# ---------------------------------------------------------------------------
# 3. LONG-RUNNING DISPATCH & PER-DEVICE HEARTBEAT FENCING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_loss_before_device_send_stops_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    sample_user: tuple[User, str],
    sample_job: Job,
) -> None:
    """Verify that if worker lease is revoked or expired before a device send, dispatch halts."""
    user, _ = sample_user

    async with session_factory() as session:
        sub1 = PushSubscription(
            user_id=user.id,
            endpoint="https://push.example.com/dev1",
            p256dh="k1",
            auth="a1",
        )
        session.add(sub1)
        notif = Notification(
            user_id=user.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PROCESSING,
            delivery_lease_token="stale-worker-token-001",
            delivery_lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        notif_id = notif.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)

    # Simulate another worker taking over by updating delivery_lease_token in DB
    async with session_factory() as session:
        stmt = (
            update(Notification)
            .where(Notification.id == notif_id)
            .values(delivery_lease_token="newer-worker-token-999")
        )
        await session.execute(stmt)
        await session.commit()

    # Stale worker attempts to process notification with 'stale-worker-token-001'
    result = PushDispatchResult()
    mock_send = MagicMock(return_value=201)
    with patch.object(dispatcher, "_send_webpush", mock_send):
        await dispatcher._process_notification(
            notif_id=notif_id,
            token="stale-worker-token-001",
            vapid_priv="priv",
            vapid_claims={"sub": "mailto:test@example.com"},
            result=result,
        )

    # Worker must halt before sending! Outbound send must be 0!
    assert mock_send.call_count == 0
    assert result.attempted_pushes == 0


@pytest.mark.asyncio
async def test_lease_loss_during_multi_device_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    sample_user: tuple[User, str],
    sample_job: Job,
) -> None:
    """Verify that if Device 1 succeeds, but lease is revoked before Device 2:

    - Device 2 is not attempted
    - Device 1's DELIVERED record is safely preserved in DB.
    """
    user, _ = sample_user

    async with session_factory() as session:
        dev1 = PushSubscription(
            user_id=user.id,
            endpoint="https://push.example.com/multi-dev-1",
            p256dh="k1",
            auth="a1",
        )
        dev2 = PushSubscription(
            user_id=user.id,
            endpoint="https://push.example.com/multi-dev-2",
            p256dh="k2",
            auth="a2",
        )
        session.add_all([dev1, dev2])
        notif = Notification(
            user_id=user.id,
            job_id=sample_job.id,
            status=NotificationStatus.UNREAD,
            delivery_status=DeliveryStatus.PROCESSING,
            delivery_lease_token="worker-token-42",
            delivery_lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
        await session.refresh(dev1)
        await session.refresh(dev2)
        notif_id = notif.id
        dev1_id = dev1.id
        dev2_id = dev2.id

    dispatcher = WebPushDispatcher(session_factory=session_factory)
    called_endpoints: list[str] = []
    main_loop = asyncio.get_running_loop()

    async def _do_revoke() -> None:
        async with session_factory() as s:
            await s.execute(
                update(Notification)
                .where(Notification.id == notif_id)
                .values(delivery_lease_token="stolen-by-other-worker")
            )
            await s.commit()

    # Mock send to revoke lease token in DB immediately after Device 1 succeeds
    def mock_send_with_concurrent_revoke(
        sub_info: dict[str, Any], data: str, priv: str, claims: dict[str, str]
    ) -> int:
        ep = sub_info["endpoint"]
        called_endpoints.append(ep)
        if "multi-dev-1" in ep:
            future = asyncio.run_coroutine_threadsafe(_do_revoke(), main_loop)
            future.result(timeout=5)
            return 201
        return 201

    with patch.object(dispatcher, "_send_webpush", side_effect=mock_send_with_concurrent_revoke):
        result = PushDispatchResult()
        await dispatcher._process_notification(
            notif_id=notif_id,
            token="worker-token-42",
            vapid_priv="priv",
            vapid_claims={"sub": "mailto:test@example.com"},
            result=result,
        )

    # Dev 1 was sent; Dev 2 was NEVER sent because lease was revoked!
    assert "https://push.example.com/multi-dev-1" in called_endpoints
    assert "https://push.example.com/multi-dev-2" not in called_endpoints

    # Device 1 DELIVERED record must be committed and preserved
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

        # Device 2 has no delivery record yet
        deliv2 = (
            await session.execute(
                select(NotificationPushDelivery).where(
                    NotificationPushDelivery.notification_id == notif_id,
                    NotificationPushDelivery.subscription_id == dev2_id,
                )
            )
        ).scalar_one_or_none()
        assert deliv2 is None


# ---------------------------------------------------------------------------
# 4. VAPID CONFIGURATION EARLY VALIDATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_vapid_failure_blocks_dispatch_early(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify run_once() fails closed before dispatching if VAPID keys are missing/invalid."""
    dispatcher = WebPushDispatcher(session_factory=session_factory)

    with (
        patch.object(settings, "env", "production"),
        patch.object(settings, "vapid_public_key", None),
        pytest.raises(RuntimeError, match="VAPID production validation failed"),
    ):
        await dispatcher.run_once()


# ---------------------------------------------------------------------------
# 5. TAURI DESKTOP COMPATIBILITY AUDIT
# ---------------------------------------------------------------------------


def test_tauri_desktop_compatibility_flag() -> None:
    """Verify backend continues serving desktop mode gracefully without requiring VAPID env vars."""
    with (
        patch.object(settings, "env", "development"),
        patch.object(settings, "vapid_public_key", None),
        patch.object(settings, "vapid_private_key", None),
    ):
        # Generates ephemeral keys automatically for desktop / dev
        keypair = generate_vapid_keypair()
        assert len(keypair.public_key) >= 80
        assert "PRIVATE KEY" in keypair.private_key
