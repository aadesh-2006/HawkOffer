"""Test suite for M15.1 Production Safety & Multi-Tenant Monitoring Hardening."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.main import create_app
from app.models.company import Company
from app.models.lease import DistributedLease
from app.models.notification import Notification
from app.models.preference import PreferenceProfile
from app.models.subscription import UserCompanySubscription
from app.models.user import User
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.registry import ScraperRegistry
from app.services.lease_service import DistributedLeaseService


class HardeningMockScraper(BaseScraperAdapter):
    """Mock scraper producing deterministic job postings."""

    name = "mock_hardening"

    def __init__(self, jobs: list[RawJobPosting] | None = None) -> None:
        super().__init__()
        self.jobs = jobs or []
        self.fetch_calls: list[str] = []

    def extract_identifier(self, url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        self.fetch_calls.append(identifier)
        return self.jobs


@pytest.fixture
def hardening_scraper() -> HardeningMockScraper:
    sample_jobs = [
        RawJobPosting(
            source_ats="mock_hardening",
            external_id="h-101",
            title="Senior Distributed Systems Architect",
            application_url="https://mock.example.com/jobs/h101",
            location="Remote",
            department="Infrastructure",
            description="Build scalable distributed systems with robust lease coordination.",
            employment_type="full_time",
            workplace_type="remote",
            published_at=datetime.now(UTC),
        ),
    ]
    return HardeningMockScraper(sample_jobs)


@pytest.fixture
def hardening_registry(hardening_scraper: HardeningMockScraper) -> ScraperRegistry:
    reg = ScraperRegistry()
    reg.register("mock_hardening", lambda **kw: hardening_scraper)
    return reg


@pytest.fixture
async def hardening_orchestrator(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_registry: ScraperRegistry,
) -> AsyncGenerator[MonitoringOrchestrator, None]:
    orch = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=hardening_registry,
        enable_pipeline_jobs=False,
    )
    yield orch


@pytest.fixture
async def hardening_client(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app(
        orchestrator=hardening_orchestrator,
        session_factory=session_factory,
        start_scheduler=False,
        init_database=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver/api/v1",
    ) as client:
        yield client


# ===========================================================================
# 1. CRON SECRET SECURITY & FAIL-CLOSED VALIDATION
# ===========================================================================


def test_production_cron_secret_strength_validation() -> None:
    """Verify that production environments reject missing, weak, or default secrets."""
    # 1. Missing secret
    s_missing = Settings(env="production", cron_secret=None)
    is_valid, reason = s_missing.validate_production_cron_secret()
    assert not is_valid
    assert "not configured" in reason

    # 2. Too short (< 32 chars)
    s_short = Settings(env="production", cron_secret="too-short-secret")
    is_valid, reason = s_short.validate_production_cron_secret()
    assert not is_valid
    assert "at least 32 characters" in reason

    # 3. Known development default
    s_dev_default = Settings(
        env="production",
        cron_secret="hawkoffer-dev-cron-secret-token",
    )
    is_valid, reason = s_dev_default.validate_production_cron_secret()
    assert not is_valid
    assert "insecure development default" in reason

    # 4. Valid strong secret (>= 32 chars and not in known weak list)
    strong_token = "0123456789abcdef0123456789abcdef0123456789abcdef"
    s_strong = Settings(env="production", cron_secret=strong_token)
    is_valid, reason = s_strong.validate_production_cron_secret()
    assert is_valid
    assert reason == "Valid"


@pytest.mark.asyncio
async def test_cron_endpoint_fails_closed_in_production(
    hardening_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that in production, endpoints return 503 if the secret is weak or missing."""
    # Simulate production environment with missing secret
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "cron_secret", None)

    res = await hardening_client.post("/internal/cron/monitor")
    assert res.status_code == 503
    assert "disabled" in res.json()["detail"].lower()

    # Simulate production environment with weak secret
    monkeypatch.setattr(settings, "cron_secret", "weak-secret")
    res_weak = await hardening_client.post(
        "/internal/cron/monitor",
        headers={"X-Cron-Secret": "weak-secret"},
    )
    assert res_weak.status_code == 503
    assert "disabled" in res_weak.json()["detail"].lower()

    # Simulate production with valid strong secret
    strong_token = "production-super-strong-secret-token-32-chars-long"
    monkeypatch.setattr(settings, "cron_secret", strong_token)

    # Wrong credential provided -> 401 Unauthorized
    res_bad = await hardening_client.post(
        "/internal/cron/monitor",
        headers={"X-Cron-Secret": "wrong-secret-token-0123456789abcdef"},
    )
    assert res_bad.status_code == 401

    # Correct credential provided -> 200 OK
    res_ok = await hardening_client.post(
        "/internal/cron/monitor",
        headers={"X-Cron-Secret": strong_token},
    )
    assert res_ok.status_code == 200


