# Milestone 15: Centralized Multi-Tenant Monitoring & Outbox Fan-Out Verification Report

**Milestone:** M15: Centralized Multi-Tenant Monitoring & Outbox Fan-Out  
**Status:** COMPLETED & VERIFIED  
**Repository:** https://github.com/aadesh-2006/HawkOffer  
**Engine:** Python 3.13 / FastAPI / SQLAlchemy / SQLite WAL & PostgreSQL Dual Dialect  

---

## 1. Executive Summary & North Star Alignment

HawkOffer's North Star is a **public, multi-user, website-first job monitoring SaaS** operating on zero-budget infrastructure.

In multi-tenant SaaS operations:
- Hundreds of users may subscribe to popular companies (e.g. Google, Stripe, Airbnb).
- Scraping each company once per user is wasteful, triggers rate limits/IP bans, and does not scale.
- Background monitoring must execute headless server-side even when users are disconnected or browser tabs are closed.

Milestone 15 transforms HawkOffer's ingestion into an efficient **centralized 1-to-N monitoring and fan-out engine**:
1. **Single Scrape per Monitored Target**:
   Regardless of subscriber volume, each monitored target is fetched exactly once per monitoring cycle.
2. **Centralized Deduplication**:
   Discovered jobs are normalized, fingerprinted, and deduplicated in the central catalog.
3. **Subscription-Aware Outbox Fan-Out**:
   Instead of global broadcast, new jobs are evaluated exclusively against active preference profiles of active users subscribed to that specific company (plus legacy desktop profiles when running locally).
4. **Per-User Notification Deduplication**:
   Candidates are promoted into the outbox with compound unique indexing `(job_id, user_id, notification_type)`, preventing duplicate notifications across repeated cycles while safely notifying distinct subscribers.
5. **Headless Cron Trigger API**:
   External schedulers invoke `POST /api/v1/internal/cron/monitor` protected by constant-time secret comparison and in-flight concurrency locks (`409 Conflict`).

---

## 2. Architecture & Implementation Details

### 2.1 Monitored Target Resolution (`CompanyRepository`)
Added `list_active_monitored_targets(self)`:
- Queries distinct active companies having at least one active user subscription (`UserCompanySubscription` joined with `users.is_active = True`) OR marked as curated (`is_curated = True`).
- Desktop backward compatibility fallback: If 0 total subscriptions exist across the database, it gracefully falls back to `list_active()`, allowing standalone desktop operation without requiring explicit subscriptions.

### 2.2 Discovery Worker Subscription Fan-Out (`DiscoveryWorker`)
Updated `_evaluate_claimed_records` and `_filter_in_thread`:
- Queries applicable subscriber profiles per `job.company_id`:
  ```sql
  SELECT DISTINCT p FROM preference_profiles p
  JOIN users u ON u.id = p.user_id
  JOIN user_company_subscriptions s ON s.user_id = u.id
  WHERE s.company_id = :company_id AND p.is_active = TRUE AND u.is_active = TRUE
  ```
- Unifies with legacy desktop profiles (`p.user_id IS NULL AND p.is_active = TRUE`).
- Only matched subscriber profiles generate `OutboxCandidate` rows tagged with `user_id = profile.user_id`.
- Users not subscribed to the company or lacking an active profile are never evaluated for that company's jobs.

### 2.3 Backlog Drain Worker Multi-Tenant Promotion (`BacklogDrainWorker`)
Updated `_process_claimed_batch`:
- Deduplicates candidate promotion against existing notifications:
  - If `user_id` is set: checks `(job_id, user_id, notification_type)`.
  - If `user_id` is `None` (legacy desktop): checks `(job_id, profile_id, notification_type)`.
- Handles concurrent insertion race conditions via `IntegrityError` rollback.

### 2.4 Historical Reevaluation Subscription Scoping (`ReevaluationWorker`)
Updated `_run_reevaluation_job`:
- When reevaluating an authenticated user's profile (`profile.user_id` is present), queries are filtered to jobs belonging to companies the user is actively subscribed to:
  ```python
  sub_company_ids = select(UserCompanySubscription.company_id).where(
      UserCompanySubscription.user_id == profile.user_id
  )
  keyset_query = keyset_query.where(Job.company_id.in_(sub_company_ids))
  ```

