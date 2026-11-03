# HawkOffer Development Roadmap

This document outlines the phased development roadmap for **HawkOffer**. Each milestone represents an incremental, testable layer of functionality. Development proceeds strictly one milestone at a time to ensure stability, maintainability, and code quality.

---

## Milestone Overview

```mermaid
flowchart LR
    M0["M0: Architecture & Foundation"] --> M1["M1: Core Models & SQLite Persistence"]
    M1 --> M2["M2: Scraper Adapters & Normalizer"]
    M2 --> M3["M3: Deduplication & Change Detection"]
    M3 --> M4["M4: APScheduler & Multi-Attribute Filtering"]
    M4 --> M5["M5: Outbox, Keyset Reeval & Fair Drain Pipeline"]
    M5 --> M6["M6: Backend API & Application Runtime (Completed)"]
    M6 --> M7["M7: React Dashboard UI & Tauri Shell (Completed)"]
    M7 --> M8["M8: Desktop Runtime Integration & V1 Stabilization (Completed)"]
    M8 --> M9["M9: Windows Packaging & Native Desktop System Tray"]
    M9 --> M10["M10: Local AI Semantic Resume Matching"]
```

---

## Milestones & Phased Execution

### Milestone 0: Architecture, Project Foundation & Guidelines (CURRENT)
- **Status**: Completed / Awaiting Approval
- **Focus**: Technical design, requirements definition, component specifications, and directory structure.
- **Deliverables**:
  - `README.md`: Product vision, technology overview, and development status.
  - `docs/ARCHITECTURE.md`: Complete system architecture, component breakdown, database schema, and API proposals.
  - `docs/ROADMAP.md`: Structured development plan with acceptance criteria.
- **Acceptance Criteria**:
  - All 10 architectural modules documented with clear boundaries.
  - Ethical scraping guidelines and constraints established.
  - Technical decisions reviewed and signed off before code authoring.

---

### Milestone 1: Python Project Setup, Data Models & Persistence Layer
- **Status**: Implemented / Ready for Review
- **Focus**: Python foundation, cross-platform configuration management, SQLite database engine, repositories, and automated test suite.
- **Tasks**:
  1. Initialize Python project environment with `pyproject.toml` (dependencies: `pydantic`, `pydantic-settings`, `sqlalchemy`, `aiosqlite`, `alembic`).
  2. Configure structured logging with standard formatting and rotation.
  3. Implement SQLAlchemy models: `Company`, `Job`, `PreferenceProfile`, `Notification`, `MonitoringLog`.
  4. Configure SQLite with WAL mode (`PRAGMA journal_mode=WAL;`).
  5. Set up initial Alembic migration scripts.
  6. Build CRUD repositories: `CompanyRepository`, `JobRepository`, `LogRepository`.
  7. Write unit tests for database transactions and repository operations using an in-memory SQLite database.
- **Acceptance Criteria**:
  - Database schema initializes correctly via Alembic migrations.
  - Repositories pass all automated unit tests (`pytest`).
  - Strict type checking with `mypy` passes with zero errors.

---

### Milestone 2: Career Scraper Adapters & Normalization Engine
- **Focus**: Building the adapter framework, initial ATS adapters (Greenhouse, Lever, Ashby), and job normalization.
- **Tasks**:
  1. Define `BaseCareerAdapter` abstract base class with standard error handling and rate-limiting.
  2. Implement `GreenhouseAdapter` using the official Greenhouse public JSON board API (`/v1/boards/{board_token}/jobs`).
  3. Implement `LeverAdapter` using the Lever Postings API (`/v0/postings/{company}`).
  4. Implement `AshbyAdapter` for public Ashby job feeds.
  5. Implement `JobNormalizer` to clean titles, parse employment types, resolve canonical URLs (strip tracking query params), and normalize locations.
  6. Implement adapter auto-detection logic based on target URL structure.
  7. Write unit tests using mocked HTTP payloads to verify robust extraction and error resilience.
- **Acceptance Criteria**:
  - Greenhouse, Lever, and Ashby adapters extract complete job listings reliably from public sample feeds.
  - Normalizer successfully resolves titles, locations, and clean URLs without tracking tokens.
  - Network timeouts and HTTP errors (404, 429, 500) are caught gracefully and logged.

---

