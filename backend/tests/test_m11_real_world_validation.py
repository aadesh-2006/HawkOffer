"""HawkOffer Milestone 11 (M11): Real-World End-to-End Release Validation Suite.

Comprehensive validation covering:
1. Clean-installation and full 12-step user workflow on real SQLite WAL persistence.
2. Preference profile creation with resume text, hard filters, and semantic toggle.
3. Model lifecycle inspection and on-demand fallback operation.
4. Deterministic job ingestion, deduplication, and transactional outbox.
5. Inviolable hard-filter precedence during discovery (ineligible jobs never queued).
6. Backlog drain promotion, notification deduplication, and unread badge lifecycle.
7. Semantic match explainability (shared skills, missing requirements, relevance tier).
8. Profile revision increment and historical reevaluation pipeline.
9. Content-hash based embedding cache reuse and automatic invalidation.
10. Cold restart persistence verification across fresh application instances.
11. Migration idempotency from fresh and existing SQLite databases.
12. Embedding vector normalization (unit L2 norm, no NaN/Inf, zero vector sanitization).
"""

import math
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import create_engine_instance, init_db
from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.outbox import (
    JobDiscoveryOutbox,
    NotificationBacklog,
    OutboxStatus,
)
from app.models.preference import PreferenceProfile
from app.models.semantic import JobEmbedding
from app.scheduler.orchestrator import MonitoringOrchestrator
from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.registry import ScraperRegistry
from app.services.backlog_drain_worker import BacklogDrainWorker
from app.services.discovery_worker import JobDiscoveryWorker
from app.services.reevaluation_worker import ProfileReevaluationWorker
from app.services.semantic.embedding_service import FallbackEmbeddingService


class M11MockScraperAdapter(BaseScraperAdapter):
    """Controlled deterministic mock scraper adapter for M11 validation."""

    name = "m11_mock_adapter"

    def __init__(self, postings: list[RawJobPosting] | None = None) -> None:
        super().__init__()
        self.postings = postings or []

    def set_postings(self, postings: list[RawJobPosting]) -> None:
        self.postings = postings

    def extract_identifier(self, url_or_identifier: str) -> str:
        return "m11-corp"

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        return list(self.postings)


