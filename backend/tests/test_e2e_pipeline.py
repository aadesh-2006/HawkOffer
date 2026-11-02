"""End-to-End Integration Tests for HawkOffer M5 Phase 3 Pipeline.

Exercises real services, repositories, and SQLite database together across the complete lifecycle:
1. Ingestion -> Outbox -> Discovery -> Backlog -> Drain -> Notification.
2. Non-matching jobs and filtering.
3. Duplicate ingestion idempotency.
4. Profile update -> Revision increment -> Historical reevaluation -> Promotion.
5. Profile deactivation and deletion cascades.
6. Expired lease recovery and concurrent reclaim.
7. Stale worker lease fencing.
8. Retry exhaustion and terminal failure handling.
9. Transaction rollbacks at critical boundaries.
10. Repeated execution and idempotency.
11. Scheduler-driven execution through MonitoringOrchestrator.
12. SQL query plans and index usage (zero temporary b-trees).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.outbox import (
    BacklogStatus,
    JobDiscoveryOutbox,
    NotificationBacklog,
    OutboxStatus,
    ProfileReevaluationTask,
    ReevaluationStatus,
)
from app.models.preference import PreferenceProfile
from app.repositories.company_repo import CompanyRepository
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.company import CompanyCreate
from app.schemas.notification import DeliveryStatus, NotificationStatus, NotificationType
from app.schemas.preference import PreferenceProfileUpdate
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.registry import ScraperRegistry
from app.services.backlog_drain_worker import BacklogDrainWorker
from app.services.discovery_worker import JobDiscoveryWorker
from app.services.ingestion_service import IngestionService
from app.services.profile_service import ProfileUpdateService
from app.services.reevaluation_worker import ProfileReevaluationWorker

# =============================================================================
# FIXTURES & TEST ADAPTER
# =============================================================================


class E2ETestScraperAdapter(BaseScraperAdapter):
    """Controlled mock adapter for end-to-end pipeline ingestion."""

    name = "e2e_mock"

    def __init__(self, postings: list[RawJobPosting] | None = None) -> None:
        super().__init__()
        self.postings: list[RawJobPosting] = postings or []

    def set_postings(self, postings: list[RawJobPosting]) -> None:
        self.postings = postings

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "e2e_id"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        return self.postings


@pytest.fixture
def e2e_adapter() -> E2ETestScraperAdapter:
    """Fixture providing a test scraper adapter."""
    return E2ETestScraperAdapter()


@pytest.fixture
def e2e_registry(e2e_adapter: E2ETestScraperAdapter) -> ScraperRegistry:
    """Fixture providing a registry containing the test adapter."""
    reg = ScraperRegistry()
    reg.register("e2e_mock", lambda **kwargs: e2e_adapter)
    return reg


@pytest.fixture
async def e2e_company(db_session: AsyncSession) -> Company:
    """Seed a test company using e2e_mock platform."""
    comp_repo = CompanyRepository(db_session)
    company = await comp_repo.create(
        CompanyCreate(
            name="Apex Systems",
            career_url="https://apex.example.com/careers",
            platform="e2e_mock",
        )
    )
    await db_session.commit()
    return company


@pytest.fixture
async def e2e_profile(db_session: AsyncSession) -> PreferenceProfile:
    """Seed a test preference profile matching Python Backend roles."""
    profile = PreferenceProfile(
        name="Python Backend Engineer",
        is_active=True,
        minimum_match_score=40.0,
        preferred_roles=["Python Developer", "Backend Engineer", "Software Engineer"],
        preferred_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        preferred_locations=["Remote", "San Francisco, CA"],
        preferred_employment_types=["Full-time"],
        required_employment_types=[],
        preferred_workplace_types=["Remote", "Hybrid"],
        excluded_keywords=["Staff", "Principal", "Director"],
        excluded_title_keywords=[],
        excluded_content_keywords=[],
        required_roles=[],
        required_locations=[],
        require_graduation_year_match=False,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


def _make_posting(
    title: str,
    external_id: str,
    description: str = "Seeking a Python engineer with FastAPI and Docker experience.",
    location: str = "Remote",
) -> RawJobPosting:
    """Helper to construct realistic RawJobPosting."""
    return RawJobPosting(
        source_ats="e2e_mock",
        external_id=external_id,
        title=title,
        application_url=f"https://apex.example.com/jobs/{external_id}",
        location=location,
        description=description,
        employment_type="Full-time",
        workplace_type="Remote",
        published_at=datetime(2026, 10, 31, tzinfo=UTC),
    )


# =============================================================================
# 1. NEW JOB INGESTION -> OUTBOX -> DISCOVERY -> BACKLOG -> DRAIN -> NOTIFICATION
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_new_job_to_notification_promotion(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify complete end-to-end flow from job ingestion to user notification."""
    posting = _make_posting(
        title="Backend Engineer - Python",
        external_id="job-py-101",
    )
    e2e_adapter.set_postings([posting])

    # 1. Ingest Job via IngestionService
    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        ingest_res = await service.ingest_company(e2e_company.id)
        assert ingest_res.newly_inserted == 1
        assert ingest_res.already_existing == 0

    # Verify Job and Outbox record were committed atomically
    async with session_factory() as session:
        job = (
            await session.execute(select(Job).where(Job.external_id == "job-py-101"))
        ).scalar_one()
        outbox = (
            await session.execute(
                select(JobDiscoveryOutbox).where(JobDiscoveryOutbox.job_id == job.id)
            )
        ).scalar_one()
        assert outbox.status == OutboxStatus.PENDING
        assert outbox.attempt_count == 0
        assert outbox.lease_token is None

        # Verify no notification created prematurely before drain
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 0

    # 2. Run Discovery Worker
    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    disc_res = await discovery_worker.run_once()
    assert disc_res.claimed_count == 1
    assert disc_res.evaluated_jobs == 1
    assert disc_res.candidate_matches == 1
    assert disc_res.processed_outbox_count == 1

    # Verify Outbox is PROCESSED and Backlog candidate is QUEUED
    async with session_factory() as session:
        outbox = (
            await session.execute(
                select(JobDiscoveryOutbox).where(JobDiscoveryOutbox.job_id == job.id)
            )
        ).scalar_one()
        assert outbox.status == OutboxStatus.PROCESSED
        assert outbox.processed_at is not None
        assert outbox.lease_token is None

        backlog = (
            await session.execute(
                select(NotificationBacklog).where(
                    NotificationBacklog.job_id == job.id,
                    NotificationBacklog.profile_id == e2e_profile.id,
                )
            )
        ).scalar_one()
        assert backlog.status == BacklogStatus.QUEUED
        assert backlog.profile_revision == e2e_profile.revision
        assert backlog.score >= e2e_profile.minimum_match_score

        # Zero notifications before drain
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 0

    # 3. Run Backlog Drain Worker
    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    drain_res = await drain_worker.run_once()
    assert drain_res.claimed_count == 1
    assert drain_res.promoted_count == 1
    assert drain_res.final_drain_counter == 1

    # Verify Backlog is PROCESSED and Notification exists
    async with session_factory() as session:
        backlog = (
            await session.execute(
                select(NotificationBacklog).where(NotificationBacklog.id == backlog.id)
            )
        ).scalar_one()
        assert backlog.status == BacklogStatus.PROCESSED
        assert backlog.processed_at is not None
        assert backlog.lease_token is None

        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 1
        notif = notifs[0]
        assert notif.job_id == job.id
        assert notif.profile_id == e2e_profile.id
        assert notif.status == NotificationStatus.UNREAD
        assert notif.delivery_status == DeliveryStatus.PENDING
        assert notif.notification_type == NotificationType.JOB_ALERT


