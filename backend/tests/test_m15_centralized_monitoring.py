"""Comprehensive test suite for M15 Centralized Multi-Tenant Monitoring & Outbox Fan-Out."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.preference import PreferenceProfile
from app.models.subscription import UserCompanySubscription
from app.models.user import User
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.exceptions import RateLimitError, ScraperError
from app.scrapers.registry import ScraperRegistry


class MockCareerScraper(BaseScraperAdapter):
    """Configurable mock scraper for deterministic multi-tenant test scenarios."""

    name = "mock_platform"

    def __init__(self, jobs: list[RawJobPosting] | None = None) -> None:
        super().__init__()
        self.jobs = jobs or []
        self.fetch_calls: list[str] = []
        self.raise_rate_limit = False
        self.raise_scraper_error = False

    def extract_identifier(self, url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        self.fetch_calls.append(identifier)
        if self.raise_rate_limit:
            raise RateLimitError("Rate limit exceeded for testing (429)")
        if self.raise_scraper_error:
            raise ScraperError("Career page parsing error for testing (500)")
        return self.jobs


@pytest.fixture
def mock_scraper() -> MockCareerScraper:
    """Fixture providing a mock scraper with sample jobs."""
    sample_jobs = [
        RawJobPosting(
            source_ats="mock_platform",
            external_id="job-101",
            title="Senior Python Backend Engineer",
            application_url="https://mock.example.com/jobs/101",
            location="Remote",
            department="Engineering",
            description="Expert Python backend engineer building distributed systems.",
            employment_type="full_time",
            workplace_type="remote",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="mock_platform",
            external_id="job-102",
            title="Frontend React Specialist",
            application_url="https://mock.example.com/jobs/102",
            location="New York, NY",
            department="Engineering",
            description="Build rich interactive web applications in React and TypeScript.",
            employment_type="full_time",
            workplace_type="onsite",
            published_at=datetime.now(UTC),
        ),
    ]
    return MockCareerScraper(sample_jobs)


@pytest.fixture
def custom_registry(mock_scraper: MockCareerScraper) -> ScraperRegistry:
    """Registry populated with the mock scraper under mock_platform and generic."""
    reg = ScraperRegistry()
    reg.register("mock_platform", lambda **kw: mock_scraper)
    return reg


@pytest.fixture
async def orchestrator_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    custom_registry: ScraperRegistry,
) -> AsyncGenerator[MonitoringOrchestrator, None]:
    """Provide an orchestrator wired with the test session factory and mock registry."""
    orch = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=custom_registry,
        enable_pipeline_jobs=False,
    )
    yield orch


@pytest.fixture
async def m15_client(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an HTTPX AsyncClient wired to the test app and orchestrator."""
    app = create_app(
        orchestrator=orchestrator_fixture,
        session_factory=session_factory,
        start_scheduler=False,
        init_database=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver/api/v1",
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# 1. CENTRALIZED COMPANY MONITORING (SINGLE SCRAPE PER COMPANY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_scrape_per_company_with_multiple_subscribers(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
    mock_scraper: MockCareerScraper,
) -> None:
    """Verify that a company is scraped exactly once per monitoring cycle,

    even when multiple users are subscribed to it.
    """
    async with session_factory() as session:
        # Create company
        company = Company(
            name="Stripe",
            career_url="https://mock.example.com/stripe",
            platform="mock_platform",
            is_active=True,
            is_curated=True,
        )
        # Create User A & User B
        user_a = User(email="user_a@stripe.com", hashed_password="pw", is_active=True)
        user_b = User(email="user_b@stripe.com", hashed_password="pw", is_active=True)
        session.add_all([company, user_a, user_b])
        await session.flush()

        # Both subscribe to Stripe
        sub_a = UserCompanySubscription(user_id=user_a.id, company_id=company.id)
        sub_b = UserCompanySubscription(user_id=user_b.id, company_id=company.id)
        session.add_all([sub_a, sub_b])

        # User A profile: Backend
        prof_a = PreferenceProfile(
            user_id=user_a.id,
            name="Python Backend",
            preferred_roles=["Backend", "Python"],
            is_active=True,
        )
        # User B profile: Frontend
        prof_b = PreferenceProfile(
            user_id=user_b.id,
            name="React Frontend",
            preferred_roles=["Frontend", "React"],
            is_active=True,
        )
        session.add_all([prof_a, prof_b])
        await session.commit()

    # Run centralized monitoring cycle
    result = await orchestrator_fixture.run_centralized_monitoring_cycle()

    # 1. Only ONE scrape call occurred for Stripe
    assert mock_scraper.fetch_calls == ["stripe"]
    assert result.companies_targeted == 1
    assert result.companies_scraped == 1
    assert result.companies_failed == 0
    assert result.jobs_new == 2