@pytest.mark.asyncio
async def test_m11_end_to_end_user_workflow_and_persistence(tmp_path: Path) -> None:
    """Execute complete 12-step real-world end-to-end workflow on SQLite WAL."""
    db_file = tmp_path / "m11_real_world.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    # Step 1: Initialize database
    engine = create_engine_instance(db_url=db_url, echo=False)
    await init_db(target_engine=engine)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    mock_adapter = M11MockScraperAdapter()
    registry = ScraperRegistry()
    registry.register("m11_mock_adapter", lambda **kwargs: mock_adapter)

    # 5 Controlled Postings to thoroughly exercise filtering and semantic matching:
    # 1. Strong Match (SF, Backend Python, Class of 2026) -> ELIGIBLE
    # 2. Excluded Keyword ("Director of Backend Engineering", SF, Class of 2026) -> INELIGIBLE
    # 3. Location Mismatch ("Senior Python Engineer", London, Class of 2026) -> INELIGIBLE
    # 4. Cohort Mismatch ("Senior Python Engineer", SF, Class of 2022) -> INELIGIBLE
    # 5. Partial Match ("Fullstack Web Developer", SF, Class of 2026) -> ELIGIBLE
    initial_postings = [
        RawJobPosting(
            source_ats="m11_mock_adapter",
            external_id="job-strong-01",
            title="Senior Python Backend Engineer",
            application_url="https://example.com/jobs/01",
            location="San Francisco, CA",
            department="Engineering",
            description=(
                "We are seeking a Senior Python Engineer with 5+ years experience in FastAPI, "
                "Docker, Redis, PostgreSQL, and distributed systems."
            ),
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="m11_mock_adapter",
            external_id="job-dir-02",
            title="Director of Backend Engineering",
            application_url="https://example.com/jobs/02",
            location="San Francisco, CA",
            department="Engineering",
            description=(
                "Leading our engineering teams. Deep expertise in Python, FastAPI, Docker, "
                "PostgreSQL, and organizational architecture."
            ),
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="m11_mock_adapter",
            external_id="job-london-03",
            title="Senior Python Backend Engineer",
            application_url="https://example.com/jobs/03",
            location="London, UK",
            department="Engineering",
            description="High-scale Python and FastAPI microservices in central London.",
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="m11_mock_adapter",
            external_id="job-grad-04",
            title="Senior Python Backend Engineer",
            application_url="https://example.com/jobs/04",
            location="San Francisco, CA",
            department="Engineering",
            description="Python development. Must be a university graduate from Class of 2022.",
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
        RawJobPosting(
            source_ats="m11_mock_adapter",
            external_id="job-partial-05",
            title="Fullstack Web Developer",
            application_url="https://example.com/jobs/05",
            location="San Francisco, CA",
            department="Product",
            description="React, TypeScript, Next.js, with some auxiliary Python scripting tasks.",
            employment_type="Full-time",
            workplace_type="On-site",
            published_at=datetime.now(UTC),
        ),
    ]
    mock_adapter.set_postings(initial_postings)

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

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client,
    ):
        # ---------------------------------------------------------------------
        # 1. Health & System Readiness
        # ---------------------------------------------------------------------
        h_resp = await client.get("/api/v1/health")
        assert h_resp.status_code == 200
        h_data = h_resp.json()
        assert h_data["status"] == "healthy"
        assert h_data["database"] == "connected"
        assert h_data["scheduler"] == "running"

        # ---------------------------------------------------------------------
        # 2. Preference Profile with Resume, Hard Filters, & Semantic Toggle
        # ---------------------------------------------------------------------
        profile_payload = {
            "name": "Senior Python Specialist",
            "preferred_roles": ["Backend Engineer", "Python Developer"],
            "preferred_skills": ["Python", "FastAPI", "Docker", "PostgreSQL", "Redis"],
            "required_locations": ["San Francisco, CA"],
            "excluded_keywords": ["Director", "Staff", "VP", "Unpaid"],
            "graduation_year": 2026,
            "require_graduation_year_match": True,
            "minimum_match_score": 60.0,
            "resume_text": (
                "Senior Python Engineer with 5+ years experience in FastAPI, Docker, "
                "PostgreSQL, Redis, and high-throughput microservices."
            ),
            "semantic_enabled": True,
            "is_active": True,
        }
        prof_resp = await client.post("/api/v1/profiles", json=profile_payload)
        assert prof_resp.status_code == 201, prof_resp.text
        profile = prof_resp.json()
        profile_id = profile["id"]
        assert profile["name"] == "Senior Python Specialist"
        assert profile["semantic_enabled"] is True
        assert "FastAPI" in profile["resume_text"]
        assert profile["revision"] == 1

        # ---------------------------------------------------------------------
        # 3. Model Lifecycle & Controls Inspection
        # ---------------------------------------------------------------------
        model_status_resp = await client.get("/api/v1/system/semantic-model")
        assert model_status_resp.status_code == 200
        model_status = model_status_resp.json()
        assert model_status["dimension"] == 384
        assert model_status["model_name"] == "all-MiniLM-L6-v2"
        assert model_status["is_fallback"] is True  # Fallback active in test env

        # Test toggle enable/disable
        disable_resp = await client.post("/api/v1/system/semantic-model/toggle?enabled=false")
        assert disable_resp.status_code == 200
        assert disable_resp.json()["status"] == "DISABLED"

        enable_resp = await client.post("/api/v1/system/semantic-model/toggle?enabled=true")
        assert enable_resp.status_code == 200
        assert enable_resp.json()["status"] in ("READY", "NOT_DOWNLOADED")

        # ---------------------------------------------------------------------
        # 4. Add Monitored Company Target
        # ---------------------------------------------------------------------
        company_payload = {
            "name": "Nexus Dynamics",
            "career_url": "https://boards.greenhouse.io/nexusdynamics",
            "platform": "m11_mock_adapter",
            "is_active": True,
        }
        comp_resp = await client.post("/api/v1/companies", json=company_payload)
        assert comp_resp.status_code == 201
        company = comp_resp.json()
        company_id = company["id"]

        # ---------------------------------------------------------------------
        # 5. Trigger Scan & Verify Deduplication
        # ---------------------------------------------------------------------
        scan_resp = await client.post(f"/api/v1/companies/{company_id}/trigger")
        assert scan_resp.status_code == 200
        scan_data = scan_resp.json()
        assert scan_data["status"] == "completed"
        assert scan_data["stats"]["total_fetched"] == 5
        assert scan_data["stats"]["newly_inserted"] == 5

        # Verify idempotency on second scan
        scan_resp2 = await client.post(f"/api/v1/companies/{company_id}/trigger")
        assert scan_resp2.status_code == 200
        scan_data2 = scan_resp2.json()
        assert scan_data2["status"] == "completed"
        assert scan_data2["stats"]["total_fetched"] == 5
        assert scan_data2["stats"]["newly_inserted"] == 0  # Deduplicated!
        assert scan_data2["stats"]["already_existing"] == 5

        # ---------------------------------------------------------------------
        # 6. Discovery Worker & Inviolable Hard-Filter Precedence
        # ---------------------------------------------------------------------
        disc_worker = JobDiscoveryWorker(session_factory=session_factory, batch_size=20)
        disc_result = await disc_worker.run_once()
        assert disc_result.claimed_count == 5
        assert disc_result.processed_outbox_count == 5
        assert disc_result.candidate_matches == 1

        async with session_factory() as sess:
            # Check Outbox: all 5 marked PROCESSED
            outbox_res = await sess.execute(
                select(JobDiscoveryOutbox.status, func.count()).group_by(JobDiscoveryOutbox.status)
            )
            outbox_counts = dict(outbox_res.all())
            assert outbox_counts.get(OutboxStatus.PROCESSED) == 5

            # PROVE: Hard filter precedence:
            # Only Job 1 met all hard criteria and score threshold (>= 60.0)!
            # Job 2 (Director), Job 3 (London), Job 4 (2022 grad) must NOT be queued.
            backlog_jobs = await sess.execute(
                select(NotificationBacklog.job_id, NotificationBacklog.score)
            )
            backlog_entries = backlog_jobs.all()
            assert len(backlog_entries) == 1, f"Expected 1 candidate, got {len(backlog_entries)}"

            # Confirm job in backlog corresponds to Job 1
            job_rows = await sess.execute(select(Job).where(Job.id == backlog_entries[0][0]))
            queued_job = job_rows.scalar_one()
            assert queued_job.external_id == "job-strong-01"

        # ---------------------------------------------------------------------
        # 7. Backlog Drain & Notification Deduplication
        # ---------------------------------------------------------------------
        drain_worker = BacklogDrainWorker(session_factory=session_factory, batch_size=10)
        drain_result = await drain_worker.run_once()
        assert drain_result.claimed_count == 1
        assert drain_result.promoted_count == 1

        # Verify notifications feed via REST API
        notifs_resp = await client.get("/api/v1/notifications")
        assert notifs_resp.status_code == 200
        notifs = notifs_resp.json()["items"]
        assert len(notifs) == 1

        unread_resp = await client.get("/api/v1/notifications/unread-count")
        assert unread_resp.status_code == 200
        assert unread_resp.json()["unread_count"] == 1

        # Second drain run produces 0 duplicate notifications
        drain_result2 = await drain_worker.run_once()
        assert drain_result2.claimed_count == 0
        assert drain_result2.promoted_count == 0

        # ---------------------------------------------------------------------
        # 8. Semantic Match Explainability & Cached Embeddings
        # ---------------------------------------------------------------------
        jobs_resp = await client.get("/api/v1/jobs")
        assert jobs_resp.status_code == 200
        all_jobs = jobs_resp.json()["items"]
        job_map = {j["title"]: j for j in all_jobs}

        job_strong = job_map["Senior Python Backend Engineer"]
        match_resp = await client.get(
            f"/api/v1/jobs/{job_strong['id']}/match?profile_id={profile_id}"
        )
        assert match_resp.status_code == 200
        match_data = match_resp.json()

        # Job 1 Explainability
        assert match_data["relevance_tier"] in ("EXCELLENT", "HIGH", "MEDIUM")
        assert match_data["blended_score"] > 60.0
        assert match_data["semantic_score"] is not None
        assert "Python" in match_data["shared_skills"]
        assert "FastAPI" in match_data["shared_skills"]
        assert len(match_data["reasons"]) > 0

        # Verify embedding cache persistence in database
        async with session_factory() as sess:
            emb_record = await sess.execute(
                select(JobEmbedding).where(JobEmbedding.job_id == job_strong["id"])
            )
            cached_emb = emb_record.scalar_one_or_none()
            assert cached_emb is not None
            assert cached_emb.content_hash == job_strong["content_hash"]
            assert len(cached_emb.to_vector()) == 384

        # Check Ineligible Job Explainability
        job_dir = job_map["Director of Backend Engineering"]
        match_dir_resp = await client.get(
            f"/api/v1/jobs/{job_dir['id']}/match?profile_id={profile_id}"
        )
        assert match_dir_resp.status_code == 200
        match_dir_data = match_dir_resp.json()
        assert match_dir_data["relevance_tier"] == "INELIGIBLE"
        assert match_dir_data["blended_score"] == 0.0

        # ---------------------------------------------------------------------
        # 9. Notification Read/Unread State Mutation
        # ---------------------------------------------------------------------
        target_notif = notifs[0]
        read_resp = await client.post(f"/api/v1/notifications/{target_notif['id']}/read")
        assert read_resp.status_code == 200
        assert read_resp.json()["status"] == "READ"
        assert read_resp.json()["read_at"] is not None

        unread_after_read = await client.get("/api/v1/notifications/unread-count")
        assert unread_after_read.json()["unread_count"] == 0

        # ---------------------------------------------------------------------
        # 10. Profile Update & Historical Keyset Reevaluation Pipeline
        # ---------------------------------------------------------------------
        # User relaxes location filter to include "London, UK" and disables strict grad match
        update_payload = {
            "name": "Global Python Specialist",
            "preferred_roles": ["Backend Engineer", "Python Developer"],
            "preferred_skills": ["Python", "FastAPI"],
            "required_locations": ["San Francisco, CA", "London, UK"],
            "require_graduation_year_match": False,
            "minimum_match_score": 50.0,
            "resume_text": "Global Python engineer with FastAPI expertise.",
            "semantic_enabled": True,
        }
        update_resp = await client.put(f"/api/v1/profiles/{profile_id}", json=update_payload)
        assert update_resp.status_code == 200
        updated_profile = update_resp.json()
        assert updated_profile["revision"] == 2
        assert "London, UK" in updated_profile["required_locations"]

        # Run reevaluation worker
        reeval_worker = ProfileReevaluationWorker(
            session_factory=session_factory,
            chunk_size=10,
            heartbeat_interval_seconds=1,
        )
        reeval_result = await reeval_worker.run_once()
        assert reeval_result.tasks_claimed >= 1
        assert reeval_result.jobs_evaluated >= 1

        # London job should now be eligible under revision 2!
        async with session_factory() as sess:
            london_backlog = await sess.execute(
                select(NotificationBacklog)
                .join(Job)
                .where(
                    Job.external_id == "job-london-03",
                    NotificationBacklog.profile_revision == 2,
                )
            )
            assert london_backlog.scalar_one_or_none() is not None

        # ---------------------------------------------------------------------
        # 11. Content Update Cache Invalidation
        # ---------------------------------------------------------------------
        new_hash = "updated_hash_999999999999999999999999999999999999999999999999"
        async with session_factory() as sess:
            job_to_update = await sess.get(Job, job_strong["id"])
            assert job_to_update is not None
            job_to_update.description = "Updated with Kubernetes and Rust requirements."
            job_to_update.content_hash = new_hash
            await sess.commit()

        # Querying match triggers re-computation and cache update for the new hash
        rematch_resp = await client.get(
            f"/api/v1/jobs/{job_strong['id']}/match?profile_id={profile_id}"
        )
        assert rematch_resp.status_code == 200
        async with session_factory() as sess:
            updated_cache = await sess.execute(
                select(JobEmbedding).where(JobEmbedding.job_id == job_strong["id"])
            )
            new_cache = updated_cache.scalar_one()
            assert new_cache.content_hash == new_hash

    # =========================================================================
    # 12. Cold Restart Persistence Verification
    # =========================================================================
    # Dispose previous engine
    await engine.dispose()

    # Reconnect fresh engine against the EXACT same SQLite database file
    engine_restart = create_engine_instance(db_url=db_url, echo=False)
    session_factory_restart = async_sessionmaker(
        bind=engine_restart,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory_restart() as sess:
        # 1. Company survived
        comp_check = await sess.execute(select(Company))
        comps = comp_check.scalars().all()
        assert len(comps) == 1
        assert comps[0].name == "Nexus Dynamics"

        # 2. Preference Profile survived with revision 2 and resume text
        prof_check = await sess.execute(select(PreferenceProfile))
        profs = prof_check.scalars().all()
        assert len(profs) == 1
        assert profs[0].revision == 2
        assert profs[0].name == "Global Python Specialist"
        assert profs[0].semantic_enabled is True
        assert "Global Python" in (profs[0].resume_text or "")

        # 3. Discovered Jobs survived
        jobs_check = await sess.execute(select(Job))
        jobs = jobs_check.scalars().all()
        assert len(jobs) == 5

        # 4. Notifications survived with read state preserved
        notifs_check = await sess.execute(select(Notification))
        survived_notifs = notifs_check.scalars().all()
        assert len(survived_notifs) == 1
        assert survived_notifs[0].status == "READ"
        assert survived_notifs[0].read_at is not None

        # 5. Cached Embeddings survived
        emb_check = await sess.execute(select(JobEmbedding))
        embs = emb_check.scalars().all()
        assert len(embs) >= 1

    await engine_restart.dispose()


def test_m11_semantic_finite_normalization_and_whitespace() -> None:
    """Verify embedding service guarantees: finite floats, L2 norm, and blank text sanitization."""
    service = FallbackEmbeddingService()

    # 1. Finite numbers and unit normalization
    text = "Fullstack Software Engineer specializing in Python, Rust, and modern React."
    vec = service.embed_text(text)
    assert len(vec) == 384
    for val in vec:
        assert isinstance(val, float)
        assert not math.isnan(val)
        assert not math.isinf(val)

    # L2 norm must be approximately 1.0
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert abs(l2_norm - 1.0) < 1e-4

    # 2. Blank text sanitization
    v_empty = service.embed_text("")
    v_spaces = service.embed_text("    \t\n   ")
    assert v_empty == [0.0] * 384
    assert v_spaces == [0.0] * 384

    # 3. Similarity boundary
    assert service.similarity("", text) == 0.0
    assert service.similarity("   ", text) == 0.0
    assert service.similarity(text, text) > 0.99


@pytest.mark.asyncio
async def test_m11_database_migration_fresh_and_existing(tmp_path: Path) -> None:
    """Verify Alembic migrations execute cleanly on fresh and existing databases."""
    fresh_db = tmp_path / "fresh_migration.db"
    fresh_url = f"sqlite+aiosqlite:///{fresh_db.as_posix()}"

    # 1. Fresh database migration to head
    engine_fresh = create_engine_instance(db_url=fresh_url)
    await init_db(target_engine=engine_fresh)

    session_factory = async_sessionmaker(bind=engine_fresh, class_=AsyncSession)
    async with session_factory() as sess:
        # Verify critical tables exist and are queryable
        comp_count = await sess.execute(select(func.count()).select_from(Company))
        assert comp_count.scalar() == 0

        job_count = await sess.execute(select(func.count()).select_from(Job))
        assert job_count.scalar() == 0

        emb_count = await sess.execute(select(func.count()).select_from(JobEmbedding))
        assert emb_count.scalar() == 0

        # Insert user entity into fresh database
        comp = Company(
            name="Migration Corp",
            career_url="https://boards.greenhouse.io/migrationcorp",
            platform="greenhouse",
            is_active=True,
        )
        sess.add(comp)
        await sess.commit()

    await engine_fresh.dispose()

    # 2. Existing database: re-run init_db (simulating app upgrade)
    engine_existing = create_engine_instance(db_url=fresh_url)
    await init_db(target_engine=engine_existing)

    session_factory_existing = async_sessionmaker(bind=engine_existing, class_=AsyncSession)
    async with session_factory_existing() as sess:
        # Existing data MUST be preserved
        comp_res = await sess.execute(select(Company))
        comps = comp_res.scalars().all()
        assert len(comps) == 1
        assert comps[0].name == "Migration Corp"

    await engine_existing.dispose()