# ===========================================================================
# 2. CROSS-PROCESS DISTRIBUTED LEASE COORDINATION
# ===========================================================================


@pytest.mark.asyncio
async def test_distributed_lease_mutual_exclusion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that two processes competing for the same lease:

    - Only one process acquires the lease.
    - The second process is rejected.
    """
    svc1 = DistributedLeaseService(session_factory)
    svc2 = DistributedLeaseService(session_factory)

    # Process 1 acquires lease
    token1 = await svc1.acquire_lease("test_lease", ttl_seconds=60)
    assert token1 is not None

    # Process 2 attempts to acquire same lease
    token2 = await svc2.acquire_lease("test_lease", ttl_seconds=60)
    assert token2 is None

    # Process 1 releases lease
    released = await svc1.release_lease("test_lease", token1)
    assert released is True

    # Process 2 can now acquire the lease
    token2_after = await svc2.acquire_lease("test_lease", ttl_seconds=60)
    assert token2_after is not None

    # Cleanup
    await svc2.release_lease("test_lease", token2_after)


@pytest.mark.asyncio
async def test_distributed_lease_expiration_and_crash_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that if a process crashes and abandons its lease,

    the lease expires and a subsequent process safely takes over.
    """
    svc = DistributedLeaseService(session_factory)

    # Acquire lease with 1-second TTL
    token1 = await svc.acquire_lease("crash_test_lease", ttl_seconds=1)
    assert token1 is not None

    # Immediate second attempt fails
    assert await svc.acquire_lease("crash_test_lease", ttl_seconds=1) is None

    # Wait past TTL expiry (simulating process crash without release)
    await asyncio.sleep(1.2)

    # Process 2 attempts acquisition -> succeeds via crash recovery
    token2 = await svc.acquire_lease("crash_test_lease", ttl_seconds=60)
    assert token2 is not None
    assert token2 != token1

    # Cleanup
    await svc.release_lease("crash_test_lease", token2)


@pytest.mark.asyncio
async def test_stale_former_owner_cannot_release_newer_owner_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that an expired former owner cannot release a newer owner's lease."""
    svc = DistributedLeaseService(session_factory)

    # Owner 1 acquires short lease
    token1 = await svc.acquire_lease("stale_owner_lease", ttl_seconds=1)
    assert token1 is not None

    # Wait for expiry
    await asyncio.sleep(1.2)

    # Owner 2 takes over the expired lease
    token2 = await svc.acquire_lease("stale_owner_lease", ttl_seconds=60)
    assert token2 is not None

    # Owner 1 wakes up and attempts to release its old lease
    stale_release = await svc.release_lease("stale_owner_lease", token1)
    assert stale_release is False

    # Verify Owner 2's lease is still active and owned by token2
    async with session_factory() as session:
        lease = await session.scalar(
            select(DistributedLease).where(DistributedLease.name == "stale_owner_lease")
        )
        assert lease is not None
        assert lease.owner_token == token2
        assert lease.released_at is None

    # Owner 2 releases cleanly
    assert await svc.release_lease("stale_owner_lease", token2) is True


# ===========================================================================
# 3. DESKTOP PROFILE ISOLATION & BOUNDARY RULES
# ===========================================================================