### Milestone 3: Playwright Dynamic Scraper & Deduplication Engine
- **Focus**: Headless browser automation for dynamic job portals, content fingerprinting, and change detection.
- **Tasks**:
  1. Implement `GenericPlaywrightAdapter` using `playwright-python` with headless execution, minimal resource overhead, and explicit selector waits.
  2. Ensure Playwright browser instances are launched on-demand and closed immediately after scraping to prevent RAM leaks.
  3. Implement `JobHasher`:
     - `fingerprint_hash`: SHA-256 of normalized `(company_id + title + location + clean_url)`.
     - `content_hash`: SHA-256 of `(description_text)`.
  4. Implement `DeduplicationEngine`:
     - Compares fetched jobs against existing records in SQLite.
     - Classifies jobs into states: `NEW`, `ACTIVE`, `UPDATED`, `CLOSED`.
  5. Write tests covering state transitions and content modification detection.
- **Acceptance Criteria**:
  - Headless Playwright scraper successfully extracts job listings from client-rendered career pages.
  - Deduplication prevents duplicate entries for previously scraped jobs.
  - Changes in job description or title trigger an `UPDATED` status.

---

### Milestone 4: Scheduler Engine & Windows Native Notifications
- **Focus**: Automated background polling and native Windows alert dispatching.
- **Tasks**:
  1. Integrate `APScheduler` (`AsyncIOScheduler`) to manage target check intervals.
  2. Implement schedule jitter ($\pm 15\%$) to avoid synchronized network requests.
  3. Implement `PreferenceEvaluator` to match new listings against user keywords and location criteria.
  4. Implement `WindowsNotificationService` using native Windows toast notifications (`windows-toasts`):
     - Displays job title, company, and location.
     - Clicking the notification opens the listing in the default web browser.
  5. Implement notification throttling and batching for multi-job publishing bursts.
  6. Build automated tests verifying scheduler job lifecycle and filter evaluations.
- **Acceptance Criteria**:
  - Scheduler runs target checks periodically in the background without user intervention.
  - Toast notification appears when an eligible new job is discovered.
  - Multiple simultaneous new jobs are grouped into a clean summary notification.

---

### Milestone 5: Local FastAPI REST Daemon & Background Processing Pipeline
- **Status**: Completed (Phase 1, Phase 2, Phase 3 Units 1–8)
- **Focus**: REST endpoints, notification delivery, and the transactional background processing pipeline.
- **Phase 3 Units Completed**:
  - **Unit 1: Data Contracts & Schema Migration**: Alembic revision `8de4342be7a1` adding `revision` to `preference_profiles`, `job_discovery_outbox`, `notification_backlog`, `profile_reevaluation_tasks`, and `system_drain_state`.
  - **Unit 2: Transactional Outbox Ingestion**: Atomic emission of `JobDiscoveryOutbox` (PENDING) during new job ingestion with idempotency and rollback safety.
  - **Unit 3: Repository Lifecycle & Backlog Query Optimization**: Direct partial indexes supporting $O(\log N)$ queued and expired candidate claims without temporary b-trees.
  - **Unit 4: Outbox Discovery Worker**: Three-phase worker architecture (Phase A: `BEGIN IMMEDIATE` claim, Phase B: in-memory profile snapshot filtering, Phase C: fenced commit).
  - **Unit 5 & 5.1: Profile Revision & Keyset Reevaluation Worker**: Atomic profile revision increment, old backlog row invalidation, and bounded keyset historical reevaluation with asynchronous lease heartbeats and stale-lease fencing.
  - **Unit 6: Backlog Drain Worker & Modulo-5 Fairness**: Persistent counter sequential allocation (4 relevance slots, 1 FIFO slot with dynamic fallback), atomic candidate claims, and deduplicated notification promotion.
  - **Unit 7 – 7.5: Scheduler Lifecycle Integration & Hardening**: Unified `MonitoringOrchestrator` lifecycle with closed startup gate, durable shutdown intent, multi-shutdown reference counting (`_active_shutdowns`), and complete admission closure throughout shutdown.
  - **Unit 8: End-to-End Integration, Documentation & Final Audit**: 14 end-to-end integration tests, full migration and downgrade audits, query plan performance verification, and architecture documentation.
- **Acceptance Criteria**:
  - All 155+ test cases pass with >90% coverage.
  - Static type checking (`mypy`) passes with zero errors.
  - Linting and formatting (`ruff`) pass with zero errors.
  - Migration upgrade and downgrade verified without data loss for user notifications.