### 2.5 Centralized Monitoring Orchestrator (`ScanOrchestrator`)
Added `run_centralized_monitoring_cycle()` and async lock:
- Prevents overlapping cycle executions across worker processes.
- Iterates active targets, scrapes each once using lock and backoff, resilient to individual company scraper failures (`partial_failure` status reported without aborting the batch).
- Dispatches discovery worker fan-out to generate outbox candidates.
- Dispatches backlog drain worker to promote candidates into `Notification` records.

### 2.6 Protected Cron Trigger Endpoint
Added `POST /api/v1/internal/cron/monitor`:
- Authenticated via `HAWKOFFER_CRON_SECRET` configured in `app.core.config`.
- Supports `X-Cron-Secret: <secret>` or `Authorization: Bearer <secret>`.
- Verified via `secrets.compare_digest` (constant time against timing attacks).
- Returns `401 Unauthorized` on missing or invalid secrets.
- Returns `409 Conflict` if a cycle is already actively running.
- Returns telemetry `CronMonitoringResponse`:
  ```json
  {
    "status": "success",
    "companies_targeted": 2,
    "companies_scraped": 2,
    "companies_failed": 0,
    "jobs_fetched": 4,
    "jobs_ingested": 4,
    "candidates_generated": 2,
    "notifications_created": 2,
    "duration_ms": 142.5
  }
  ```

---

## 3. Test Verification Matrix

### 3.1 Milestone 15 Dedicated Test Suite (`tests/test_m15_centralized_monitoring.py`)
10 tests passed (100% success rate):
1. `test_single_scrape_per_monitored_company_across_multiple_subscribers`: Verifies company with multiple active subscribers is scraped exactly once.
2. `test_multi_user_discovery_fan_out_to_correct_subscribers`: Verifies 2 users subscribed to same company both receive notifications for matching jobs.
3. `test_job_filtering_criteria_applied_per_user`: Verifies User A (Backend) receives only Backend jobs, User B (Frontend) receives only Frontend jobs from the same company.
4. `test_repeated_cycles_do_not_duplicate_notifications`: Verifies idempotency across repeated runs; 0 duplicates generated.
5. `test_unsubscribed_users_do_not_receive_notifications`: Verifies users subscribed to Company A never receive Company B's jobs.
6. `test_user_without_active_profile_receives_no_notifications`: Verifies subscriptions without active preference profile are safely ignored.
7. `test_partial_failure_resilience_in_centralized_cycle`: Verifies cycle completes and reports `partial_failure` without crash if one scraper fails.
8. `test_cron_monitor_endpoint_authentication`: Verifies 401 without secret, 200 with `X-Cron-Secret` or Bearer token.
9. `test_cron_monitor_endpoint_prevents_concurrent_runs`: Verifies 409 Conflict returned during active in-flight cycle.
10. `test_desktop_legacy_profiles_backward_compatibility`: Verifies single-user unauthenticated desktop profiles (`user_id IS NULL`) receive matching notifications without subscriptions.

### 3.2 Full Test Suite Regressions
- **Backend Test Suite**: 357 passed (347 existing + 10 new M15 tests).
- **Frontend Vitest Suite**: 34 passed across 9 test files.
- **Ruff Code Linter**: Zero linting or formatting errors (`ruff check app tests`).
- **MyPy Static Typing**: Zero typing issues across 118 source files (`mypy app tests`).
- **Frontend Production Build**: Clean TypeScript compilation and Vite bundle generation (`npm run build`).

---

## 4. Test Environment Distinction Notice

- **Executed Runtime Tests**: Ran under Python 3.13 with SQLite WAL and `aiosqlite`.
- **PostgreSQL Compilation**: Verified via SQLAlchemy PostgreSQL compiler dialect tests and Alembic migrations.
- **Live Cloud PostgreSQL Testing**: Scheduled for deployment acceptance milestone (M18).