@pytest.mark.asyncio
async def test_desktop_profile_isolated_from_private_saas_company(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that in a mixed database, legacy desktop profiles (user_id IS NULL)

    do NOT receive jobs from private companies created by SaaS users.
    """
    monkeypatch.setattr(settings, "auth_required", False)

    async with session_factory() as session:
        # SaaS user creates private company
        saas_user = User(email="saas_owner@domain.com", hashed_password="pw", is_active=True)
        session.add(saas_user)
        await session.flush()

        private_company = Company(
            name="PrivateStartup",
            career_url="https://mock.example.com/privatestartup",
            platform="mock_hardening",
            is_active=True,
            is_curated=False,
            created_by_user_id=saas_user.id,
        )
        session.add(private_company)
        await session.flush()

        # SaaS user subscribes
        sub = UserCompanySubscription(user_id=saas_user.id, company_id=private_company.id)
        # SaaS user profile
        saas_prof = PreferenceProfile(
            user_id=saas_user.id,
            name="SaaS Profile",
            preferred_roles=["Architect"],
            is_active=True,
        )
        # Legacy desktop profile (user_id IS NULL)
        desktop_prof = PreferenceProfile(
            user_id=None,
            name="Desktop Legacy Profile",
            preferred_roles=["Architect"],
            is_active=True,
        )
        session.add_all([sub, saas_prof, desktop_prof])
        await session.commit()

    # Execute cycle
    result = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert result.status == "completed"

    async with session_factory() as session:
        # SaaS user received notification
        saas_notifs = (
            await session.execute(
                select(Notification).where(Notification.user_id == saas_user.id)
            )
        ).scalars().all()
        assert len(saas_notifs) == 1

        # Desktop profile did NOT receive notification for private SaaS company
        desktop_notifs = (
            await session.execute(
                select(Notification).where(Notification.profile_id == desktop_prof.id)
            )
        ).scalars().all()
        assert len(desktop_notifs) == 0


@pytest.mark.asyncio
async def test_saas_mode_strictly_suppresses_legacy_desktop_profiles(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that when auth_required=True, unauthenticated desktop profiles

    never receive any notifications even for curated companies.
    """
    monkeypatch.setattr(settings, "auth_required", True)

    async with session_factory() as session:
        curated_comp = Company(
            name="PublicCurated",
            career_url="https://mock.example.com/publiccurated",
            platform="mock_hardening",
            is_active=True,
            is_curated=True,
        )
        saas_user = User(email="subscriber@saas.com", hashed_password="pw", is_active=True)
        session.add_all([curated_comp, saas_user])
        await session.flush()

        sub = UserCompanySubscription(user_id=saas_user.id, company_id=curated_comp.id)
        saas_prof = PreferenceProfile(
            user_id=saas_user.id,
            name="Subscriber Prof",
            preferred_roles=["Architect"],
            is_active=True,
        )
        desktop_prof = PreferenceProfile(
            user_id=None,
            name="Orphaned Desktop Prof",
            preferred_roles=["Architect"],
            is_active=True,
        )
        session.add_all([sub, saas_prof, desktop_prof])
        await session.commit()

    result = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert result.status == "completed"

    async with session_factory() as session:
        # SaaS user received 1
        saas_notifs = (
            await session.execute(
                select(Notification).where(Notification.user_id == saas_user.id)
            )
        ).scalars().all()
        assert len(saas_notifs) == 1

        # Legacy desktop profile received 0 in SaaS mode
        desktop_notifs = (
            await session.execute(
                select(Notification).where(Notification.profile_id == desktop_prof.id)
            )
        ).scalars().all()
        assert len(desktop_notifs) == 0


# ===========================================================================
# 4. MONITORING TARGET SELECTION SEMANTICS
# ===========================================================================


@pytest.mark.asyncio
async def test_curated_companies_without_subscribers_behavior(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that curated companies with 0 subscribers are:

    - Excluded when monitor_curated_without_subscribers=False.
    - Included when monitor_curated_without_subscribers=True.
    """
    monkeypatch.setattr(settings, "auth_required", True)

    async with session_factory() as session:
        comp = Company(
            name="CuratedZeroSubs",
            career_url="https://mock.example.com/zero",
            platform="mock_hardening",
            is_active=True,
            is_curated=True,
        )
        session.add(comp)
        await session.commit()

    # 1. With monitor_curated_without_subscribers=False -> NOT targeted
    monkeypatch.setattr(settings, "monitor_curated_without_subscribers", False)
    res_default = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert res_default.companies_targeted == 0

    # 2. With monitor_curated_without_subscribers=True -> targeted!
    monkeypatch.setattr(settings, "monitor_curated_without_subscribers", True)
    res_enabled = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert res_enabled.companies_targeted == 1
    assert res_enabled.companies_scraped == 1


@pytest.mark.asyncio
async def test_inactive_subscribers_do_not_trigger_monitoring(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that a company whose only subscribers are inactive users (is_active=False)

    is NOT treated as active monitoring demand.
    """
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "monitor_curated_without_subscribers", False)

    async with session_factory() as session:
        comp = Company(
            name="GhostTarget",
            career_url="https://mock.example.com/ghost",
            platform="mock_hardening",
            is_active=True,
            is_curated=False,
        )
        inactive_user = User(
            email="inactive@corp.com",
            hashed_password="pw",
            is_active=False,  # INACTIVE USER
        )
        session.add_all([comp, inactive_user])
        await session.flush()

        sub = UserCompanySubscription(user_id=inactive_user.id, company_id=comp.id)
        session.add(sub)
        await session.commit()

    result = await hardening_orchestrator.run_centralized_monitoring_cycle()
    # Company should not be targeted because user is inactive
    assert result.companies_targeted == 0


@pytest.mark.asyncio
async def test_saas_user_with_no_subscriptions_receives_no_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that a user who has active preference profiles but has subscribed

    to zero companies receives no notifications when other companies are scraped.
    """
    monkeypatch.setattr(settings, "auth_required", True)

    async with session_factory() as session:
        comp = Company(
            name="SubscribedByOther",
            career_url="https://mock.example.com/other",
            platform="mock_hardening",
            is_active=True,
            is_curated=False,
        )
        subscriber = User(email="sub@corp.com", hashed_password="pw", is_active=True)
        unsubscribed_user = User(email="nosub@corp.com", hashed_password="pw", is_active=True)
        session.add_all([comp, subscriber, unsubscribed_user])
        await session.flush()

        sub = UserCompanySubscription(user_id=subscriber.id, company_id=comp.id)
        prof_sub = PreferenceProfile(
            user_id=subscriber.id,
            name="Sub Prof",
            preferred_roles=["Architect"],
            is_active=True,
        )
        prof_nosub = PreferenceProfile(
            user_id=unsubscribed_user.id,
            name="NoSub Prof",
            preferred_roles=["Architect"],
            is_active=True,
        )
        session.add_all([sub, prof_sub, prof_nosub])
        await session.commit()

    result = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert result.status == "completed"

    async with session_factory() as session:
        # Unsubscribed user has 0 notifications
        notifs = (
            await session.execute(
                select(Notification).where(Notification.user_id == unsubscribed_user.id)
            )
        ).scalars().all()
        assert len(notifs) == 0


# ===========================================================================
# 5. CROSS-PROCESS CRON OVERLAP & 409 CONFLICT
# ===========================================================================


@pytest.mark.asyncio
async def test_cron_endpoint_cross_process_lease_conflict_409(
    hardening_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that if another process holds the distributed lease,

    the internal cron endpoint returns 409 Conflict.
    """
    lease_svc = DistributedLeaseService(session_factory)
    token = await lease_svc.acquire_lease("centralized_monitoring", ttl_seconds=60)
    assert token is not None

    try:
        secret = settings.cron_secret or "hawkoffer-dev-cron-secret-token"
        res = await hardening_client.post(
            "/internal/cron/monitor",
            headers={"X-Cron-Secret": secret},
        )
        assert res.status_code == 409
        assert "in progress" in res.json()["detail"].lower()
    finally:
        await lease_svc.release_lease("centralized_monitoring", token)


@pytest.mark.asyncio
async def test_partial_pipeline_stage_failure_reporting(
    session_factory: async_sessionmaker[AsyncSession],
    hardening_orchestrator: MonitoringOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that if an internal worker stage fails (e.g. DiscoveryWorker),

    the monitoring cycle accurately reports 'partial_failure' with error diagnostics
    and does not falsely report 'completed'.
    """
    async with session_factory() as session:
        comp = Company(
            name="ValidCo",
            career_url="https://mock.example.com/validco",
            platform="mock_hardening",
            is_active=True,
            is_curated=True,
        )
        user = User(email="test@user.com", hashed_password="pw", is_active=True)
        session.add_all([comp, user])
        await session.flush()
        session.add(UserCompanySubscription(user_id=user.id, company_id=comp.id))
        await session.commit()

    # Simulate DiscoveryWorker failure
    async def mock_failed_discovery() -> None:
        raise RuntimeError("Simulated outbox worker database failure")

    monkeypatch.setattr(
        hardening_orchestrator.discovery_worker,
        "run_once",
        mock_failed_discovery,
    )

    result = await hardening_orchestrator.run_centralized_monitoring_cycle()
    assert result.status == "partial_failure"
    assert "partial failures" in result.message.lower()
    assert any("discovery" in err.lower() for err in result.errors)


def test_alembic_m15_1_migration_lifecycle() -> None:
    """Verify Alembic runs up to head (including m15.1 distributed leases),

    downgrades, and upgrades cleanly.
    """
    import tempfile
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        clean_path = Path(db_path).as_posix()
        url = f"sqlite:///{clean_path}"

        alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", url)

        # 1. Upgrade to head (includes m15.1 distributed leases)
        command.upgrade(alembic_cfg, "head")

        # 2. Downgrade by 1 (rolls back m15.1)
        command.downgrade(alembic_cfg, "-1")

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")
    finally:
        if Path(db_path).exists():
            Path(db_path).unlink()


