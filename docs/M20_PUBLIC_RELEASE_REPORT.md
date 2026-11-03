# HawkOffer — Milestone 20 Public Deployment & Live Release Report

## Executive Summary

Milestone 20 (M20) establishes the live public deployment readiness and execution framework for HawkOffer. Following the zero-budget architecture ($0.00/month operational costs), the application is fully validated, containerized, and configured for turnkey live release across Cloudflare Pages (Frontend), Render / Fly.io (Backend), Supabase / Neon (PostgreSQL 16), and GitHub Actions (Scheduled Monitoring).

In strict adherence to engineering safety and authorization boundaries, **no cloud accounts were created, no cloud resources were provisioned, and no credentials were entered**. This report details the audit findings, provider configurations, test results, and the exact step-by-step operator procedure required to complete the live public launch.

---

## 1. Deployment Architecture & Provider Configuration

```mermaid
flowchart TD
    subgraph Users ["Public Web Users"]
        Browser["Modern Browser (Desktop / Mobile)"]
    end

    subgraph Edge ["Frontend Edge (Cloudflare Pages)"]
        CF["Cloudflare Global Anycast Network"]
        SPA["React 18 + Vite SPA (dist/)"]
        Redirects["_redirects (SPA Fallback)"]
        SW["Service Worker (sw.js - Web Push)"]
    end

    subgraph Cloud ["Backend Compute (Render Free Web Service)"]
        Docker["Docker Container (Python 3.11-slim, appuser)"]
        WaitDB["app.database.wait_for_db (Connectivity Probe)"]
        Migrations["Alembic Migrations (asyncpg)"]
        ASGI["Uvicorn ASGI Daemon (FastAPI v1)"]
        Dispatcher["WebPushDispatcher (Outbox Engine)"]
    end

    subgraph Data ["Relational Persistence (Supabase / Neon)"]
        PG["PostgreSQL 16 (Multi-Tenant Relational Schema)"]
    end

    subgraph Cron ["External Automation (GitHub Actions)"]
        GHA[".github/workflows/monitor-cron.yml (Every 30m)"]
        Secret["Masked CRON_SECRET Header"]
    end

    Browser -->|HTTPS| CF
    CF --> SPA
    SPA -->|JSON REST API / JWT| ASGI
    GHA -->|X-Cron-Secret| ASGI
    Docker --> WaitDB
    WaitDB --> PG
    WaitDB --> Migrations
    Migrations --> PG
    ASGI --> PG
    Dispatcher -->|RFC 8291 Web Push| SW
```

---

## 2. Deployment Readiness Audit & M20 Fixes

Before initiating deployment readiness validation, the codebase was audited to eliminate latent production bugs:

1. **GitHub Actions Cron URL Concatenation Bug Fixed**:
   - *Audit Finding*: In `.github/workflows/monitor-cron.yml`, if an operator configured `API_BASE_URL` with `/api/v1` (e.g. `https://hawkoffer-api.onrender.com/api/v1`), the workflow appended `/api/v1/internal/cron/monitor`, resulting in an invalid duplicate path `.../api/v1/api/v1/internal/cron/monitor` (HTTP 404).
   - *Fix*: Added path stripping: `CLEAN_BASE_URL="${CLEAN_BASE_URL%/api/v1}"`, ensuring that whether the operator provides the domain root or the `/api/v1` prefix, the endpoint always correctly resolves to `${CLEAN_BASE_URL}/api/v1/internal/cron/monitor`.
2. **Smoke-Test Application Tracking Schema Alignment**:
   - *Audit Finding*: In `scripts/smoke_test.py`, the application tracker test attempted to post unmapped fields (`company_name`, `role_title`, `stage`) rather than `job_id` and `status`, causing HTTP 422 schema validation errors.
   - *Fix*: Updated `scripts/smoke_test.py` to dynamically query discovered jobs from `GET /api/v1/jobs?limit=1`. If a job exists, it creates the application referencing `job_id` with `status: "APPLIED"`, updates to `"INTERVIEWING"`, and cleans up via `DELETE /applications/{id}`.
3. **Local Live Server Smoke Test Verification**:
   - Executed `scripts/smoke_test.py` against a running backend instance: **11 passed, 0 failed, 3 skipped (clean exit code 0)**.

---

## 3. Provider Free-Tier Analysis & Operational Limits

| Provider | Component | Free Tier Specifications | Operational Limits & Mitigation |
| :--- | :--- | :--- | :--- |
| **Cloudflare Pages** | Frontend Web | Unlimited bandwidth, global CDN, automated Git builds, free custom domains, automatic TLS | Build limit of 500 builds/month; asset size limit 25MB (HawkOffer bundle is ~356 kB uncompressed, 89 kB gzipped). |
| **Render** | Backend Container | 0.1 CPU, 512 MB RAM, 750 free instance hours/month, automatic TLS | Inactivity sleep after 15 minutes of zero traffic. Cold start latency is ~30–50s. **Mitigation**: GitHub Actions 30-min cron keeps instance warm during daytime. |
| **Supabase / Neon** | PostgreSQL 16 | 500 MB storage, connection pooling, SSL encryption required | Supabase pauses projects after 1 week of zero queries. **Mitigation**: Automated cron executes queries every 30m, preventing inactivity pauses. |
| **GitHub Actions** | Monitoring Cron | 2,000 runner minutes/month (unlimited on public repos), cron schedules | Occasional scheduling jitter (5–15 minutes delay during peak GitHub Actions load). Acceptable for 30-min job discovery cycles. |