---

### Milestone 6: Backend API & Application Runtime Integration (COMPLETED)
- **Status**: Completed / Ready for Review
- **Focus**: Exposing the complete M0–M5 backend via a clean, stable local REST API and establishing the application runtime for future Tauri + React desktop consumers.
- **Tasks**:
  1. Implement FastAPI application factory (`app.main:create_app`) with configurable lifespan management, startup DB verification, orchestrator coordination, and graceful teardown.
  2. Configure localhost-only security boundaries and strict CORS origins (`http://127.0.0.1:1420`, `http://localhost:1420`, `http://127.0.0.1:5173`, `http://localhost:5173`, `tauri://localhost`).
  3. Implement standardized error handling (400 for `ValueError`, 409 for `IntegrityError`, 404 for missing resources, 503 for closed gates).
  4. Build REST API v1 router modules:
     - `system`: `/api/v1/health` and `/api/v1/system/status`.
     - `companies`: CRUD, pagination, `/activate`, `/deactivate`, on-demand `/trigger`, and `/scan-state`.
     - `profiles`: CRUD, `/activate`, `/deactivate`, integrating `ProfileUpdateService` to preserve revision increments and reevaluation tasks.
     - `jobs`: Paginated listings with search and multi-parameter filtering (`company_id`, `status`, `job_type`, `employment_type`, `graduation_year`).
     - `notifications`: Paginated feeds, unread badges (`/unread-count`), lifecycle mutations (`/read`, `/unread`, `/dismiss`, `/restore`), and batch actions (`/batch/read`, `/batch/dismiss`).
     - `monitoring`: Pipeline and scheduler runtime status, out-of-band triggers for discovery, reevaluation, and drain workers, and pause/resume controls.
  5. Add repository pagination and scalar count helpers (`list_paginated`, `count_companies`, `count_filtered`, `count_notifications`) to eliminate full table in-memory scans.
  6. Author comprehensive automated tests for lifespan, CORS, and all API endpoints (`tests/test_app_lifespan.py`, `tests/test_api_v1.py`).
- **Acceptance Criteria**:
  - All 284 backend tests pass with zero failures.
  - Strict static type checking (`mypy`) passes with zero errors in strict mode across 82 source files.
  - Linting and formatting (`ruff`) pass with zero violations.
  - Preserves M5 Phase 3 outbox, keyset reevaluation, and fairness-aware drain semantics.

---

### Milestone 7: React + Vite + TypeScript Desktop Dashboard & Tauri Shell (COMPLETED)
- **Status**: Completed / Ready for Review
- **Focus**: Usable, polished local-first desktop application UI consuming the existing FastAPI REST backend.
- **Tasks**:
  1. Scaffold frontend desktop UI using React 18, TypeScript, Vite 6, Tailwind CSS, Lucide icons, and Tauri 2.x configuration.
  2. Implement centralized typed API client (`frontend/src/services/api.ts`) and global reactive state supervisor (`AppContext`).
  3. Build reusable atomic UI components (`Badge`, `Button`, `Modal`, `EmptyState`, `LoadingSpinner`) with dark/light theme support.
  4. Develop full application views:
     - **Dashboard**: Real-time KPI statistics (`companies_count`, `total_jobs_count`, `unread_notifications_count`), background worker status monitors, quick manual trigger buttons, and recent job discoveries feed.
     - **Jobs Feed**: Multi-attribute filter controls (company, status, job type, graduation year), debounced search, responsive paginated table, and job detail inspection modal.
     - **Company Targets Manager**: CRUD interface for monitored companies, ATS platform badges, and on-demand scraping trigger with stats feedback.
     - **Preference Profiles**: Multi-tag preference editors, match score thresholds, active state toggles, and historical reevaluation notice banner.
     - **Notification Inbox**: Read/unread/dismissed tab filters, individual and batch operations, and unread count badges.
     - **Monitoring Controls**: APScheduler runtime state, lifecycle gate indicators, worker run controls, and execution results inspector.
     - **Settings & Connectivity**: Endpoint URL configuration, live health diagnostics, theme switcher, and daemon startup snippet.
  5. Author comprehensive unit and integration test suite across API service and all screen components (29 tests passing with Vitest).
