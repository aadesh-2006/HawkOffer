"""Comprehensive test suite for HawkOffer REST API v1 endpoints."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import create_app
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.scan_state import CompanyScanState
from app.schemas.notification import NotificationStatus
from app.services.backlog_drain_worker import DrainRunResult
from app.services.discovery_worker import DiscoveryRunResult
from app.services.reevaluation_worker import ReevaluationRunResult

ApiClientFixture = tuple[httpx.AsyncClient, MagicMock]


@pytest.fixture
async def api_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[ApiClientFixture, None]:
    """Provide an HTTPX AsyncClient configured with an isolated test application."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.scheduler.running = True
    mock_orchestrator.is_lifecycle_gate_open = True
    mock_orchestrator._is_shutting_down = False
    mock_orchestrator._is_starting = False
    mock_orchestrator.sync_company_schedules = AsyncMock()
    mock_orchestrator.pause_company = MagicMock(return_value=True)
    mock_orchestrator.resume_company = MagicMock(return_value=True)
    mock_orchestrator.pause_pipeline_job = MagicMock(return_value=True)
    mock_orchestrator.resume_pipeline_job = MagicMock(return_value=True)
    mock_orchestrator.get_pipeline_status = MagicMock(
        return_value={
            "discovery": {"job_id": "job_discovery", "scheduled": True, "paused": False},
            "reevaluation": {"job_id": "profile_reevaluation", "scheduled": True, "paused": False},
            "backlog_drain": {"job_id": "backlog_drain", "scheduled": True, "paused": False},
        }
    )

    app = create_app(
        orchestrator=mock_orchestrator,
        session_factory=session_factory,
        start_scheduler=False,
        init_database=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, mock_orchestrator


# ============================================================================
# SYSTEM ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_health_check(api_client: ApiClientFixture) -> None:
    client, _ = api_client
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["database"] == "connected"
    assert data["scheduler"] == "running"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_health_check_db_failure(api_client: ApiClientFixture) -> None:
    """Verify health endpoint reports degraded and disconnected when DB raises error."""
    client, _ = api_client
    with patch(
        "sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=RuntimeError("DB disconnected")
    ):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["database"] == "disconnected"
        assert data["status"] == "degraded"


@pytest.mark.asyncio
async def test_system_status(api_client: ApiClientFixture, db_session: AsyncSession) -> None:
    client, _ = api_client
    # Seed a company and job
    company = Company(
        name="Acme",
        career_url="https://boards.greenhouse.io/acme",
        platform="greenhouse",
    )
    db_session.add(company)
    await db_session.commit()

    response = await client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["companies_count"] >= 1
    assert data["is_running"] is True
    assert "pipeline_status" in data


# ============================================================================
# COMPANIES ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_company_crud_lifecycle(api_client: ApiClientFixture) -> None:
    client, _ = api_client

    # 1. Create company
    create_payload = {
        "name": "Stripe",
        "career_url": "https://jobs.lever.co/stripe",
        "platform": "lever",
        "is_active": True,
    }
    res_create = await client.post("/api/v1/companies", json=create_payload)
    assert res_create.status_code == 201
    created = res_create.json()
    company_id = created["id"]
    assert created["name"] == "Stripe"
    assert created["career_url"] == "https://jobs.lever.co/stripe"

    # 2. Invalid career_url returns 422
    res_invalid = await client.post(
        "/api/v1/companies",
        json={"name": "Bad", "career_url": "not-a-url", "platform": "lever"},
    )
    assert res_invalid.status_code == 422

    # 3. Get company
    res_get = await client.get(f"/api/v1/companies/{company_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == company_id

    # 4. List companies with pagination
    res_list = await client.get("/api/v1/companies?limit=10&offset=0")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["total"] >= 1
    assert any(c["id"] == company_id for c in list_data["items"])

    # 5. Patch company
    res_patch = await client.patch(
        f"/api/v1/companies/{company_id}",
        json={"name": "Stripe Payments"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["name"] == "Stripe Payments"

    # 6. Deactivate and Activate
    res_deact = await client.post(f"/api/v1/companies/{company_id}/deactivate")
    assert res_deact.status_code == 200
    assert res_deact.json()["is_active"] is False

    res_act = await client.post(f"/api/v1/companies/{company_id}/activate")
    assert res_act.status_code == 200
    assert res_act.json()["is_active"] is True

    # 7. Delete company
    res_del = await client.delete(f"/api/v1/companies/{company_id}")
    assert res_del.status_code == 200
    assert "deleted" in res_del.json()["message"].lower()

    # 8. Confirm deleted returns 404
    res_get_deleted = await client.get(f"/api/v1/companies/{company_id}")
    assert res_get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_company_scan_state_and_trigger(
    api_client: ApiClientFixture, db_session: AsyncSession
) -> None:
    client, mock_orch = api_client

    company = Company(
        name="Datadog",
        career_url="https://boards.greenhouse.io/datadog",
        platform="greenhouse",
    )
    db_session.add(company)
    await db_session.flush()

    scan_state = CompanyScanState(
        company_id=company.id,
        status="IDLE",
        scan_interval_minutes=15,
        total_scans=4,
    )
    db_session.add(scan_state)
    await db_session.commit()

    # Get scan state
    res_state = await client.get(f"/api/v1/companies/{company.id}/scan-state")
    assert res_state.status_code == 200
    assert res_state.json()["status"] == "IDLE"
    assert res_state.json()["total_scans"] == 4

    # Get scan state when none exists initializes new state
    company2 = Company(
        name="NoStateCo",
        career_url="https://jobs.lever.co/nostate",
        platform="lever",
    )
    db_session.add(company2)
    await db_session.commit()
    res_state_init = await client.get(f"/api/v1/companies/{company2.id}/scan-state")
    assert res_state_init.status_code == 200
    assert res_state_init.json()["company_id"] == company2.id

    # List companies with active filter
    res_act_filter = await client.get("/api/v1/companies?is_active=true")
    assert res_act_filter.status_code == 200
    assert res_act_filter.json()["total"] >= 1

    # Trigger scan
    scan_result_mock = MagicMock()
    scan_result_mock.total_fetched = 10
    scan_result_mock.newly_inserted = 5
    scan_result_mock.already_existing = 5
    scan_result_mock.updated_records = 0
    scan_result_mock.failed_records = 0
    scan_result_mock.duration_ms = 120.5
    mock_orch.trigger_scan_now = AsyncMock(return_value=scan_result_mock)

    res_trigger = await client.post(f"/api/v1/companies/{company.id}/trigger")
    assert res_trigger.status_code == 200
    trigger_data = res_trigger.json()
    assert trigger_data["status"] == "completed"
    assert trigger_data["stats"]["newly_inserted"] == 5


# ============================================================================
# PREFERENCE PROFILES ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_preference_profile_crud_and_revision(api_client: ApiClientFixture) -> None:
    client, _ = api_client

    # 1. Create profile
    payload = {
        "name": "Backend Python",
        "preferred_roles": ["python", "backend"],
        "is_active": True,
    }
    res_create = await client.post("/api/v1/profiles", json=payload)
    assert res_create.status_code == 201
    profile = res_create.json()
    profile_id = profile["id"]
    assert profile["revision"] == 1
    assert profile["name"] == "Backend Python"

    # 2. Get profile
    res_get = await client.get(f"/api/v1/profiles/{profile_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == profile_id

    # 3. List profiles
    res_list = await client.get("/api/v1/profiles")
    assert res_list.status_code == 200
    assert len(res_list.json()) >= 1

    # 4. Patch profile using ProfileUpdateService (revision increments!)
    patch_payload = {
        "preferred_roles": ["python", "backend", "fastapi"],
    }
    res_patch = await client.patch(f"/api/v1/profiles/{profile_id}", json=patch_payload)
    assert res_patch.status_code == 200
    updated_profile = res_patch.json()
    assert updated_profile["revision"] == 2
    assert "fastapi" in updated_profile["preferred_roles"]

    # 5. Deactivate / Activate
    res_deact = await client.post(f"/api/v1/profiles/{profile_id}/deactivate")
    assert res_deact.status_code == 200
    assert res_deact.json()["is_active"] is False

    res_act = await client.post(f"/api/v1/profiles/{profile_id}/activate")
    assert res_act.status_code == 200
    assert res_act.json()["is_active"] is True

    # 6. Delete profile
    res_del = await client.delete(f"/api/v1/profiles/{profile_id}")
    assert res_del.status_code == 200
    assert "deleted" in res_del.json()["message"].lower()

    # 7. Confirm deleted returns 404
    res_get_deleted = await client.get(f"/api/v1/profiles/{profile_id}")
    assert res_get_deleted.status_code == 404


# ============================================================================
# JOBS ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_jobs_list_and_filtering(
    api_client: ApiClientFixture, db_session: AsyncSession
) -> None:
    client, _ = api_client

    company = Company(
        name="Netflix",
        career_url="https://boards.greenhouse.io/netflix",
        platform="greenhouse",
    )
    db_session.add(company)
    await db_session.flush()

    job1 = Job(
        company_id=company.id,
        title="Senior Python Engineer",
        location="Remote, US",
        department="Platform",
        application_url="https://netflix.com/job1",
        fingerprint_hash="a" * 64,
        content_hash="1" * 64,
        status="NEW",
        job_type="Full-time",
        graduation_year=2024,
    )
    job2 = Job(
        company_id=company.id,
        title="Frontend React Developer",
        location="Los Gatos, CA",
        department="UI",
        application_url="https://netflix.com/job2",
        fingerprint_hash="b" * 64,
        content_hash="2" * 64,
        status="PROCESSED",
        job_type="Internship",
        graduation_year=2025,
    )
    db_session.add_all([job1, job2])
    await db_session.commit()

    # 1. List all jobs
    res_all = await client.get("/api/v1/jobs")
    assert res_all.status_code == 200
    assert res_all.json()["total"] >= 2

    # 2. Filter by search keyword
    res_search = await client.get("/api/v1/jobs?search=Python")
    assert res_search.status_code == 200
    data = res_search.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Senior Python Engineer"

    # 3. Filter by company_id and status
    res_filtered = await client.get(f"/api/v1/jobs?company_id={company.id}&status=NEW")
    assert res_filtered.status_code == 200
    assert res_filtered.json()["total"] == 1

    # 4. Get by ID
    res_job = await client.get(f"/api/v1/jobs/{job1.id}")
    assert res_job.status_code == 200
    assert res_job.json()["id"] == job1.id

    # 5. Nonexistent job returns 404
    res_none = await client.get("/api/v1/jobs/999999")
    assert res_none.status_code == 404


# ============================================================================
# NOTIFICATIONS ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_notifications_lifecycle_and_batch_actions(
    api_client: ApiClientFixture, db_session: AsyncSession
) -> None:
    client, _ = api_client

    company = Company(
        name="Apple",
        career_url="https://jobs.lever.co/apple",
        platform="lever",
    )
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="iOS Engineer",
        application_url="https://apple.com/job",
        fingerprint_hash="c" * 64,
        content_hash="3" * 64,
    )
    db_session.add(job)
    await db_session.flush()

    n1 = Notification(
        job_id=job.id,
        notification_type="job_alert",
        status=NotificationStatus.UNREAD,
    )
    n2 = Notification(
        job_id=job.id,
        notification_type="job_alert",
        status=NotificationStatus.UNREAD,
    )
    db_session.add_all([n1, n2])
    await db_session.commit()

    # 1. Check unread count
    res_count = await client.get("/api/v1/notifications/unread-count")
    assert res_count.status_code == 200
    assert res_count.json()["unread_count"] >= 2

    # 2. Get notification details
    res_n = await client.get(f"/api/v1/notifications/{n1.id}")
    assert res_n.status_code == 200
    assert res_n.json()["status"] == "UNREAD"

    # 3. Mark read
    res_read = await client.post(f"/api/v1/notifications/{n1.id}/read")
    assert res_read.status_code == 200
    assert res_read.json()["status"] == "READ"
    assert res_read.json()["read_at"] is not None

    # 4. Mark unread
    res_unread = await client.post(f"/api/v1/notifications/{n1.id}/unread")
    assert res_unread.status_code == 200
    assert res_unread.json()["status"] == "UNREAD"

    # 5. Dismiss and Restore
    res_dismiss = await client.post(f"/api/v1/notifications/{n1.id}/dismiss")
    assert res_dismiss.status_code == 200
    assert res_dismiss.json()["status"] == "DISMISSED"

    res_restore = await client.post(f"/api/v1/notifications/{n1.id}/restore")
    assert res_restore.status_code == 200
    assert res_restore.json()["status"] == "UNREAD"

    # 6. Batch mark read
    res_batch_read = await client.post(
        "/api/v1/notifications/batch/read",
        json={"ids": [n1.id, n2.id]},
    )
    assert res_batch_read.status_code == 200
    assert res_batch_read.json()["affected_count"] == 2

    # 7. Batch dismiss
    res_batch_dismiss = await client.post(
        "/api/v1/notifications/batch/dismiss",
        json={"ids": [n1.id, n2.id]},
    )
    assert res_batch_dismiss.status_code == 200
    assert res_batch_dismiss.json()["affected_count"] == 2

    # 8. List notifications excluding dismissed
    res_list = await client.get("/api/v1/notifications?include_dismissed=false")
    assert res_list.status_code == 200
    assert not any(n["id"] in [n1.id, n2.id] for n in res_list.json()["items"])

    # 9. List and count notifications with profile_id filter
    res_prof_list = await client.get("/api/v1/notifications?profile_id=1")
    assert res_prof_list.status_code == 200

    res_prof_unread = await client.get("/api/v1/notifications/unread-count?profile_id=1")
    assert res_prof_unread.status_code == 200
    assert res_prof_unread.json()["profile_id"] == 1


# ============================================================================
# MONITORING ENDPOINTS
# ============================================================================


@pytest.mark.asyncio
async def test_monitoring_status_and_triggers(api_client: ApiClientFixture) -> None:
    client, mock_orch = api_client

    # 1. Monitoring status
    res_status = await client.get("/api/v1/monitoring/status")
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert status_data["is_running"] is True
    assert status_data["is_lifecycle_gate_open"] is True
    assert "discovery" in status_data["pipeline_status"]

    # 2. Trigger Discovery
    mock_orch.trigger_discovery_now = AsyncMock(
        return_value=DiscoveryRunResult(
            claimed_count=3,
            evaluated_jobs=3,
            candidate_matches=2,
            processed_outbox_count=3,
            failed_outbox_count=0,
            stale_lease_skips=0,
        )
    )
    res_disc = await client.post("/api/v1/monitoring/discovery/trigger")
    assert res_disc.status_code == 200
    assert res_disc.json()["status"] == "completed"
    assert res_disc.json()["result"]["claimed_count"] == 3

    # 3. Trigger Reevaluation
    mock_orch.trigger_reevaluation_now = AsyncMock(
        return_value=ReevaluationRunResult(
            tasks_claimed=1,
            chunks_processed=1,
            jobs_evaluated=10,
            candidates_created=4,
            tasks_completed=1,
            tasks_superseded=0,
            stale_lease_skips=0,
            tasks_failed=0,
        )
    )
    res_reeval = await client.post("/api/v1/monitoring/reevaluation/trigger")
    assert res_reeval.status_code == 200
    assert res_reeval.json()["status"] == "completed"
    assert res_reeval.json()["result"]["tasks_claimed"] == 1

    # 4. Trigger Backlog Drain
    mock_orch.trigger_backlog_drain_now = AsyncMock(
        return_value=DrainRunResult(
            claimed_count=5,
            promoted_count=5,
            already_promoted_count=0,
            stale_revision_count=0,
            dropped_count=0,
            stale_lease_skips=0,
            failed_count=0,
            final_drain_counter=5,
        )
    )
    res_drain = await client.post("/api/v1/monitoring/drain/trigger")
    assert res_drain.status_code == 200
    assert res_drain.json()["status"] == "completed"
    assert res_drain.json()["result"]["promoted_count"] == 5

    # 5. Pause and Resume pipeline jobs
    res_pause = await client.post("/api/v1/monitoring/pause?job_name=discovery")
    assert res_pause.status_code == 200
    assert "paused" in res_pause.json()["message"]

    res_resume = await client.post("/api/v1/monitoring/resume?job_name=discovery")
    assert res_resume.status_code == 200
    assert "resumed" in res_resume.json()["message"]

    # 6. Pause and Resume company scans
    res_pause_co = await client.post("/api/v1/monitoring/pause?company_id=42")
    assert res_pause_co.status_code == 200
    assert "paused" in res_pause_co.json()["message"]

    res_resume_co = await client.post("/api/v1/monitoring/resume?company_id=42")
    assert res_resume_co.status_code == 200
    assert "resumed" in res_resume_co.json()["message"]

    # 6b. Pause and Resume with JSON body payload
    res_pause_body = await client.post("/api/v1/monitoring/pause", json={"job_name": "discovery"})
    assert res_pause_body.status_code == 200

    res_resume_body = await client.post("/api/v1/monitoring/resume", json={"company_id": 42})
    assert res_resume_body.status_code == 200

    # 7. Pause unknown job -> 400
    res_unknown = await client.post("/api/v1/monitoring/pause?job_name=nonexistent")
    assert res_unknown.status_code == 400

    # 8. Pause with no arguments -> 400
    res_no_args = await client.post("/api/v1/monitoring/pause")
    assert res_no_args.status_code == 400


# ============================================================================
# ERROR HANDLING AND EDGE CASES
# ============================================================================


@pytest.mark.asyncio
async def test_not_found_and_validation_errors(api_client: ApiClientFixture) -> None:
    client, _ = api_client

    # Company 404s
    assert (await client.get("/api/v1/companies/9999")).status_code == 404
    assert (await client.patch("/api/v1/companies/9999", json={"name": "X"})).status_code == 404
    assert (await client.post("/api/v1/companies/9999/activate")).status_code == 404
    assert (await client.post("/api/v1/companies/9999/deactivate")).status_code == 404
    assert (await client.get("/api/v1/companies/9999/scan-state")).status_code == 404
    assert (await client.delete("/api/v1/companies/9999")).status_code == 404
    assert (await client.post("/api/v1/companies/9999/trigger")).status_code == 404

    # Profile 404s
    assert (await client.get("/api/v1/profiles/9999")).status_code == 404
    assert (await client.patch("/api/v1/profiles/9999", json={"name": "X"})).status_code == 404
    assert (await client.post("/api/v1/profiles/9999/activate")).status_code == 404
    assert (await client.post("/api/v1/profiles/9999/deactivate")).status_code == 404
    assert (await client.delete("/api/v1/profiles/9999")).status_code == 404

    # Notification 404s
    assert (await client.get("/api/v1/notifications/9999")).status_code == 404
    assert (await client.post("/api/v1/notifications/9999/read")).status_code == 404
    assert (await client.post("/api/v1/notifications/9999/unread")).status_code == 404
    assert (await client.post("/api/v1/notifications/9999/dismiss")).status_code == 404
    assert (await client.post("/api/v1/notifications/9999/restore")).status_code == 404

    # Validation errors (422)
    assert (await client.get("/api/v1/jobs?limit=0")).status_code == 422
    assert (await client.get("/api/v1/jobs?offset=-1")).status_code == 422
    assert (await client.get("/api/v1/companies?limit=1000")).status_code == 422


@pytest.mark.asyncio
async def test_worker_triggers_rejected_when_shutting_down(
    api_client: ApiClientFixture,
) -> None:
    """Verify triggers return 'rejected' WorkerTriggerResponse when orchestrator returns None."""
    client, mock_orch = api_client
    mock_orch.trigger_discovery_now = AsyncMock(return_value=None)
    mock_orch.trigger_reevaluation_now = AsyncMock(return_value=None)
    mock_orch.trigger_backlog_drain_now = AsyncMock(return_value=None)

    r1 = await client.post("/api/v1/monitoring/discovery/trigger")
    assert r1.status_code == 200
    assert r1.json()["status"] == "rejected"

    r2 = await client.post("/api/v1/monitoring/reevaluation/trigger")
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"

    r3 = await client.post("/api/v1/monitoring/drain/trigger")
    assert r3.status_code == 200
    assert r3.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_company_conflict_and_pause_not_found(
    api_client: ApiClientFixture, db_session: AsyncSession
) -> None:
    """Verify career_url duplicate conflict on create and update, and pause company 404."""
    client, mock_orch = api_client

    # Seed company
    co1 = Company(name="Co1", career_url="https://jobs.lever.co/co1", platform="lever")
    co2 = Company(name="Co2", career_url="https://jobs.lever.co/co2", platform="lever")
    db_session.add_all([co1, co2])
    await db_session.commit()

    # Create conflict
    r_create = await client.post(
        "/api/v1/companies",
        json={"name": "Duplicate", "career_url": "https://jobs.lever.co/co1", "platform": "lever"},
    )
    assert r_create.status_code == 409

    # Update conflict
    r_update = await client.patch(
        f"/api/v1/companies/{co2.id}",
        json={"career_url": "https://jobs.lever.co/co1"},
    )
    assert r_update.status_code == 409

    # Pause company not found
    mock_orch.pause_company = MagicMock(return_value=False)
    r_pause_404 = await client.post("/api/v1/monitoring/pause?company_id=99999")
    assert r_pause_404.status_code == 404

    # Resume company not found
    mock_orch.resume_company = MagicMock(return_value=False)
    r_resume_404 = await client.post("/api/v1/monitoring/resume?company_id=99999")
    assert r_resume_404.status_code == 404


@pytest.mark.asyncio
async def test_api_branches_and_resilience(
    api_client: ApiClientFixture, db_session: AsyncSession
) -> None:
    """Verify schedule sync failure resilience and error branches across endpoints."""
    client, mock_orch = api_client

    # 1. Schedule sync failure during company create does not fail the HTTP request
    mock_orch.sync_company_schedules = AsyncMock(side_effect=RuntimeError("Sync error"))
    r_create = await client.post(
        "/api/v1/companies",
        json={
            "name": "Resilient Co",
            "career_url": "https://jobs.lever.co/resilient",
            "platform": "lever",
            "is_active": True,
        },
    )
    assert r_create.status_code == 201
    co_id = r_create.json()["id"]

    # 2. Schedule sync failure during company update, activate, deactivate, delete
    r_update = await client.patch(f"/api/v1/companies/{co_id}", json={"name": "Resilient Co 2"})
    assert r_update.status_code == 200

    r_deact = await client.post(f"/api/v1/companies/{co_id}/deactivate")
    assert r_deact.status_code == 200

    r_act = await client.post(f"/api/v1/companies/{co_id}/activate")
    assert r_act.status_code == 200

    # 3. Trigger company scan conflict when trigger returns None
    mock_orch.is_lifecycle_gate_open = True
    mock_orch.trigger_scan_now = AsyncMock(return_value=None)
    r_trig_conf = await client.post(f"/api/v1/companies/{co_id}/trigger")
    assert r_trig_conf.status_code == 409

    # 4. Trigger company scan 503 when lifecycle gate is closed
    mock_orch.is_lifecycle_gate_open = False
    r_trig_closed = await client.post(f"/api/v1/companies/{co_id}/trigger")
    assert r_trig_closed.status_code == 503
    mock_orch.is_lifecycle_gate_open = True

    # 5. Delete company with sync failure
    r_del = await client.delete(f"/api/v1/companies/{co_id}")
    assert r_del.status_code == 200

    # Reset sync mock
    mock_orch.sync_company_schedules = AsyncMock(return_value=None)

    # 6. Job filters for employment_type, job_type, graduation_year
    r_jobs_emp = await client.get(
        "/api/v1/jobs?employment_type=Full-time&job_type=Full-time&graduation_year=2024"
    )
    assert r_jobs_emp.status_code == 200

    # 7. Notification list with specific status filters
    r_notif_read = await client.get("/api/v1/notifications?status=READ")
    assert r_notif_read.status_code == 200

    r_notif_unread = await client.get("/api/v1/notifications?status=UNREAD")
    assert r_notif_unread.status_code == 200

    r_notif_dismissed = await client.get("/api/v1/notifications?status=DISMISSED")
    assert r_notif_dismissed.status_code == 200


@pytest.mark.asyncio
async def test_monitoring_unavailable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify monitoring endpoints handle absent or stopped orchestrator with 503."""
    app = create_app(
        orchestrator=None,
        session_factory=session_factory,
        start_scheduler=False,
        init_database=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Status still works and reports not running
        res_status = await client.get("/api/v1/monitoring/status")
        assert res_status.status_code == 200
        assert res_status.json()["is_running"] is False

        # Triggers return 503
        assert (await client.post("/api/v1/monitoring/discovery/trigger")).status_code == 503
        assert (await client.post("/api/v1/monitoring/reevaluation/trigger")).status_code == 503
        assert (await client.post("/api/v1/monitoring/drain/trigger")).status_code == 503
        assert (await client.post("/api/v1/monitoring/pause?job_name=discovery")).status_code == 503
        assert (
            await client.post("/api/v1/monitoring/resume?job_name=discovery")
        ).status_code == 503


@pytest.mark.asyncio
async def test_semantic_model_system_endpoints(
    api_client: ApiClientFixture,
) -> None:
    client, _ = api_client

    # 1. GET status
    res = await client.get("/api/v1/system/semantic-model")
    assert res.status_code == 200
    data = res.json()
    assert data["model_name"] == "all-MiniLM-L6-v2"
    assert data["dimension"] == 384
    assert "status" in data

    # 2. POST toggle
    res_toggle = await client.post("/api/v1/system/semantic-model/toggle?enabled=false")
    assert res_toggle.status_code == 200
    assert res_toggle.json()["status"] == "DISABLED"

    # Re-enable
    res_toggle2 = await client.post("/api/v1/system/semantic-model/toggle?enabled=true")
    assert res_toggle2.status_code == 200


@pytest.mark.asyncio
async def test_job_match_endpoint(
    api_client: ApiClientFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.models.preference import PreferenceProfile

    client, _ = api_client

    # 1. Seed Company, Job, and Profile
    async with session_factory() as session:
        company = Company(
            name="Match Tech",
            career_url="https://boards.greenhouse.io/matchtech",
            platform="greenhouse",
            is_active=True,
        )
        session.add(company)
        await session.flush()

        job = Job(
            company_id=company.id,
            title="Backend Python Engineer",
            description="Build scalable distributed APIs with FastAPI, Docker, and SQL.",
            location="Remote",
            application_url="https://example.com/apply/backend",
            fingerprint_hash="m" * 64,
            content_hash="c" * 64,
        )
        profile = PreferenceProfile(
            name="Python Match",
            preferred_roles=["Backend Python Engineer"],
            preferred_skills=["FastAPI", "Docker", "SQL"],
            resume_text="Senior Python developer with FastAPI and Docker experience.",
            semantic_enabled=True,
            is_active=True,
        )
        session.add(job)
        session.add(profile)
        await session.commit()
        job_id = job.id

    # 2. Query match endpoint
    res = await client.get(f"/api/v1/jobs/{job_id}/match")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == job_id
    assert data["eligibility_status"] == "ELIGIBLE"
    assert data["deterministic_score"] > 50.0
    assert data["semantic_score"] is not None
    assert data["blended_score"] > 0.0
    assert "FastAPI" in data["shared_skills"] or "fastapi" in [
        s.lower() for s in data["shared_skills"]
    ]
