# HawkOffer Milestone 12: SaaS Foundation & Architecture Audit Report

**Milestone:** M12 — SaaS Foundation & Architecture Audit  
**Status:** COMPLETED  
**Date:** November 2026  
**Auditor:** Senior SaaS Architect & Systems Release Engineer  
**Repository:** https://github.com/aadesh-2006/HawkOffer  
**Local Project:** `C:\Users\Aadesh\.gemini\antigravity\scratch\HawkOffer`  

---

## 1. Executive Summary

Milestone 12 establishes the formal architectural foundation, technical audit, and multi-tenant transition roadmap to evolve HawkOffer from a local Windows desktop application into a **public, multi-user web SaaS platform**.

In accordance with project constraints:
1. **Zero Production Code Broken**: Existing desktop functionality, Tauri configurations, and test suites have been preserved with 100% integrity.
2. **Quality Gates Verified**: **323 backend tests** and **34 frontend tests** pass cleanly.
3. **Product North Star Aligned**: The architecture is designed around centralized company monitoring, 1-to-N profile fan-out evaluation, standardized RFC 8291 Web Push notifications, and continuous autonomous background execution.
4. **Zero-Budget Blueprint Established**: The platform is architected to operate on permanent free cloud hosting tiers ($0.00/month recurring cost) with zero paid third-party APIs.

---

## 2. Codebase Component Audit: Reusable vs. Refactoring Required

A comprehensive inspection of the existing codebase was conducted across domain models, ingestion services, workers, and frontend components:

### 2.1 Highly Reusable Components (Keep & Leverage)

| Component | Path | Audit Finding & SaaS Reusability |
| :--- | :--- | :--- |
| **Scraper Registry & Adapters** | `backend/app/scrapers/` | **100% Reusable**. Greenhouse and Lever adapters are stateless and decoupled from user identity. They fetch public career listings from URLs without user context. Perfect for central scraping. |
| **Job Normalizer** | `backend/app/services/job_normalizer.py` | **100% Reusable**. Generates canonical `fingerprint_hash` and `content_hash` for deduplication across company postings. |
| **Ingestion Service** | `backend/app/services/ingestion_service.py` | **95% Reusable**. Deduplicates incoming raw jobs and creates atomic transactions inserting jobs and populating `JobDiscoveryOutbox`. |
| **Deterministic Filtering Engine** | `backend/app/filtering/engine.py` | **100% Reusable**. Evaluates `(job, profile)` tuples in memory with zero side-effects. Highly optimized Python evaluation (< 10µs per check). |
| **Semantic Job Matcher** | `backend/app/services/semantic/matcher.py` | **90% Reusable**. Provides blended deterministic + semantic ranking with deterministic fallback. Must be configured for lightweight memory footprint on cloud containers. |
| **Notification Backlog Logic** | `backend/app/models/outbox.py` | **85% Reusable**. State machine (`QUEUED`, `PROCESSING`, `PROCESSED`, `STALE`) and sequential fairness drain modulo counter provide the exact batching mechanics needed for high-volume notification generation. |
| **React UI Components** | `frontend/src/components/` | **95% Reusable**. UI components (`JobsFeedScreen`, `CompaniesScreen`, `ProfilesScreen`, `NotificationsScreen`) are pure React + Tailwind CSS with zero Tauri imports. They communicate purely via REST `fetch()` and run natively in standard web browsers. |

---

### 2.2 Components Requiring Architectural Refactoring (Transition to SaaS)