- **Acceptance Criteria**:
  - All 29 frontend Vitest tests pass cleanly.
  - TypeScript compilation (`tsc --noEmit`) passes with zero errors.
  - Production build (`vite build`) outputs optimized desktop web bundle.
  - All 285 backend tests pass with zero regressions.

---

### Milestone 8: Desktop Runtime Integration & V1 Stabilization (COMPLETED)
- **Status**: Completed / Ready for Review
- **Focus**: Launch and operate HawkOffer as a coherent desktop application with managed backend lifecycle, readiness checks, graceful shutdown, single-instance enforcement, first-run onboarding, and end-to-end user workflow verification.
- **Tasks**:
  1. Implement Desktop Runtime Manager (`backend/app/desktop_runtime.py`):
     - Single-instance PID lockfile management (`hawkoffer.pid` in application data directory).
     - Cross-platform process liveness verification (Windows Win32 `OpenProcess`/`GetExitCodeProcess` and Unix `os.kill(pid, 0)`).
     - Bounded readiness supervisor (`check_readiness()`, `wait_for_readiness()`) polling `/api/v1/health` with strict database connection checks.
     - Stale PID lockfile detection and cleanup.
     - Graceful process shutdown coordination via `SIGTERM` and bounded timeout fallback.
     - CLI entry point: `python -m app.desktop_runtime start|stop|restart|status|readiness`.
  2. Implement Frontend Startup & Readiness Gate:
     - `BackendReadinessGate.tsx`: Splash overlay during initial boot verification and actionable recovery UI with copyable launch command if daemon is offline.
     - `AppContext.tsx`: Reactive readiness state (`readinessState: "checking" | "ready" | "failed" | "skipped"`), timeout fallback, and offline mode continuation.
  3. Implement First-Run Onboarding Experience:
     - `FirstRunGuide.tsx`: Clean onboarding card presented when `companies_count === 0` guiding users through adding their first monitored company (with recommended Greenhouse/Lever board slugs), configuring deterministic preference criteria, and triggering their first scan.
  4. Author Comprehensive 15-Step Automated Workflow Verification Suite:
     - `backend/tests/test_desktop_workflow.py`: Exercises all 15 stages from clean SQLite initialization in a temp directory, health readiness, company registration, profile definition, immediate ATS scan, fingerprint deduplication, discovery worker execution, backlog drain promotion, notification retrieval, job detail inspection, mark as read, profile update with revision increment & historical reevaluation, clean shutdown, and fresh database instance persistence reload.
  5. Conduct V1 Stabilization & Architecture Audit:
     - Complete review of error handling, bounded retry limits, zero LLM dependencies, zero fake metrics, and memory lifecycle.
- **Acceptance Criteria**:
  - All 15 steps in `test_desktop_workflow.py` pass automated in `pytest`.
  - All 13 unit tests in `test_desktop_runtime.py` pass cleanly.
  - All 33 frontend tests pass with Vitest.
  - Static type checks (`mypy`) pass with zero errors across 85 backend source files.
  - Linting (`ruff` and `tsc --noEmit`) passes with zero violations.
  - Production frontend build (`vite build`) generates optimized bundle cleanly.
  - Single-instance PID locking prevents duplicate backend daemon execution.


---

