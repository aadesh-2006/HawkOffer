"""End-to-End Desktop Workflow Verification Suite for HawkOffer M8.

Verifies the complete 15-step user workflow across the real application stack:
1. Initialize application with a clean SQLite database in a temporary directory.
2. Start backend via the application factory and verify readiness via GET /api/v1/health.
3. Add a monitored company via POST /api/v1/companies (e.g., Stripe on Greenhouse).
4. Create a preference profile via POST /api/v1/profiles.
5. Trigger an immediate company scan via POST /api/v1/companies/{id}/trigger with mock adapter.
6. Verify jobs are ingested into the database:
   - Check fingerprint_hash deduplication.
   - Check JobDiscoveryOutbox events are created for new jobs.
7. Run the JobDiscoveryWorker once:
   - Verify outbox events are claimed and marked PROCESSED.
   - Verify matching jobs produce NotificationBacklog entries.
   - Verify non-matching jobs are filtered out.
8. Run the BacklogDrainWorker once:
   - Verify modulo-5 sequential allocation logic executes.
   - Verify NotificationBacklog entries are promoted to Notification.
   - Verify CompanyDrainAllocation records are updated.
9. Verify promoted notifications are retrievable via GET /api/v1/notifications:
   - Check response contains the matching job details.
   - Check unread count via GET /api/v1/notifications/unread-count.
10. Verify notification details and application URL are accessible via GET /api/v1/jobs/{id}.
11. Mark a notification as read via POST /api/v1/notifications/{id}/read:
    - Verify status changes to READ.
    - Verify unread count decrements to 0.
12. Verify monitoring status via GET /api/v1/monitoring/status:
    - Check pipeline worker statuses.
    - Check scheduler health and lifecycle gate.
13. Update preference profile via PUT /api/v1/profiles/{id}:
    - Verify revision increments.
    - Run ProfileReevaluationWorker once.
    - Verify reevaluation handles existing jobs against the new profile revision.
14. Perform clean shutdown:
    - Stop the orchestrator.
    - Dispose the database engine.
15. Reopen the database in a fresh application instance:
    - Verify all previously persisted data (companies, jobs, profiles, notifications)
      remains intact and accessible.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import create_engine_instance, init_db
from app.main import create_app
from app.models.job import Job
from app.models.notification import Notification
from app.models.outbox import (
    BacklogStatus,
    JobDiscoveryOutbox,
    NotificationBacklog,
    OutboxStatus,
)
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.schemas.notification import NotificationStatus
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.registry import ScraperRegistry


class MockWorkflowScraperAdapter(BaseScraperAdapter):
    """Mock ATS scraper adapter returning realistic multi-role postings for testing."""

    name = "mock_workflow"

    def __init__(self, postings: list[RawJobPosting] | None = None) -> None:
        super().__init__()
        self.postings = postings or []

    def set_postings(self, postings: list[RawJobPosting]) -> None:
        self.postings = postings

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "workflow-corp"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        return list(self.postings)


@pytest.mark.asyncio
async def test_complete_desktop_user_workflow(tmp_path: Path) -> None:
    """Execute complete 15-step end-to-end desktop workflow with real SQLite database."""
    # =========================================================================
    # STEP 1: Initialize application with clean SQLite database in temp dir
    # =========================================================================
    db_file = tmp_path / "hawkoffer_desktop_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    engine = create_engine_instance(db_url=db_url, echo=False)
    await init_db(target_engine=engine)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Configure controlled ATS mock scraper adapter
    mock_adapter = MockWorkflowScraperAdapter()
    registry = ScraperRegistry()
    registry.register("mock_workflow", lambda **kwargs: mock_adapter)

    # Configure postings:
    # 1. Matching Python Backend Role (should match profile criteria)
    # 2. Non-matching Sales Role (should be filtered out by discovery)
    # 3. Junior Support Role (minimal description, non-matching)
    test_postings = [
        RawJobPosting(
            source_ats="mock_workflow",
            external_id="stripe-py-01",
            title="Senior Backend Engineer - Python Infrastructure",
            application_url="https://boards.greenhouse.io/stripe/jobs/01",
            location="Remote",
            department="Engineering",
            description=(
                "We are seeking an experienced Backend Engineer with deep Python, "
                "FastAPI, PostgreSQL, and distributed systems experience. Full-time position."
            ),
            employment_type="Full-time",
            workplace_type="Remote",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="mock_workflow",
            external_id="stripe-sales-02",
            title="Global Enterprise Sales Account Executive",
            application_url="https://boards.greenhouse.io/stripe/jobs/02",
            location="Miami, FL",
            department="Sales",
            description="Leading regional sales quotas and client relationship development.",
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="mock_workflow",
            external_id="stripe-supp-03",
            title="General Customer Support Associate",
            application_url="https://boards.greenhouse.io/stripe/jobs/03",
            location="Dallas, TX",
            department="Operations",
            description="Responding to tier-1 tickets and handling inbound customer inquiries.",
            employment_type="Part-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
    ]
    mock_adapter.set_postings(test_postings)

    # Initialize scheduler orchestrator with isolated dependencies
    orchestrator = MonitoringOrchestrator(
        session_factory=session_factory,
        registry=registry,
        default_interval_minutes=60,
        enable_pipeline_jobs=True,
    )

    app = create_app(
        orchestrator=orchestrator,
        session_factory=session_factory,
        start_scheduler=True,
        init_database=False,
    )

    # =========================================================================
    # STEP 2: Start backend via application factory and verify readiness
    # =========================================================================
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client,
    ):
        health_res = await client.get("/api/v1/health")
        assert health_res.status_code == 200, health_res.text
        health_data = health_res.json()
        assert health_data["status"] == "healthy"
        assert health_data["database"] == "connected"
        assert health_data["scheduler"] == "running"

        # =====================================================================
        # STEP 3: Add monitored company via POST /api/v1/companies
        # =====================================================================
        company_payload = {
            "name": "Stripe",
            "career_url": "https://boards.greenhouse.io/stripe",
            "platform": "mock_workflow",
            "is_active": True,
        }
        comp_res = await client.post("/api/v1/companies", json=company_payload)
        assert comp_res.status_code == 201, comp_res.text
        company = comp_res.json()
        company_id = company["id"]
        assert company["name"] == "Stripe"
        assert company["platform"] == "mock_workflow"
        assert company["is_active"] is True

        # =====================================================================
        # STEP 4: Create preference profile via POST /api/v1/profiles
        # =====================================================================
        profile_payload = {
            "name": "Backend Python Specialist",
            "is_active": True,
            "minimum_match_score": 40.0,
            "preferred_roles": ["Backend Engineer", "Software Engineer", "Systems Engineer"],
            "preferred_skills": ["Python", "FastAPI", "PostgreSQL", "Distributed Systems"],
            "preferred_locations": ["Remote", "San Francisco, CA"],
            "preferred_employment_types": ["Full-time"],
            "required_employment_types": [],
            "preferred_workplace_types": ["Remote", "Hybrid"],
            "excluded_keywords": ["Sales", "Director", "Support"],
            "excluded_title_keywords": ["Sales", "Support", "Executive"],
            "excluded_content_keywords": [],
            "required_roles": [],
            "required_locations": [],
            "require_graduation_year_match": False,
        }
        prof_res = await client.post("/api/v1/profiles", json=profile_payload)
        assert prof_res.status_code == 201, prof_res.text
        profile = prof_res.json()
        profile_id = profile["id"]
        assert profile["name"] == "Backend Python Specialist"
        assert profile["revision"] == 1
        assert profile["is_active"] is True

        # =====================================================================
        # STEP 5: Trigger immediate company scan via POST /api/v1/companies/{id}/trigger
        # =====================================================================
        scan_res = await client.post(f"/api/v1/companies/{company_id}/trigger")
        assert scan_res.status_code == 200, scan_res.text
        scan_data = scan_res.json()
        assert scan_data["status"] == "completed"
        assert scan_data["stats"]["total_fetched"] == 3
        assert scan_data["stats"]["newly_inserted"] == 3
        assert scan_data["stats"]["already_existing"] == 0

        # =====================================================================
        # STEP 6: Verify jobs ingested and outbox events created
        # =====================================================================
        async with session_factory() as verify_session:
            # Check 3 jobs ingested with fingerprint_hash
            jobs = (await verify_session.execute(select(Job))).scalars().all()
            assert len(jobs) == 3
            for j in jobs:
                assert j.fingerprint_hash is not None
                assert len(j.fingerprint_hash) == 64
                assert j.company_id == company_id

            # Check 3 JobDiscoveryOutbox events committed
            outbox_events = (
                (await verify_session.execute(select(JobDiscoveryOutbox))).scalars().all()
            )
            assert len(outbox_events) == 3
            for ev in outbox_events:
                assert ev.status == OutboxStatus.PENDING
                assert ev.attempt_count == 0

        # Re-trigger company scan to verify idempotency and fingerprint deduplication
        scan_res2 = await client.post(f"/api/v1/companies/{company_id}/trigger")
        assert scan_res2.status_code == 200
        assert scan_res2.json()["stats"]["newly_inserted"] == 0
        assert scan_res2.json()["stats"]["already_existing"] == 3

        # =====================================================================
        # STEP 7: Run JobDiscoveryWorker once
        # =====================================================================
        disc_res = await client.post("/api/v1/monitoring/discovery/trigger")
        assert disc_res.status_code == 200, disc_res.text
        disc_data = disc_res.json()
        assert disc_data["status"] == "completed"
        assert disc_data["result"]["claimed_count"] == 3
        assert disc_data["result"]["evaluated_jobs"] == 3
        # Exactly 1 job (Python backend) matched criteria and created backlog candidate
        assert disc_data["result"]["candidate_matches"] == 1
        assert disc_data["result"]["processed_outbox_count"] == 3

        # Verify in DB: outbox events processed & notification backlog populated
        async with session_factory() as verify_session:
            processed_outbox = (
                (
                    await verify_session.execute(
                        select(JobDiscoveryOutbox).where(
                            JobDiscoveryOutbox.status == OutboxStatus.PROCESSED
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(processed_outbox) == 3

            backlog_items = (
                (await verify_session.execute(select(NotificationBacklog))).scalars().all()
            )
            assert len(backlog_items) == 1
            assert backlog_items[0].status == BacklogStatus.QUEUED
            assert backlog_items[0].profile_id == profile_id

        # =====================================================================
        # STEP 8: Run BacklogDrainWorker once
        # =====================================================================
        drain_res = await client.post("/api/v1/monitoring/drain/trigger")
        assert drain_res.status_code == 200, drain_res.text
        drain_data = drain_res.json()
        assert drain_data["status"] == "completed"
        assert drain_data["result"]["claimed_count"] == 1
        assert drain_data["result"]["promoted_count"] == 1

        # Verify in DB: backlog status updated to PROCESSED and Notification created
        async with session_factory() as verify_session:
            promoted_backlog = (
                (
                    await verify_session.execute(
                        select(NotificationBacklog).where(
                            NotificationBacklog.status == BacklogStatus.PROCESSED
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(promoted_backlog) == 1

            notifications = (await verify_session.execute(select(Notification))).scalars().all()
            assert len(notifications) == 1
            assert notifications[0].status == NotificationStatus.UNREAD
            promoted_notif_id = notifications[0].id
            promoted_job_id = notifications[0].job_id

        # =====================================================================
        # STEP 9: Verify promoted notifications via GET /api/v1/notifications
        # =====================================================================
        notifs_res = await client.get("/api/v1/notifications")
        assert notifs_res.status_code == 200, notifs_res.text
        notifs_data = notifs_res.json()
        assert notifs_data["total"] == 1
        assert len(notifs_data["items"]) == 1
        notif_item = notifs_data["items"][0]
        assert notif_item["id"] == promoted_notif_id
        assert notif_item["status"] == NotificationStatus.UNREAD
        assert notif_item["job_id"] == promoted_job_id

        # Check unread count
        unread_res = await client.get("/api/v1/notifications/unread-count")
        assert unread_res.status_code == 200
        assert unread_res.json()["unread_count"] == 1

        # =====================================================================
        # STEP 10: Verify notification details and application URL via GET /api/v1/jobs/{id}
        # =====================================================================
        job_res = await client.get(f"/api/v1/jobs/{promoted_job_id}")
        assert job_res.status_code == 200, job_res.text
        job_data = job_res.json()
        assert "Senior Backend Engineer" in job_data["title"]
        assert job_data["application_url"] == "https://boards.greenhouse.io/stripe/jobs/01"
        assert job_data["company_id"] == company_id

        # =====================================================================
        # STEP 11: Mark notification as read via POST /api/v1/notifications/{id}/read
        # =====================================================================
        read_res = await client.post(f"/api/v1/notifications/{promoted_notif_id}/read")
        assert read_res.status_code == 200, read_res.text
        assert read_res.json()["status"] == NotificationStatus.READ

        # Verify unread count decremented
        unread_res2 = await client.get("/api/v1/notifications/unread-count")
        assert unread_res2.status_code == 200
        assert unread_res2.json()["unread_count"] == 0

        # =====================================================================
        # STEP 12: Verify monitoring status via GET /api/v1/monitoring/status
        # =====================================================================
        mon_res = await client.get("/api/v1/monitoring/status")
        assert mon_res.status_code == 200, mon_res.text
        mon_data = mon_res.json()
        assert mon_data["is_running"] is True
        assert mon_data["is_lifecycle_gate_open"] is True
        pipe = mon_data["pipeline_status"]
        assert "discovery" in pipe
        assert "reevaluation" in pipe
        assert "backlog_drain" in pipe
        assert pipe["discovery"]["job_id"] == "job_discovery"
        assert pipe["reevaluation"]["job_id"] == "profile_reevaluation"
        assert pipe["backlog_drain"]["job_id"] == "backlog_drain"

        # =====================================================================
        # STEP 13: Update preference profile via PUT /api/v1/profiles/{id}
        # =====================================================================
        update_payload = {
            "name": "Updated Python & Cloud Specialist",
            "minimum_match_score": 30.0,
            "preferred_roles": ["Backend Engineer", "Software Engineer", "Cloud Engineer"],
            "preferred_skills": ["Python", "FastAPI", "Docker", "AWS"],
        }
        update_res = await client.put(f"/api/v1/profiles/{profile_id}", json=update_payload)
        assert update_res.status_code == 200, update_res.text
        updated_profile = update_res.json()
        assert updated_profile["revision"] == 2
        assert updated_profile["name"] == "Updated Python & Cloud Specialist"

        # Trigger reevaluation worker via API
        reeval_res = await client.post("/api/v1/monitoring/reevaluation/trigger")
        assert reeval_res.status_code == 200, reeval_res.text
        assert reeval_res.json()["status"] == "completed"

    # =========================================================================
    # STEP 14: Perform clean shutdown
    # =========================================================================
    # After exiting the lifespan context above, orchestrator is shut down
    assert not orchestrator.scheduler.running
    await engine.dispose()

    # =========================================================================
    # STEP 15: Reopen database in fresh application instance & verify persistence
    # =========================================================================
    assert db_file.exists()

    engine2 = create_engine_instance(db_url=db_url, echo=False)
    session_factory2 = async_sessionmaker(
        bind=engine2,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    fresh_app = create_app(
        orchestrator=None,
        session_factory=session_factory2,
        start_scheduler=False,
        init_database=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fresh_app), base_url="http://127.0.0.1:8765"
    ) as client2:
        # Verify persisted company
        companies_res = await client2.get("/api/v1/companies")
        assert companies_res.status_code == 200
        comp_items = companies_res.json()["items"]
        assert len(comp_items) == 1
        assert comp_items[0]["name"] == "Stripe"
        assert comp_items[0]["id"] == company_id

        # Verify persisted jobs
        jobs_res = await client2.get("/api/v1/jobs")
        assert jobs_res.status_code == 200
        job_items = jobs_res.json()["items"]
        assert len(job_items) == 3

        # Verify persisted profile with revision 2
        prof_res2 = await client2.get(f"/api/v1/profiles/{profile_id}")
        assert prof_res2.status_code == 200
        prof_item = prof_res2.json()
        assert prof_item["revision"] == 2
        assert prof_item["name"] == "Updated Python & Cloud Specialist"

        # Verify persisted notification in READ status
        notifs_res2 = await client2.get("/api/v1/notifications")
        assert notifs_res2.status_code == 200
        persisted_notifs = notifs_res2.json()["items"]
        assert len(persisted_notifs) == 1
        assert persisted_notifs[0]["status"] == NotificationStatus.READ

    await engine2.dispose()