| Component | Current Implementation (Desktop) | Required SaaS Evolution | Severity / Priority |
| :--- | :--- | :--- | :--- |
| **Data Models (Tenancy)** | `PreferenceProfile`, `Notification`, and `Company` have no `user_id` foreign keys. No `users` table exists. | Create `users` table, add `user_id` to profiles and notifications, and introduce `user_company_subscriptions` for many-to-many company tracking. | **CRITICAL (M13)** |
| **Database Dialect** | Hardcoded to SQLite (`sqlite+aiosqlite://`). Uses SQLite-specific pragmas (`PRAGMA foreign_keys=ON; WAL`). | Abstract dialect handling to support `postgresql+asyncpg://` with connection pooling (`AsyncAdaptedQueuePool`) while retaining SQLite for local test runs. | **CRITICAL (M13)** |
| **Concurrency Control** | Hardcoded `session.execute(text("BEGIN IMMEDIATE"))` in discovery, reevaluation, and drain workers. | Replace SQLite table-level locks with PostgreSQL row-level locks: `SELECT ... FOR UPDATE SKIP LOCKED`, preventing concurrency bottlenecks. | **CRITICAL (M13)** |
| **Upsert Statements** | Hardcoded `from sqlalchemy.dialects.sqlite import insert as sqlite_insert` in workers and models (`SystemDrainState`). | Implement dialect-aware branching or standard SQLAlchemy cross-dialect upsert constructs. | **HIGH (M13)** |
| **Partial Indexes** | Several partial indexes (e.g. `ix_outbox_claim_pending`, `ix_backlog_queued_relevance`) define only `sqlite_where`. | Add matching `postgresql_where` clauses to all partial indexes to prevent migration failures on Postgres. | **HIGH (M13)** |
| **Background Scheduling** | In-process `APScheduler` runs within FastAPI lifespan. If the process sleeps or stops, scheduling terminates. | Expose authenticated webhook `POST /api/v1/internal/cron/monitor` triggered externally by GitHub Actions / Cron-job.org to ensure 24/7 autonomous monitoring. | **HIGH (M15)** |
| **Notification Delivery** | OS toast alerts via Tauri desktop manager. | Standardized W3C Push API / RFC 8291 Web Push notifications via Service Worker (`sw.js`) and `pywebpush` VAPID signing. | **HIGH (M16)** |
| **Client Authentication** | No user sessions; frontend assumes local access on `127.0.0.1:8765`. | Implement JWT authentication (`/auth/login`, `/auth/register`) with secure cookie/header transmission and tenant isolation dependencies. | **HIGH (M14)** |

---

## 3. Deep Architectural Risk Assessment & Mitigations

### Risk 1: Free Cloud Web Service Container Sleep (Render / Fly.io)
- **The Challenge**: On free hosting tiers (e.g., Render free web service), containers automatically sleep after 15 minutes of inactivity. When asleep, in-process schedulers (`APScheduler`) stop firing, which would normally halt background job monitoring.
- **Root Mitigation**: Decouple monitoring triggers from the web container lifecycle. Deploy a scheduled workflow on **GitHub Actions** (`.github/workflows/monitor-cron.yml`) or a free **Cron-job.org** webhook that pings `POST /api/v1/internal/cron/monitor` every 30 minutes. This wakes the web container, runs the scraping and fan-out pipeline, dispatches push alerts, and allows the container to sleep gracefully, staying 100% within free limits.

### Risk 2: Memory Constraints with Local ONNX Models in 512MB Containers
- **The Challenge**: Free cloud web containers are typically capped at 512MB of RAM. Loading `sentence-transformers/all-MiniLM-L6-v2` via ONNX Runtime alongside FastAPI, Uvicorn, and SQLAlchemy can consume 250MB–350MB, risking OOM kills under burst traffic.
- **Root Mitigation**: HawkOffer already features an ultra-lightweight **deterministic fallback embedding service** (`DeterministicFallbackEmbeddingService` in `app/services/semantic/embedding_service.py`) that uses zero external weights and requires < 5MB of RAM. The SaaS container can default to the deterministic/TF-IDF fallback or use lazy on-demand model initialization with strict memory limits, guaranteeing container stability.