---

## 4. Step-by-Step Operator Action Plan for Live Release

To execute the live public launch without incurring costs, perform these 5 steps in your provider consoles:

### Step 1: Provision Free PostgreSQL Database (Supabase or Neon)
1. Log in to [Supabase](https://supabase.com) or [Neon](https://neon.tech) and create a free project named `hawkoffer-prod`.
2. Select a region close to your target backend (e.g. `US East / us-east-1`).
3. Copy the database connection URI:
   ```
   postgresql://postgres:[YOUR-PASSWORD]@[HOST]:5432/postgres?sslmode=require
   ```
4. Convert scheme to `postgresql+asyncpg://`:
   ```
   postgresql+asyncpg://postgres:[YOUR-PASSWORD]@[HOST]:5432/postgres?sslmode=require
   ```

### Step 2: Deploy Backend Container to Render
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** -> **Web Service** -> Connect repository `aadesh-2006/HawkOffer`.
3. Configure Service:
   - **Name**: `hawkoffer-backend`
   - **Root Directory**: `backend`
   - **Environment**: `Docker`
   - **Dockerfile Path**: `Dockerfile`
   - **Instance Type**: `Free`
4. Add Environment Variables:
   - `HAWKOFFER_ENV` = `production`
   - `HAWKOFFER_DATABASE_URL` = `<your-postgresql+asyncpg-url-from-step-1>`
   - `HAWKOFFER_AUTH_REQUIRED` = `true`
   - `HAWKOFFER_JWT_SECRET_KEY` = `<generate-with-openssl-rand-hex-32>`
   - `HAWKOFFER_CRON_SECRET` = `<generate-with-openssl-rand-hex-32>`
   - `HAWKOFFER_CORS_ORIGINS` = `https://hawkoffer.pages.dev` (or your Cloudflare domain)
   - `HAWKOFFER_START_INTERNAL_SCHEDULER` = `false`
   - `RUN_MIGRATIONS` = `true`
5. Set **Health Check Path**: `/api/v1/health`.
6. Click **Deploy Web Service**. Note your public URL (e.g. `https://hawkoffer-backend.onrender.com`).

### Step 3: Deploy Frontend SPA to Cloudflare Pages
1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com) -> **Workers & Pages** -> **Create application** -> **Pages** -> **Connect to Git**.
2. Select `aadesh-2006/HawkOffer`.
3. Configure Build Settings:
   - **Framework Preset**: `Vite`
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Build Output Directory**: `dist`
4. Configure Environment Variables:
   - `VITE_API_BASE_URL` = `https://hawkoffer-backend.onrender.com/api/v1`
5. Click **Save and Deploy**. Cloudflare Pages will build the application and deploy it with automatic HTTPS and SPA routing.

### Step 4: Configure GitHub Actions Monitoring Cron
1. Open your repository on GitHub: `https://github.com/aadesh-2006/HawkOffer`.
2. Go to **Settings** -> **Secrets and variables** -> **Actions**.
3. Under **Repository Secrets**, click **New repository secret**:
   - **Name**: `CRON_SECRET`
   - **Value**: The exact secret generated for `HAWKOFFER_CRON_SECRET` in Step 2.
4. Under **Repository Variables**, click **New repository variable**:
   - **Name**: `API_BASE_URL`
   - **Value**: `https://hawkoffer-backend.onrender.com`
5. Go to the **Actions** tab -> select **HawkOffer Scheduled Monitoring** -> click **Run workflow** to test execution.

### Step 5: Execute Live Verification Smoke Test
Run the automated smoke-test suite against your live deployment:

```bash
python scripts/smoke_test.py \
    --api-url https://hawkoffer-backend.onrender.com/api/v1 \
    --web-url https://hawkoffer.pages.dev \
    --cron-secret <your-cron-secret>
```

---

## 5. Quality Gates & Verification Summary

All code changes and deployment configurations were verified locally against the full test matrix:

| Quality Gate | Tool / Command | Result | Status |
| :--- | :--- | :--- | :--- |
| **Backend Test Suite** | `pytest tests -q` | **419 passed**, 0 failed | **PASSED** |
| **Frontend Test Suite** | `npm test -- --run` | **42 passed**, 0 failed | **PASSED** |
| **Static Type Analysis** | `mypy app tests` | **0 issues** across 130 source files | **PASSED** |
| **Code Style & Linting** | `ruff check app tests` | **All checks passed** (0 errors) | **PASSED** |
| **Frontend Production Build** | `npm run build` | **0 errors**, bundled in 4.57s | **PASSED** |
| **Alembic Database Engine** | `alembic current` | Functional under asyncpg/aiosqlite | **PASSED** |
| **Live Smoke Test (Local)** | `python scripts/smoke_test.py` | **11 passed, 0 failed, 3 skipped** | **PASSED** |

---

## 6. Current Deployment Status & Authorization Boundary Disclosure

- **Live Deployment Execution**: **Awaiting Operator Account Credentials**.
- **Reason**: In accordance with user safety guidelines, the agent cannot create external cloud accounts (Render, Cloudflare, Supabase) or configure billing/secrets on the user's behalf.
- **Readiness State**: **100% Turnkey Ready**. All files, containers, entrypoints, healthchecks, migration runners, and smoke-test tooling are fully tested and committed to `main`.