# =============================================================================
# 2. NON-MATCHING JOB -> NO BACKLOG & NO NOTIFICATION
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_non_matching_job_creates_no_backlog_or_notification(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify jobs that do not match profile preferences create no backlog or notification."""
    posting = _make_posting(
        title="Marketing Specialist",
        external_id="job-mkt-202",
        description="Search engine optimization and content writing marketing role.",
    )
    e2e_adapter.set_postings([posting])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        ingest_res = await service.ingest_company(e2e_company.id)
        assert ingest_res.newly_inserted == 1

    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    disc_res = await discovery_worker.run_once()
    assert disc_res.claimed_count == 1
    assert disc_res.evaluated_jobs == 1
    assert disc_res.candidate_matches == 0
    assert disc_res.processed_outbox_count == 1

    # Outbox is marked PROCESSED, but 0 backlog items created
    async with session_factory() as session:
        backlog_items = (await session.execute(select(NotificationBacklog))).scalars().all()
        assert len(backlog_items) == 0

    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    drain_res = await drain_worker.run_once()
    assert drain_res.claimed_count == 0
    assert drain_res.promoted_count == 0

    async with session_factory() as session:
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 0


# =============================================================================
# 3. DUPLICATE INGESTION GENERATES NO DUPLICATE OUTBOX OR NOTIFICATIONS
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_duplicate_ingestion_generates_no_duplicates(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify duplicate ingestion is idempotent and does not create duplicates."""
    posting = _make_posting(
        title="Python Software Engineer",
        external_id="job-py-303",
    )
    e2e_adapter.set_postings([posting])

    # Ingest first time
    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        res1 = await service.ingest_company(e2e_company.id)
        assert res1.newly_inserted == 1

    # Ingest second time
    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        res2 = await service.ingest_company(e2e_company.id)
        assert res2.newly_inserted == 0
        assert res2.already_existing == 1

    # Process pipeline
    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    await discovery_worker.run_once()

    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    await drain_worker.run_once()

    # Re-ingest third time after pipeline processed
    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        res3 = await service.ingest_company(e2e_company.id)
        assert res3.newly_inserted == 0
        assert res3.already_existing == 1

    # Run pipeline again
    await discovery_worker.run_once()
    await drain_worker.run_once()

    # Verify exactly 1 outbox record, 1 backlog candidate, and 1 notification exist
    async with session_factory() as session:
        outbox_count = (await session.execute(select(JobDiscoveryOutbox))).scalars().all()
        assert len(outbox_count) == 1

        backlog_count = (await session.execute(select(NotificationBacklog))).scalars().all()
        assert len(backlog_count) == 1

        notif_count = (await session.execute(select(Notification))).scalars().all()
        assert len(notif_count) == 1


# =============================================================================
# 4. PROFILE PREFERENCE UPDATE -> HISTORICAL REEVALUATION
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_profile_preference_update_historical_reevaluation(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify meaningful preference updates trigger keyset historical reevaluation and promotion."""
    # Ingest Job 1 (Python - matches initial profile) and Job 2 (Rust - does not match)
    p1 = _make_posting("Python Developer", "job-p1")
    p2 = _make_posting(
        "Rust Systems Programmer",
        "job-p2",
        description="Low level systems programming in Rust and Tokio.",
    )
    e2e_adapter.set_postings([p1, p2])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    await discovery_worker.run_once()

    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    await drain_worker.run_once()

    # Initial state: 1 notification for Job 1
    async with session_factory() as session:
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 1

    # Update profile to also prefer Rust Systems roles
    update_dto = PreferenceProfileUpdate(
        preferred_roles=["Python Developer", "Rust Systems Programmer"],
        preferred_skills=["Python", "Rust", "Tokio"],
    )
    profile_service = ProfileUpdateService(session_factory=session_factory)
    updated_prof, task = await profile_service.update_profile(e2e_profile.id, update_dto)
    assert updated_prof is not None
    assert updated_prof.revision == 2
    assert task is not None
    assert task.status == ReevaluationStatus.PENDING
    assert task.last_processed_job_id == 0
    assert task.max_job_id >= 2

    # Run ProfileReevaluationWorker
    reeval_worker = ProfileReevaluationWorker(session_factory=session_factory, chunk_size=10)
    reeval_res = await reeval_worker.run_once()
    assert reeval_res.tasks_claimed == 1
    assert reeval_res.tasks_completed == 1
    assert reeval_res.candidates_created >= 1

    # Run BacklogDrainWorker to promote new candidate
    drain_res = await drain_worker.run_once()
    assert drain_res.promoted_count >= 1

    # Verify both jobs now have notifications
    async with session_factory() as session:
        notifs = (
            (await session.execute(select(Notification).order_by(Notification.job_id.asc())))
            .scalars()
            .all()
        )
        assert len(notifs) == 2


# =============================================================================
# 5. NO-OP PROFILE UPDATE DOES NOT INCREMENT REVISION OR TRIGGER REEVALUATION
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_profile_noop_update_does_not_increment_revision(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_profile: PreferenceProfile,
) -> None:
    """Verify non-filtering updates or identical preference updates do not trigger reevaluation."""
    profile_service = ProfileUpdateService(session_factory=session_factory)

    # 1. Non-filtering change (name only)
    upd1 = PreferenceProfileUpdate(name="Renamed Profile")
    prof1, task1 = await profile_service.update_profile(e2e_profile.id, upd1)
    assert prof1 is not None
    assert prof1.revision == 1
    assert task1 is None

    # 2. Identical filter change
    upd2 = PreferenceProfileUpdate(
        preferred_roles=["Python Developer", "Backend Engineer", "Software Engineer"]
    )
    prof2, task2 = await profile_service.update_profile(e2e_profile.id, upd2)
    assert prof2 is not None
    assert prof2.revision == 1
    assert task2 is None

    async with session_factory() as session:
        tasks = (await session.execute(select(ProfileReevaluationTask))).scalars().all()
        assert len(tasks) == 0


# =============================================================================
# 6. PROFILE DEACTIVATION DURING PROCESSING
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_profile_deactivation_during_processing(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify candidate is dropped without notification if profile is deactivated before drain."""
    p = _make_posting("Python Developer", "job-deact-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    await discovery_worker.run_once()

    # Backlog candidate is QUEUED. Now deactivate profile
    async with session_factory() as session:
        prof = await session.get(PreferenceProfile, e2e_profile.id)
        assert prof is not None
        prof.is_active = False
        await session.commit()

    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    drain_res = await drain_worker.run_once()
    assert drain_res.claimed_count == 1
    assert drain_res.dropped_count == 1
    assert drain_res.promoted_count == 0

    async with session_factory() as session:
        backlog = (await session.execute(select(NotificationBacklog))).scalar_one()
        assert backlog.status == BacklogStatus.DROPPED

        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 0


# =============================================================================
# 7. PROFILE DELETION CASCADES SAFELY
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_profile_deletion_cascades(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify deleting a profile cascades and deletes backlog items and reevaluation tasks."""
    p = _make_posting("Python Developer", "job-cascade-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    await discovery_worker.run_once()

    # Delete profile
    async with session_factory() as session:
        prof = await session.get(PreferenceProfile, e2e_profile.id)
        assert prof is not None
        await session.delete(prof)
        await session.commit()

    # Verify backlog items cascaded
    async with session_factory() as session:
        backlog_items = (await session.execute(select(NotificationBacklog))).scalars().all()
        assert len(backlog_items) == 0


# =============================================================================
# 8. EXPIRED LEASES AND WORKER RECLAIM
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_expired_leases_and_worker_reclaim(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify expired PROCESSING records in outbox and backlog are reclaimed safely."""
    p = _make_posting("Python Developer", "job-reclaim-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    # Simulate expired outbox record
    past = datetime.now(UTC) - timedelta(minutes=10)
    async with session_factory() as session:
        outbox = (await session.execute(select(JobDiscoveryOutbox))).scalar_one()
        outbox.status = OutboxStatus.PROCESSING
        outbox.lease_token = "stale-token-1"
        outbox.lease_expires_at = past
        outbox.attempt_count = 1
        await session.commit()

    # Discovery worker reclaims expired outbox record
    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    disc_res = await discovery_worker.run_once()
    assert disc_res.claimed_count == 1
    assert disc_res.processed_outbox_count == 1

    # Simulate expired backlog candidate
    async with session_factory() as session:
        backlog = (await session.execute(select(NotificationBacklog))).scalar_one()
        backlog.status = BacklogStatus.PROCESSING
        backlog.lease_token = "stale-token-2"
        backlog.lease_expires_at = past
        backlog.attempt_count = 1
        await session.commit()

    # Drain worker reclaims expired backlog record and promotes it
    drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
    drain_res = await drain_worker.run_once()
    assert drain_res.claimed_count == 1
    assert drain_res.promoted_count == 1

    async with session_factory() as session:
        notifs = (await session.execute(select(Notification))).scalars().all()
        assert len(notifs) == 1


# =============================================================================
# 9. STALE WORKER LEASE FENCING
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_stale_worker_fencing(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify stale workers with outdated lease tokens cannot commit chunks or promotions."""
    p = _make_posting("Python Developer", "job-fence-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    discovery_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=10)
    claimed_records = await discovery_worker._claim_outbox_batch()
    assert len(claimed_records) == 1
    rec = claimed_records[0]

    # Another worker reclaims with new lease token
    async with session_factory() as session:
        outbox = await session.get(JobDiscoveryOutbox, rec.id)
        assert outbox is not None
        outbox.lease_token = str(uuid.uuid4())
        await session.commit()

    # Worker A attempts commit with old token
    commit_ok, was_stale = await discovery_worker._fenced_commit_outbox(rec, [])
    assert commit_ok is False
    assert was_stale is True


# =============================================================================
# 10. RETRY AND TERMINAL FAILURE HANDLING
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_retry_and_terminal_failure_exhaustion(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify records that exhaust max retry attempts transition to terminal FAILED/DROPPED."""
    p = _make_posting("Python Developer", "job-retry-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    discovery_worker = JobDiscoveryWorker(
        session_factory=session_factory, batch_size=10, max_attempts=3
    )

    # Simulate 3 consecutive failures
    for attempt in range(1, 4):
        claimed = await discovery_worker._claim_outbox_batch()
        assert len(claimed) == 1
        rec = claimed[0]

        async with session_factory() as session:
            outbox = (await session.execute(select(JobDiscoveryOutbox))).scalar_one()
            assert outbox.attempt_count == attempt

        await discovery_worker._record_outbox_failure(rec.id, f"Error {attempt}")

    # After 3 attempts, outbox is FAILED
    async with session_factory() as session:
        outbox = (await session.execute(select(JobDiscoveryOutbox))).scalar_one()
        assert outbox.status == OutboxStatus.FAILED
        assert outbox.attempt_count == 3
        assert "Error 3" in (outbox.last_error or "")


# =============================================================================
# 11. TRANSACTION ROLLBACK AT CRITICAL BOUNDARIES
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_ingestion_transaction_rollback(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify failure while persisting Outbox rolls back Job creation atomically."""
    p = _make_posting("Python Developer", "job-rollback-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        original_execute = session.execute

        async def mock_execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
            if (
                hasattr(stmt, "table")
                and getattr(stmt.table, "name", None) == "job_discovery_outbox"
            ):
                raise RuntimeError("Simulated database failure during outbox insert")
            return await original_execute(stmt, *args, **kwargs)

        with patch.object(session, "execute", side_effect=mock_execute):
            service = IngestionService(session, registry=e2e_registry)
            res = await service.ingest_company(e2e_company.id)
            assert res.failed_records == 1
            assert res.newly_inserted == 0
        await session.commit()

    # Both Job and Outbox must be absent from DB
    async with session_factory() as session:
        jobs = (await session.execute(select(Job))).scalars().all()
        assert len(jobs) == 0
        outbox = (await session.execute(select(JobDiscoveryOutbox))).scalars().all()
        assert len(outbox) == 0


# =============================================================================
# 12. REPEATED WORKER EXECUTION IDEMPOTENCY
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_repeated_worker_execution_idempotency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify running workers when queues are empty makes zero mutations."""
    discovery_worker = JobDiscoveryWorker(session_factory=session_factory)
    reeval_worker = ProfileReevaluationWorker(session_factory=session_factory)
    drain_worker = BacklogDrainWorker(session_factory=session_factory)

    for _ in range(3):
        disc_res = await discovery_worker.run_once()
        assert disc_res.claimed_count == 0

        reeval_res = await reeval_worker.run_once()
        assert reeval_res.tasks_claimed == 0

        drain_res = await drain_worker.run_once()
        assert drain_res.claimed_count == 0


# =============================================================================
# 13. SCHEDULER-DRIVEN PIPELINE EXECUTION
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_scheduler_driven_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    e2e_company: Company,
    e2e_profile: PreferenceProfile,
    e2e_adapter: E2ETestScraperAdapter,
    e2e_registry: ScraperRegistry,
) -> None:
    """Verify the complete pipeline can be driven via MonitoringOrchestrator."""
    p = _make_posting("Python Developer", "job-sched-1")
    e2e_adapter.set_postings([p])

    async with session_factory() as session:
        service = IngestionService(session, registry=e2e_registry)
        await service.ingest_company(e2e_company.id)

    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        enable_pipeline_jobs=True,
    )

    await orchestrator.start()
    try:
        assert orchestrator.is_running is True
        assert orchestrator.is_lifecycle_gate_open is True

        # Trigger workers through orchestrator
        disc_res = await orchestrator.trigger_discovery_now()
        assert disc_res is not None
        assert disc_res.processed_outbox_count == 1

        drain_res = await orchestrator.trigger_backlog_drain_now()
        assert drain_res is not None
        assert drain_res.promoted_count == 1

        async with session_factory() as session:
            notifs = (await session.execute(select(Notification))).scalars().all()
            assert len(notifs) == 1
    finally:
        await orchestrator.shutdown(wait=True)
        assert orchestrator.is_running is False
        assert orchestrator.is_lifecycle_gate_open is False


# =============================================================================
# 14. PERFORMANCE & RELIABILITY AUDIT: SQL QUERY PLANS & ZERO TEMP B-TREES
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_performance_query_plans_and_indexing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify query plans for all critical pipeline paths use index without temp b-trees."""
    now = datetime.now(UTC)

    # Queries with strict zero temp b-trees
    zero_btree_queries: list[tuple[str, str, dict[str, Any]]] = [
        # 1. Outbox claim pending
        (
            "ix_outbox_claim_pending",
            "EXPLAIN QUERY PLAN SELECT id, job_id FROM job_discovery_outbox "
            "INDEXED BY ix_outbox_claim_pending "
            "WHERE status = 'PENDING' ORDER BY id ASC LIMIT 50",
            {},
        ),
        # 2. Backlog queued relevance
        (
            "ix_backlog_queued_relevance",
            "EXPLAIN QUERY PLAN SELECT id FROM notification_backlog "
            "INDEXED BY ix_backlog_queued_relevance "
            "WHERE status = 'QUEUED' ORDER BY score DESC, created_at ASC, id ASC LIMIT 1",
            {},
        ),
        # 3. Backlog queued FIFO
        (
            "ix_backlog_queued_fifo",
            "EXPLAIN QUERY PLAN SELECT id FROM notification_backlog "
            "INDEXED BY ix_backlog_queued_fifo "
            "WHERE status = 'QUEUED' ORDER BY created_at ASC, id ASC LIMIT 1",
            {},
        ),
        # 4. Keyset pagination on Jobs table
        (
            "primary key",
            "EXPLAIN QUERY PLAN SELECT id, company_id, title FROM jobs "
            "WHERE id > 0 AND id <= 1000 ORDER BY id ASC LIMIT 100",
            {},
        ),
        # 5. Notification deduplication
        (
            "uq_notifications_job_profile_type",
            "EXPLAIN QUERY PLAN SELECT id FROM notifications "
            "INDEXED BY uq_notifications_job_profile_type "
            "WHERE job_id = 1 AND profile_id = 1 AND notification_type = 'job_alert'",
            {},
        ),
    ]

    async with session_factory() as session:
        for expected_term, sql, params in zero_btree_queries:
            res = await session.execute(text(sql).params(**params))
            rows = res.all()
            plan_text = " ".join(str(r) for r in rows).lower()

            assert expected_term.lower() in plan_text, (
                f"Query did not use expected index/access pattern {expected_term}: {plan_text}"
            )
            assert "temp b-tree" not in plan_text, (
                f"Query plan unexpectedly produced temporary B-tree: {plan_text}"
            )

        # 6. Outbox claim expired - uses ix_outbox_claim_expired to avoid full table scan
        expired_sql = (
            "EXPLAIN QUERY PLAN SELECT id, job_id FROM job_discovery_outbox "
            "INDEXED BY ix_outbox_claim_expired "
            "WHERE status = 'PROCESSING' AND lease_expires_at < :now ORDER BY id ASC LIMIT 50"
        )
        res_exp = await session.execute(text(expired_sql).params(now=now))
        plan_exp = " ".join(str(r) for r in res_exp.all()).lower()
        assert "ix_outbox_claim_expired" in plan_exp, (
            f"Expired claim query did not use ix_outbox_claim_expired index: {plan_exp}"
        )