### Milestone 9: Native Packaging & Desktop Release Engineering (COMPLETED)
- **Status**: Completed / Ready for Review
- **Focus**: Distribute HawkOffer as a genuine desktop application with a bundled standalone Python backend runtime, native Tauri process lifecycle integration, persistent data protection, and a reproducible release build pipeline.
- **Tasks**:
  1. Standalone Python Backend Executable Packaging:
     - `backend/app/bundle_entry.py`: Frozen environment path resolution (`sys._MEIPASS`), automatic Alembic migration execution to `head`, table creation, strict `127.0.0.1` binding, single-instance PID locking, and signal trapping.
     - `backend/hawkoffer-backend.spec`: Isolated PyInstaller single-file bundling containing FastAPI, Uvicorn, SQLAlchemy, aiosqlite, Alembic (including all migration versions and config), Pydantic, and APScheduler, while excluding development dependencies.
     - `backend/scripts/build_backend.py`: Compiles `backend/dist/hawkoffer-backend.exe` (~24 MB) and deploys to `src-tauri/binaries/hawkoffer-backend-x86_64-pc-windows-msvc.exe`.
  2. Native Tauri Desktop Process Lifecycle Integration:
     - `src-tauri/src/backend_manager.rs`: Dedicated Rust backend supervisor that resolves binary paths, probes health readiness with bounded timeout, spawns backend on startup, and cleanly terminates processes on window close without orphaned processes.
     - `src-tauri/src/main.rs`: Registers backend manager in Tauri managed state and exposes `get_backend_status`, `restart_backend`, and `get_data_directory` invoke handlers.
     - `src-tauri/tauri.conf.json`: Configures `externalBin` sidecar and currentUser NSIS packaging.
     - `src-tauri/capabilities/default.json`: Enforces strict least-privilege capability permissions.
  3. Persistent Data, Upgrade, and Uninstall Safety:
     - Verified `%LOCALAPPDATA%\HawkOffer\HawkOffer\hawkoffer.db` is stored outside the application installation directory.
     - Application upgrades and reinstalls reconnect cleanly to existing SQLite tables, companies, preference profiles, and notification histories without data loss or split databases.
  4. Reproducible Release Build Pipeline:
     - `scripts/build_release.py`: Unified pipeline orchestrating frontend build (`npm run build`), PyInstaller compilation, sidecar deployment, SHA-256 checksum generation, and release manifest generation (`dist/release-manifest.json`).
  5. Automated Verification Test Suites:
     - `backend/tests/test_backend_bundle.py`: Tests compiled standalone binary execution, readiness probe, duplicate process avoidance, and graceful termination.
     - `backend/tests/test_persistence_lifecycle.py`: Tests database survival, reconnection across updates, and idempotent migration execution.
- **Acceptance Criteria**:
  - Standalone executable runs on Windows without requiring a pre-installed Python runtime.
  - All 304 backend tests pass with zero failures.
  - All 33 frontend Vitest tests pass cleanly.
  - Static type checks (`mypy` and `tsc --noEmit`) and linting (`ruff`) pass with zero violations.
  - Distribution artifacts and release manifest are generated deterministically in `dist/`.

---

### Milestone 9.1: Native Installer Verification & Release Sign-Off (COMPLETED)
- **Status**: Completed / Ready for Review
- **Focus**: Verify native desktop packaging prerequisites, enforce process ownership safety across Tauri and backend runtime supervisor, and conduct exhaustive verification gates and release audit.
- **Tasks**:
  1. Process Ownership Safety & Multi-Instance Protection:
     - Fixed race condition in `src-tauri/src/backend_manager.rs` and `backend/app/desktop_runtime.py` where closing Tauri would kill an existing backend daemon started externally or delete its PID file.
     - Added explicit `owns_process` tracking to `BackendStatus` and `BackendManager`: when Tauri connects to an existing healthy backend, `owns_process` is set to `false`, and `stop_backend()` cleanly preserves the external daemon and its PID file.
     - Enhanced `DesktopRuntimeManager.stop(only_if_owned=True)` and `status()` diagnostics with `owns_process`.
     - Added test coverage in `backend/tests/test_desktop_runtime.py` verifying external daemon preservation and owned daemon termination.
  2. Native Toolchain Discovery & Status:
     - Installed official Rust 1.99.1 & Cargo 1.99.1 toolchain via Rustup.
     - Audited C++ linker requirements: compilation of native MSVC binaries requires Microsoft Visual C++ Build Tools (`Microsoft.VisualStudio.2022.BuildTools` with "Desktop development with C++" workload). Documented exact linker diagnostics and CI/CD native build requirements.
  3. Quality Gates & Release Verification:
     - 306 backend Python tests passing (100% pass rate, including standalone bundle and persistence lifecycle).
     - 33 frontend Vitest tests passing across all 9 test suites.
     - Mypy static type checking passing with 0 errors across all 88 source files.
     - Ruff linting passing with 0 violations.
     - Vite frontend production build compiling cleanly.
     - Standalone binary `backend/dist/hawkoffer-backend.exe` verified operational without Python installed.
- **Acceptance Criteria**:
  - Process ownership safety verified via automated tests.
  - All quality gates pass cleanly with zero warnings/errors.
  - Accurate documentation of toolchain prerequisites and release engineering status.

---

### Milestone 9.2: Windows Native Build & Installer Acceptance (AUDITED & BLOCKED ON UAC)
- **Status**: Completed audit; MSVC installation completed by user on D: drive
- **Focus**: Documented MSVC build toolchain provisioning, UAC elevation diagnostic, and environment prerequisites.

---

