# HAWKOFFER — MILESTONE 17: PUBLIC SAAS WEB DASHBOARD & APPLICATION TRACKING

**Document Version:** 1.0.0  
**Date:** November 2026  
**Status:** COMPLETED & VERIFIED  
**Baseline Commit:** `5bd119b`  
**Milestone Scope:** Website-first SaaS interface evolution, multi-user authentication, company catalog discovery & subscription, recruitment Kanban application tracking, and RFC 8291 Web Push onboarding.

---

## 1. Executive Summary

Milestone 17 evolves HawkOffer from a desktop/local job scraper into a modern, public, multi-user SaaS web dashboard. Building upon the foundational backend milestones (M13 data foundation, M14 multi-tenant authentication, M15 centralized monitoring, and M16 Web Push notifications), M17 delivers the complete user-facing SaaS experience:

- **Universal Multi-User Authentication**: JWT-based authentication flow with automated session restoration, transparent 401 token refresh loop, and contextual authentication prompts.
- **Career Intelligence Dashboard**: Modern layout displaying KPI statistics (Curated/Followed Companies, Discovered Jobs, Active Applications, Unread Alerts), mini-Kanban pipeline overview, background worker health, and contextual Web Push onboarding.
- **Company Discovery & Subscription Directory**: Interactive company management with catalog filtering (Curated vs. Followed vs. All Targets), one-click follow/unfollow toggle, and custom company registration.
- **Job Application Tracker**: Comprehensive 5-stage recruitment pipeline (`SAVED`, `APPLIED`, `INTERVIEWING`, `OFFER`, `REJECTED`) supported in both interactive Kanban board and dense tabular list views.
- **Web Push Notification Onboarding**: Non-intrusive prompt strictly conforming to RFC 8291/8292 standards with zero-pressure dismissal and live status management in the Settings view.
- **Zero-Paid-API Philosophy & Desktop Parity**: Built with zero external paid dependencies, preserving 100% backward compatibility with Tauri desktop mode and SQLite/PostgreSQL dual-dialect execution.

---

## 2. Architecture & Design Implementation

### 2.1 Authentication & Session Management (`AuthContext` & `AuthModal`)
- **Token Management**: Stores access and refresh tokens in `localStorage` under `hawkoffer_access_token` and `hawkoffer_refresh_token`.
- **Automatic Session Restoration**: On app initialization, `AuthProvider` validates active tokens against `GET /api/v1/auth/me`. If invalid or expired, transparently attempts token refresh or clears session gracefully.
- **401 Interceptor with Auto-Refresh**: In `frontend/src/services/api.ts`, 401 responses trigger a background refresh call to `POST /api/v1/auth/refresh`. If successful, the original request is replayed with the new token.
- **AuthModal Component**: Supports fluid switching between Sign In and Account Creation with validation, password visibility toggle, and error alerts.

### 2.2 Career Intelligence Dashboard (`DashboardScreen`)
- **System & Metric Overview**: Fetches system status, followed company counts, active application pipeline metrics, and unread notifications concurrently via `Promise.allSettled`.
- **Pipeline Metric Cards**: Direct click-through navigation to relevant sections.
- **Mini-Kanban Snapshot**: Displays real-time breakdown of current job applications across stages.
- **Worker Telemetry & Quick Triggers**: Provides one-click triggers for Job Discovery and Backlog Drain workers directly from the dashboard.

### 2.3 Company Directory & Following (`CompaniesScreen`)
- **Catalog Categorization**:
  1. *Curated Catalog*: Pre-configured production job portals (e.g. Stripe, Datadog).
  2. *Followed Companies*: User's personal subscriptions fetched from `GET /api/v1/subscriptions`.
  3. *All Targets*: Complete company registry.
- **Subscription Action**: Direct follow/unfollow buttons linked to `POST /api/v1/subscriptions` and `DELETE /api/v1/subscriptions/{id}`.
- **Add Target Modal**: Enables users to submit custom Greenhouse/Lever career portals, immediately auto-subscribing the creating user.

### 2.4 Job Discovery Feed & Explainability (`JobsFeedScreen` & `JobDetailModal`)
- **Multi-Tab Feed**: Filter by "All Discoveries", "Top Matches" (semantic score threshold), and "Tracked in Pipeline".
- **One-Click Tracking**: Bookmark button on job cards creates or updates application records in real-time.
- **Enriched Schema**: Updated `ApplicationRead` schema in `backend/app/schemas/application.py` to embed `job: JobRead | None = None`, eliminating N+1 queries.
- **Explainability**: Full breakdown of hard filters, keyword overlaps, and semantic matching scores.

### 2.5 Recruitment Pipeline Tracker (`ApplicationsScreen`)
- **5 Kanban Stages**:
  - `SAVED`: Jobs bookmarked for later review.
  - `APPLIED`: Submitted applications.
  - `INTERVIEWING`: Active interview processes.
  - `OFFER`: Received offers.
  - `REJECTED`: Archived or declined applications.
- **View Modes**: Fluid toggle between multi-column Kanban board and dense sortable tabular list.
- **Quick Status Selector**: Direct dropdown on each card allowing rapid stage promotion.
- **Modal Management**: Add custom or direct applications, edit notes, and track application dates.

### 2.6 Browser Web Push Onboarding (`PushNotificationBanner`)
- **Non-Intrusive Display**: Renders only when browser supports Web Push, permission is `default`, and user has not dismissed the banner.
- **One-Click Enable**: Automatically calls `navigator.serviceWorker.register()`, requests permission, generates browser push subscription via VAPID key, and synchronizes to `POST /api/v1/push/subscribe`.
- **Durable Dismissal**: Stored in `localStorage` under `hawkoffer_push_banner_dismissed`.

---

## 3. Verification & Quality Assurance Matrix

| Layer / Test Suite | Scope | Result | Details |
|---|---|---|---|
| **Frontend Unit & Component Tests** | Vitest (12 test suites, 42 tests) | **PASSED (100%)** | 8 new tests added covering `auth.test.tsx`, `applications.test.tsx`, and `push_banner.test.tsx`. |
| **Frontend Production Build** | `tsc && vite build` | **PASSED** | Compiled in 4.16s. Bundle size: 89.74 kB gzipped JS, 7.85 kB gzipped CSS (well below 500 kB threshold). |
| **Backend Unit & Integration Tests** | Pytest (402 tests) | **PASSED (100%)** | Full test suite passed in 2m 15s across dual SQLite/PostgreSQL dialects, multi-tenancy, and Web Push. |
| **Python Code Quality & Linting** | Ruff (`ruff check app tests`) | **PASSED** | 0 lint or formatting errors across all backend source files. |
| **Python Static Type Checking** | MyPy (`mypy app tests`) | **PASSED** | Strict typing verified across 127 Python source files. |
| **Desktop / Tauri Backward Compatibility** | Desktop sidecar & runtime | **PRESERVED** | Desktop mode continues to function seamlessly with fallback in-memory auth and SQLite database. |

---

## 4. Milestone 17 Sign-Off & Next Steps

All Milestone 17 objectives are fully achieved and verified. HawkOffer now presents a complete, modern, website-first SaaS frontend with robust multi-user authentication, application pipeline tracking, company subscriptions, and browser push onboarding.

**Next Milestone:** **Milestone 18: Zero-Budget Deployment Automation & Cloud Readiness** (Cloudflare Pages frontend deployment, containerized backend on free cloud tier, external cron trigger via GitHub Actions / cron-job.org).
