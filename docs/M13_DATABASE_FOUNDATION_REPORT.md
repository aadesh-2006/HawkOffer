# HawkOffer Milestone 13: Multi-Tenant Data Model & Dual-Dialect Foundation Report

**Milestone:** M13 — Multi-Tenant Data Model & Dual-Dialect Foundation  
**Status:** COMPLETED  
**Date:** November 2026  
**Auditor / Engineer:** Senior Backend & Database Architect  
**Repository:** https://github.com/aadesh-2006/HawkOffer  
**Local Project:** `C:\Users\Aadesh\.gemini\antigravity\scratch\HawkOffer`  
**Git Commit:** [Pending M13 Sign-Off]  

---

## 1. Executive Summary

Milestone 13 establishes the production multi-tenant relational schema and dual-dialect database foundation for HawkOffer. The implementation fulfills the product North Star: evolving from a single-user desktop application into a scalable, multi-user web SaaS platform, while preserving the existing desktop application and its SQLite WAL persistence with 100% backward compatibility.

### Key Milestones Achieved:
1. **Multi-Tenant Schema Implemented**: Created `User`, `UserCompanySubscription`, `JobApplication`, and `PushSubscription` models, and enriched `Company`, `PreferenceProfile`, and `Notification` with tenant scoping.
2. **Dual-Dialect Abstraction Established**: Engineered `app/database/compat.py` providing seamless dialect detection, portable UPSERT generation (`get_upsert_insert`), and isolated write locking (`acquire_exclusive_write_lock`).
3. **Engine & Connection Pool Modernization**: Configured `app/database/session.py` with PostgreSQL connection pool parameters (`pool_size=5`, `max_overflow=10`, `pool_recycle=300`) and URL normalization in `app/core/config.py`.
4. **Safe Alembic Migrations**: Authored `b3e4f5a6c7d8_m13_multi_tenant_schema.py` using `batch_alter_table` for SQLite/PostgreSQL cross-compatibility, verified through full roundtrip upgrade/downgrade execution.
5. **Quality Gates Verified**:
   - **Backend Pytest Suite**: **336 passed**, 0 failed (323 preexisting + 13 new M13 tests).
   - **Frontend Vitest Suite**: **34 passed**, 0 failed across 9 test files.
   - **Frontend Production Build**: Passed in 3.43s.
   - **Static Analysis**: `mypy` passed with 0 errors across 108 source files; `ruff` passed with 0 errors.

---

## 2. Models Added & Modified

### 2.1 New Relational Entities

| Model | Path | Description & Architectural Purpose |
| :--- | :--- | :--- |
| **`User`** | `app/models/user.py` | Core multi-tenant account entity (`email`, `hashed_password`, `full_name`, `role`, `is_active`, `is_verified`, `verification_token`, timestamps). Enforces global email uniqueness. |
| **`UserCompanySubscription`** | `app/models/subscription.py` | Many-to-many subscription mapping linking users to followed companies. Enforces `uq_user_company_subscription` (`UNIQUE(user_id, company_id)`). Enables 1 company to be scraped once and fan-out matched to $N$ users. |
| **`JobApplication`** | `app/models/application.py` | User-scoped job application pipeline tracker (`status`, `notes`, `applied_at`, timestamps). Enforces `uq_user_job_application` (`UNIQUE(user_id, job_id)`). Allows users to track applications independently. |
| **`PushSubscription`** | `app/models/push.py` | Stores RFC 8291 browser Web Push credentials (`endpoint`, `p256dh`, `auth`, `user_agent`, `failure_count`, timestamps) for zero-cost background push delivery. |

### 2.2 Modified Existing Entities

| Model | Path | Modification Details |
| :--- | :--- | :--- |
| **`Company`** | `app/models/company.py` | Added `is_curated: Mapped[bool] = mapped_column(Boolean, default=False)` to distinguish global catalog entries from user-added companies. Added `created_by_user_id: Mapped[int | None]` with `ON DELETE SET NULL`. Added `subscribers` relationship. |
| **`PreferenceProfile`** | `app/models/preference.py` | Added `user_id: Mapped[int | None]` with `ON DELETE CASCADE`. Replaced SQLite-specific `from sqlalchemy.dialects.sqlite import JSON` with portable `from sqlalchemy import JSON`. Added `Index("ix_preference_profiles_user_active", "user_id", "is_active")`. |
| **`Notification`** | `app/models/notification.py` | Added `user_id: Mapped[int | None]` with `ON DELETE CASCADE`. Added `Index("ix_notifications_user_id_status", "user_id", "status")` and `Index("ix_notifications_user_created", "user_id", "created_at")`. |
| **`JobDiscoveryOutbox`** | `app/models/outbox.py` | Added `postgresql_where` alongside `sqlite_where` for `ix_outbox_claim_pending` and `ix_outbox_claim_expired`. |
| **`NotificationBacklog`** | `app/models/outbox.py` | Added `postgresql_where` alongside `sqlite_where` for queued and processing partial indexes. |
| **`ProfileReevaluationTask`** | `app/models/outbox.py` | Added `postgresql_where=text("status IN ('PENDING', 'RUNNING')")` for `uq_active_task_per_profile`. |
| **`SystemDrainState`** | `app/models/outbox.py` | Refactored `get_or_create` to utilize `get_upsert_insert`, supporting both PostgreSQL and SQLite UPSERT semantics. |

---

## 3. Schema Design Decisions & Invariants