### Milestone 9.3: Native Tauri Build & Installer Acceptance (COMPLETED & ACCEPTED)
- **Status**: Completed / Ready for Review
- **Focus**: Native MSVC environment initialization, Tauri release build, NSIS installer compilation, and full real installed-application acceptance testing.
- **Tasks**:
  1. MSVC Toolchain & Build Environment Integration:
     - Verified Microsoft Visual Studio 2022 Build Tools v17.14.44 (`link.exe` v14.44.35229.0) and Windows 10/11 SDK 10.0.26100.0.
     - Initialized environment dynamically via `vcvars64.bat` without modifying global system PATH permanently.
     - Added `src-tauri/build.rs` invoking `tauri_build::build()`.
     - Generated platform icons in `src-tauri/icons/` (`icon.ico`, `icon.icns`, `32x32.png`, `128x128.png`, etc.).
  2. Native Tauri Release Compilation & NSIS Packaging:
     - Successfully built native binary `src-tauri/target/release/hawkoffer.exe` (9.28 MB) in release mode.
     - Built native NSIS installer `src-tauri/target/release/bundle/nsis/HawkOffer_0.1.0_x64-setup.exe` (25.96 MB / 27,216,927 bytes).
     - Computed SHA-256: `3fb5bbc5893c9706c03e744cabd38fcf70a4a5514ba3571fe764a358bb8b9201`.
     - Integrated into unified release build pipeline `scripts/build_release.py` and `dist/release-manifest.json`.
  3. Real Installed-Application Acceptance Verification:
     - Executed installer silently (`HawkOffer_0.1.0_x64-setup.exe /S`), confirming installation to `%LOCALAPPDATA%\HawkOffer`.
     - Verified installed files: `hawkoffer.exe`, `hawkoffer-backend.exe`, and `uninstall.exe`.
     - Launched installed desktop app, verified bundled backend startup on `127.0.0.1:8765`, and validated health check (`database: connected`, `status: healthy`, `scheduler: running`).
     - Created monitored company and preference profile via REST API, and confirmed persistence in SQLite database at `%LOCALAPPDATA%\HawkOffer\HawkOffer\hawkoffer.db`.
     - Closed desktop app, verified clean backend shutdown, port release, and lockfile cleanup.
     - Relaunched application, confirming data survived intact across restarts.
     - Verified process ownership safety: closing Tauri when attached to an external daemon preserves the daemon.
  4. Quality Gates:
     - All 306 backend unit, integration, bundle, and runtime tests pass (100%).
     - All 33 frontend Vitest tests pass cleanly.
     - All 9 installed-application lifecycle acceptance tests pass.
     - Static analysis (`mypy` across 88 source files and `tsc --noEmit`) and linting (`ruff`) pass with 0 errors.
- **Acceptance Criteria**:
  - Native Windows installer built and verified.
  - Real installation, launch, REST operations, persistence, and shutdown verified.
  - All quality gates passing.

---

### Milestone 10: AI-Powered Job & Resume Matching (Phase 2)
- **Focus**: Semantic job scoring, resume parsing, and personalized match recommendations.
- **Tasks**:
  1. Implement `ResumeParser` to extract skills, experience level, and keywords from local PDF/DOCX resumes.
  2. Implement local embedding generation (e.g., via `fastembed` or local Ollama instance).
  3. Calculate cosine similarity between job descriptions and resume profile.
  4. Display AI match score (0–100%) and key qualifications highlights in the desktop UI.
  5. Maintain zero cloud dependency (100% local inference).
- **Acceptance Criteria**:
  - Jobs are ranked by semantic relevance to the founder's resume.
  - All processing runs locally with zero external API fees or data leakage.
- **Acceptance Criteria**:
  - Jobs are ranked by semantic relevance to the founder's resume.
  - All processing runs locally with zero external API fees or data leakage.

---

## Engineering Guidelines & Rules

1. **Step-by-Step Approval**: Do not advance to subsequent milestones without explicit review and approval of the current milestone's deliverables.
2. **Quality Standards**: All code must include type hints (`mypy`), comprehensive docstrings, automated unit tests (`pytest`), and linting compliance (`ruff`).
3. **Defensive Scraping**: Always test adapters with network simulation and handle schema drifts gracefully with fallback alerts.
4. **Security & Secrets**: Never commit credentials, personal tokens, or API keys. Keep all runtime state inside local application data folders.