# ---------------------------------------------------------------------------
# 2. SUBSCRIPTION-AWARE MATCHING & FAN-OUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_aware_fan_out_and_different_criteria(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
) -> None:
    """Verify that jobs fan out strictly according to matching criteria:

    - User A (Backend) receives notification candidate for Python Backend job.
    - User B (Frontend) receives notification candidate for React Frontend job.
    - Neither receives non-matching jobs.
    """
    async with session_factory() as session:
        company = Company(
            name="Datadog",
            career_url="https://mock.example.com/datadog",
            platform="mock_platform",
            is_active=True,
        )
        user_a = User(email="alice@dd.com", hashed_password="pw", is_active=True)
        user_b = User(email="bob@dd.com", hashed_password="pw", is_active=True)
        session.add_all([company, user_a, user_b])
        await session.flush()

        session.add_all(
            [
                UserCompanySubscription(user_id=user_a.id, company_id=company.id),
                UserCompanySubscription(user_id=user_b.id, company_id=company.id),
                PreferenceProfile(
                    user_id=user_a.id,
                    name="Backend Focus",
                    preferred_roles=["Backend", "Python"],
                    required_roles=["Backend"],
                    is_active=True,
                ),
                PreferenceProfile(
                    user_id=user_b.id,
                    name="Frontend Focus",
                    preferred_roles=["Frontend", "React"],
                    required_roles=["Frontend"],
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    # Execute cycle
    result = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert result.status == "completed"
    assert result.notifications_promoted == 2

    async with session_factory() as session:
        # User A should have received 1 notification for Python Backend
        notifs_a = (
            await session.execute(
                select(Notification)
                .join(Job, Job.id == Notification.job_id)
                .where(Notification.user_id == user_a.id)
            )
        ).scalars().all()
        assert len(notifs_a) == 1
        assert "Backend" in notifs_a[0].job.title

        # User B should have received 1 notification for React Frontend
        notifs_b = (
            await session.execute(
                select(Notification)
                .join(Job, Job.id == Notification.job_id)
                .where(Notification.user_id == user_b.id)
            )
        ).scalars().all()
        assert len(notifs_b) == 1
        assert "Frontend" in notifs_b[0].job.title


@pytest.mark.asyncio
async def test_same_job_matches_both_users(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
) -> None:
    """Verify that if two subscribed users both match the same job,

    both users receive their own distinct candidate notification.
    """
    async with session_factory() as session:
        company = Company(
            name="GitHub",
            career_url="https://mock.example.com/github",
            platform="mock_platform",
            is_active=True,
        )
        u1 = User(email="u1@gh.com", hashed_password="pw", is_active=True)
        u2 = User(email="u2@gh.com", hashed_password="pw", is_active=True)
        session.add_all([company, u1, u2])
        await session.flush()

        session.add_all(
            [
                UserCompanySubscription(user_id=u1.id, company_id=company.id),
                UserCompanySubscription(user_id=u2.id, company_id=company.id),
                # Both profiles seek Python
                PreferenceProfile(
                    user_id=u1.id,
                    name="U1 Python",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
                PreferenceProfile(
                    user_id=u2.id,
                    name="U2 Python",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    result = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert result.status == "completed"

    async with session_factory() as session:
        n1 = (
            await session.execute(select(Notification).where(Notification.user_id == u1.id))
        ).scalars().all()
        n2 = (
            await session.execute(select(Notification).where(Notification.user_id == u2.id))
        ).scalars().all()

        assert len(n1) == 1
        assert len(n2) == 1
        # Both received notifications for the same Python job (ID match)
        assert n1[0].job_id == n2[0].job_id


# ---------------------------------------------------------------------------
# 3. DEDUPLICATION ACROSS REPEATED CYCLES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_cycles_do_not_duplicate_jobs_or_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
    mock_scraper: MockCareerScraper,
) -> None:
    """Verify that repeated monitoring cycles do not create duplicate jobs or notifications."""
    async with session_factory() as session:
        company = Company(
            name="Vercel",
            career_url="https://mock.example.com/vercel",
            platform="mock_platform",
            is_active=True,
        )
        user = User(email="dev@vercel.com", hashed_password="pw", is_active=True)
        session.add_all([company, user])
        await session.flush()

        session.add_all(
            [
                UserCompanySubscription(user_id=user.id, company_id=company.id),
                PreferenceProfile(
                    user_id=user.id,
                    name="Dev Profile",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    # Cycle 1
    res1 = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert res1.jobs_new == 2
    assert res1.notifications_promoted == 1

    # Cycle 2 (same postings returned by scraper)
    res2 = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert res2.jobs_new == 0
    assert res2.jobs_existing == 2
    assert res2.notifications_promoted == 0

    async with session_factory() as session:
        total_jobs = (await session.execute(select(func.count(Job.id)))).scalar()
        total_notifs = (
            await session.execute(
                select(func.count(Notification.id)).where(Notification.user_id == user.id)
            )
        ).scalar()
        assert total_jobs == 2
        assert total_notifs == 1


# ---------------------------------------------------------------------------
# 4. UNSUBSCRIPTION BEHAVIOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsubscription_excludes_user_from_future_monitoring(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
    mock_scraper: MockCareerScraper,
) -> None:
    """Verify that unsubscribing immediately stops monitoring fan-out for that user."""
    async with session_factory() as session:
        company = Company(
            name="Linear",
            career_url="https://mock.example.com/linear",
            platform="mock_platform",
            is_active=True,
        )
        u_stay = User(email="stay@linear.com", hashed_password="pw", is_active=True)
        u_leave = User(email="leave@linear.com", hashed_password="pw", is_active=True)
        session.add_all([company, u_stay, u_leave])
        await session.flush()

        session.add_all(
            [
                UserCompanySubscription(user_id=u_stay.id, company_id=company.id),
                UserCompanySubscription(user_id=u_leave.id, company_id=company.id),
                PreferenceProfile(
                    user_id=u_stay.id,
                    name="P1",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
                PreferenceProfile(
                    user_id=u_leave.id,
                    name="P2",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        # Cycle 1: both are subscribed and receive notification
        await orchestrator_fixture.run_centralized_monitoring_cycle()

        # User leave unsubscribes
        unsub_stmt = delete(UserCompanySubscription).where(
            UserCompanySubscription.user_id == u_leave.id,
            UserCompanySubscription.company_id == company.id,
        )
        await session.execute(unsub_stmt)
        await session.commit()

    # Scraper now discovers a NEW third job
    mock_scraper.jobs.append(
        RawJobPosting(
            source_ats="mock_platform",
            external_id="job-103",
            title="Principal Python Architect",
            application_url="https://mock.example.com/jobs/103",
            location="Remote",
            department="Engineering",
            description="High-scale Python architecture and distributed messaging.",
            employment_type="full_time",
            workplace_type="remote",
            published_at=datetime.now(UTC),
        )
    )

    # Cycle 2
    res2 = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert res2.jobs_new == 1

    async with session_factory() as session:
        # u_stay received the new notification (total 2)
        stay_notifs = (
            await session.execute(
                select(Notification).where(Notification.user_id == u_stay.id)
            )
        ).scalars().all()
        assert len(stay_notifs) == 2

        # u_leave did NOT receive the new notification (total remains 1 from cycle 1)
        leave_notifs = (
            await session.execute(
                select(Notification).where(Notification.user_id == u_leave.id)
            )
        ).scalars().all()
        assert len(leave_notifs) == 1


# ---------------------------------------------------------------------------
# 5. USER WITH NO ACTIVE PROFILE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_with_no_profile_or_inactive_profile(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
) -> None:
    """Verify that a user subscribed to a company with no active profile

    safely produces zero candidates without errors.
    """
    async with session_factory() as session:
        company = Company(
            name="Figma",
            career_url="https://mock.example.com/figma",
            platform="mock_platform",
            is_active=True,
        )
        user = User(email="noprofile@figma.com", hashed_password="pw", is_active=True)
        session.add_all([company, user])
        await session.flush()

        # Subscribed, but profile is deactivated
        session.add_all(
            [
                UserCompanySubscription(user_id=user.id, company_id=company.id),
                PreferenceProfile(
                    user_id=user.id,
                    name="Disabled Profile",
                    preferred_roles=["Python"],
                    is_active=False,
                ),
            ]
        )
        await session.commit()

    result = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert result.status == "completed"
    assert result.jobs_new == 2
    assert result.candidates_created == 0
    assert result.notifications_promoted == 0


# ---------------------------------------------------------------------------
# 6. PARTIAL ADAPTER FAILURES (ISOLATION ACROSS COMPANIES)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_adapter_failure_does_not_abort_batch(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
    custom_registry: ScraperRegistry,
) -> None:
    """Verify that if one company scraper fails, other companies continue processing."""
    # Create failing scraper
    failing_scraper = MockCareerScraper()
    failing_scraper.name = "failing_platform"
    failing_scraper.raise_scraper_error = True
    custom_registry.register("failing_platform", lambda **kw: failing_scraper)

    async with session_factory() as session:
        c_bad = Company(
            name="BrokenCorp",
            career_url="https://mock.example.com/broken",
            platform="failing_platform",
            is_active=True,
        )
        c_good = Company(
            name="HealthyCorp",
            career_url="https://mock.example.com/healthy",
            platform="mock_platform",
            is_active=True,
        )
        user = User(email="test@mixed.com", hashed_password="pw", is_active=True)
        session.add_all([c_bad, c_good, user])
        await session.flush()

        session.add_all(
            [
                UserCompanySubscription(user_id=user.id, company_id=c_bad.id),
                UserCompanySubscription(user_id=user.id, company_id=c_good.id),
                PreferenceProfile(
                    user_id=user.id,
                    name="Python",
                    preferred_roles=["Python"],
                    required_roles=["Python"],
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    result = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert result.status == "partial_failure"
    assert result.companies_targeted == 2
    assert result.companies_scraped == 1
    assert result.companies_failed == 1
    assert result.jobs_new == 2
    assert result.notifications_promoted == 1
    assert any("BrokenCorp" in err for err in result.errors)


# ---------------------------------------------------------------------------
# 7. CRON AUTHENTICATION & OVERLAP PREVENTIONS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_endpoint_authentication_and_execution(
    m15_client: httpx.AsyncClient,
) -> None:
    """Verify that POST /api/v1/internal/cron/monitor:

    - Rejects missing or invalid credentials (401).
    - Accepts valid X-Cron-Secret or Authorization: Bearer <cron_secret>.
    - Returns structured summary.
    """
    secret = settings.cron_secret or "hawkoffer-dev-cron-secret-token"

    # 1. Missing credentials -> 401
    res_no_auth = await m15_client.post("/internal/cron/monitor")
    assert res_no_auth.status_code == 401

    # 2. Bad secret -> 401
    res_bad_auth = await m15_client.post(
        "/internal/cron/monitor",
        headers={"X-Cron-Secret": "invalid-secret-token"},
    )
    assert res_bad_auth.status_code == 401

    # 3. Valid X-Cron-Secret -> 200
    res_valid_header = await m15_client.post(
        "/internal/cron/monitor",
        headers={"X-Cron-Secret": secret},
    )
    assert res_valid_header.status_code == 200
    data = res_valid_header.json()
    assert data["status"] in ("completed", "partial_failure")
    assert "duration_ms" in data
    assert "companies_targeted" in data
    assert "jobs_new" in data
    assert "notifications_promoted" in data

    # 4. Valid Authorization Bearer cron secret -> 200
    res_valid_bearer = await m15_client.post(
        "/internal/cron/monitor",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert res_valid_bearer.status_code == 200


@pytest.mark.asyncio
async def test_cron_endpoint_overlap_protection(
    m15_client: httpx.AsyncClient,
    orchestrator_fixture: MonitoringOrchestrator,
) -> None:
    """Verify that concurrent/overlapping cron monitoring runs are rejected with 409 Conflict."""
    secret = settings.cron_secret or "hawkoffer-dev-cron-secret-token"

    # Acquire lock externally to simulate ongoing monitoring cycle
    async with orchestrator_fixture._cron_monitor_lock:
        res_overlap = await m15_client.post(
            "/internal/cron/monitor",
            headers={"X-Cron-Secret": secret},
        )
        assert res_overlap.status_code == 409
        assert "in progress" in res_overlap.json()["detail"]


# ---------------------------------------------------------------------------
# 8. LEGACY DESKTOP BACKWARD COMPATIBILITY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_desktop_profile_evaluation(
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator_fixture: MonitoringOrchestrator,
) -> None:
    """Verify that unauthenticated desktop profiles (user_id IS NULL) continue

    to evaluate and generate desktop notifications seamlessly.
    """
    async with session_factory() as session:
        # Desktop active company
        company = Company(
            name="DesktopCorp",
            career_url="https://mock.example.com/desktop",
            platform="mock_platform",
            is_active=True,
        )
        # Desktop profile with NO user_id
        profile = PreferenceProfile(
            user_id=None,
            name="Desktop Default Profile",
            preferred_roles=["Python"],
            required_roles=["Python"],
            is_active=True,
        )
        session.add_all([company, profile])
        await session.commit()

    # In desktop mode (0 subscriptions in DB), orchestrator monitors all active companies
    result = await orchestrator_fixture.run_centralized_monitoring_cycle()
    assert result.status == "completed"
    assert result.companies_targeted == 1
    assert result.notifications_promoted == 1

    async with session_factory() as session:
        notifs = (
            await session.execute(
                select(Notification).where(Notification.profile_id == profile.id)
            )
        ).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].user_id is None