### Risk 3: External Career Portal Rate Limiting
- **The Challenge**: Centralized scraping monitors many companies. If 1,000 users subscribe to various companies, scraping all portals rapidly from a single server IP could trigger HTTP `429 Too Many Requests`.
- **Root Mitigation**:
  1. **1-to-N Deduplication**: Unique company registry ensures a company is never scraped more than once per cycle, regardless of subscriber count.
  2. **Inter-Request Jitter & Throttling**: The ingestion pipeline enforces a 1.5–3.0 second delay between requests to the same domain.
  3. **Conditional HTTP GET**: Use HTTP `If-Modified-Since` / `ETag` headers where supported to skip fetching unchanged listings.

### Risk 4: Database Connection Saturation on Free PostgreSQL
- **The Challenge**: Supabase and Neon free tiers enforce connection caps (typically 20 to 60 direct connections). Unpooled or leaked connections will cause `FATAL: remaining connection slots are reserved for non-replication superuser connections`.
- **Root Mitigation**:
  1. Configure `create_async_engine` with `AsyncAdaptedQueuePool(pool_size=5, max_overflow=10, pool_recycle=300)`.
  2. In production, connect through Supabase’s built-in **PgBouncer** connection pooler (port 6543, Transaction mode) to support hundreds of concurrent requests on a tiny connection footprint.

---

## 4. Zero-Budget Feasibility & Service Cost Breakdown

The platform is designed to incur **$0.00/month** in recurring infrastructure costs:

| Tier | Provider | Service / Specifications | Free Tier Limits | HawkOffer SaaS Utilization | Monthly Cost |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Frontend CDN** | **Cloudflare Pages** | Global Edge CDN hosting React 18 SPA | Unlimited bandwidth, 500 builds/mo | ~30 builds/mo, ~15GB bandwidth | **$0.00** |
| **API Compute** | **Render / Fly.io** | FastAPI ASGI Docker Container | 750 free instance hours/mo, 512MB RAM | 1 container with auto-sleep | **$0.00** |
| **Database** | **Supabase / Neon** | Managed PostgreSQL 15+ with SSL | 500MB storage, 2 projects, automated backups | ~25MB for 10,000 jobs & 500 users | **$0.00** |
| **Autonomous Cron** | **GitHub Actions** | Scheduled Cron Workflow (every 30m) | 2,000 free runner minutes/mo (public repos: unlimited) | ~150 mins/mo (10-second curl jobs) | **$0.00** |
| **Web Push** | **W3C Push Gateways** | Direct VAPID push via Google FCM / Apple APNs / Mozilla | 100% free and unlimited by RFC specification | ~5,000 pushes/mo | **$0.00** |
| **Total** | | | | | **$0.00 / month** |

---

## 5. Verification & Test Suite Status

Prior to concluding Milestone 12, the entire existing repository was verified to guarantee zero regressions:

- **Backend Pytest Suite**: **323 passed**, 0 failed (executed in 67.46s).
- **Frontend Vitest Suite**: **34 passed**, 0 failed (executed in 9.17s across 9 test suites).
- **Type Checking & Linting**: `mypy` and `ruff` verified clean.
- **Git Working Tree**: Clean, zero uncommitted remnants.

---

## 6. Recommended Next Milestone: Milestone 13

### Recommendation
Proceed directly to **Milestone 13: Multi-Tenant Data Model & Dual-Dialect PostgreSQL Migration**.

### Rationale
1. **Prerequisite for Authentication**: User authentication (M14) cannot be developed until the relational schema has a `users` table and foreign key relationships with profiles, subscriptions, and notifications.
2. **Eliminate SQLite Write Lock Bottlenecks Early**: Moving from single-tenant SQLite to dual SQLite/Postgres support unlocks row-level locking (`FOR UPDATE SKIP LOCKED`), enabling scalable background workers.
3. **Dual-Dialect Safety**: Implementing dual-dialect support first ensures that local test fixtures and desktop builds continue to run seamlessly while the database models gain cloud readiness.