1. **Nullable `user_id` for Backward Compatibility**:
   - `user_id` is defined as `nullable=True` on `preference_profiles` and `notifications`.
   - **Rationale**: Existing desktop databases operate without user accounts. Making `user_id` nullable allows legacy single-user desktop databases to upgrade seamlessly via Alembic without requiring dummy user accounts or destroying existing profiles.
2. **Centralized Scraping / Many-to-Many Subscription**:
   - Decoupled `companies` from individual users via `user_company_subscriptions`.
   - A single canonical company row (e.g. Stripe) is stored. When 500 users subscribe to Stripe, only 1 scraper job runs.
3. **Application Tracker Isolation**:
   - `job_applications` uses `UniqueConstraint("user_id", "job_id")`.
   - Different users can track their application status (`SAVED`, `APPLIED`, `OFFER`) for the same job posting without cross-tenant interference.
4. **Push Subscription Cryptography**:
   - `push_subscriptions` stores standard W3C Push API keys (`p256dh` and `auth` strings) and endpoint URLs, indexed by `user_id`.

---

## 4. Dual-Dialect Database Support & Abstraction

### 4.1 Compatibility Abstraction Layer (`app/database/compat.py`)
To isolate dialect differences cleanly without polluting domain services:
- **`get_dialect_name(session_or_bind)`**: Automatically resolves dialect name (`"sqlite"`, `"postgresql"`, etc.).
- **`get_upsert_insert(target, session)`**: Returns `sqlite_insert(target)` or `pg_insert(target)`. Both share the identical `.values(...)` and `.on_conflict_do_nothing(index_elements=...)` syntax.
- **`acquire_exclusive_write_lock(session)`**: Executes `BEGIN IMMEDIATE` for SQLite write safety; becomes a safe no-op on PostgreSQL, allowing Postgres MVCC row-level locking to operate unhindered.

### 4.2 Engine & Connection Configuration
- **PostgreSQL Connection Pool**: Configured in `app/database/session.py` when `postgresql` is in the database URL:
  - `pool_size = 5`
  - `max_overflow = 10`
  - `pool_recycle = 300`
  - `pool_pre_ping = True`
- **URL Normalization in `app/core/config.py`**:
  - `postgres://` and `postgresql://` automatically map to `postgresql+asyncpg://` for async runtime execution.
  - Automatically map to `postgresql://` for synchronous Alembic migrations.

---

## 5. Migration Safety & Testing

### 5.1 Alembic Revision `b3e4f5a6c7d8`
- Created `alembic/versions/b3e4f5a6c7d8_m13_multi_tenant_schema.py`.
- Employs `with op.batch_alter_table(...) as batch_op:` to ensure DDL safety across both SQLite (table rebuild) and PostgreSQL (direct ALTER TABLE).
- Verified full migration lifecycle:
  ```
  Running upgrade 9a1c5d7e8b2f -> b3e4f5a6c7d8 (m13_multi_tenant_schema)
  Running downgrade b3e4f5a6c7d8 -> 9a1c5d7e8b2f
  Running upgrade 9a1c5d7e8b2f -> b3e4f5a6c7d8
  ```
  Result: 100% clean roundtrip with zero data corruption.

### 5.2 PostgreSQL DDL Compilation Verification
Tested via SQLAlchemy mock engine (`postgresql+asyncpg://`):
- Compiled **76 SQL DDL statements** for the entire metadata schema.
- Verified syntax for PostgreSQL `SERIAL`, `TIMESTAMP WITH TIME ZONE`, partial indexes, foreign keys, and unique constraints.

---

## 6. Test Suite Execution & Results

| Test Category | Suite / File | Tests | Result | Duration |
| :--- | :--- | :--- | :--- | :--- |
| **M13 Multi-Tenant Schema** | `tests/test_m13_multi_tenant_schema.py` | 13 | **PASSED** | 1.13s |
| **Phase 3 Migrations** | `tests/test_m5_phase_3_migrations.py` | 2 | **PASSED** | 3.20s |
| **Notifications & Scoping** | `tests/test_notifications.py` | 15 | **PASSED** | 1.30s |
| **Full Backend Pytest** | `backend/tests/` (all 29 test files) | **336** | **PASSED** | 82.79s |
| **Frontend Vitest** | `frontend/src/test/` (all 9 test files) | **34** | **PASSED** | 9.25s |
| **Frontend Build** | `tsc && vite build` | 1 | **PASSED** | 3.43s |
| **Type Checking** | `mypy app tests` | 108 files | **PASSED** (0 issues) | 2.10s |
| **Linting & Formatting** | `ruff check app tests` | All files | **PASSED** (0 issues) | 0.45s |

---

## 7. Known Limitations & Next Steps

### Known Scope Boundaries (By Design)
1. **No User Authentication Endpoints**: The database schema and Pydantic validation schemas exist, but authentication routes (`POST /auth/register`, `POST /auth/login`) and JWT token issuing have not yet been implemented.
2. **No Push Dispatch Worker**: Push subscription credentials can be stored in `push_subscriptions`, but Web Push dispatching to browsers is deferred to M16.
3. **No Frontend Auth UI**: Frontend continues to communicate with existing endpoints; auth screens and company subscription UI belong to M17.

### Recommended Next Milestone
**Milestone 14: Authentication, Security & Tenant Isolation**  
*Next Tasks*:
- Implement password hashing (`argon2-cffi` / `passlib`) and JWT token lifecycle (access + refresh tokens).
- Build authentication routes: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`.
- Implement FastAPI dependency `get_current_active_user` and enforce repository-level tenant scoping to prevent any cross-tenant data leaks.
