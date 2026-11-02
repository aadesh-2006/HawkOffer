# HawkOffer Backend Daemon

The core backend daemon and persistence service for **HawkOffer**, an open-source, local-first career monitoring application.

---

## 🏗️ Architecture & Modules

The backend is built with Python 3.11+, FastAPI, SQLAlchemy 2.x, and SQLite (WAL mode).

```
backend/
├── app/
│   ├── core/           # Configuration, settings & logging (cross-platform path resolution)
│   ├── database/       # SQLite connection manager, sessionmaker & Base metadata
│   ├── models/         # SQLAlchemy ORM models (Company, Job, Preference, Notification, Log)
│   ├── schemas/        # Pydantic v2 validation models and DTOs
│   ├── repositories/   # Asynchronous data access repositories (CRUD, indexed lookups)
│   └── api/            # Local REST endpoints (FastAPI)
├── alembic/            # Database schema migration environment
├── tests/              # Pytest test suite covering models, constraints, and repos
├── pyproject.toml      # Project manifest and uv/pip configuration
└── .env.example        # Environment variable template
```

---

## 🚀 Getting Started with `uv`

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) fast Python package manager

### 1. Create Virtual Environment & Install Dependencies

From the `backend/` directory:

```bash
# Create virtual environment with uv
uv venv

# Install project and development dependencies
uv pip install -e ".[dev]"
```

Alternatively, if running via `py`:
```bash
py -3.13 -m uv venv
py -3.13 -m uv pip install -e ".[dev]"
```

### 2. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

By default, the application data directory is resolved automatically in a cross-platform manner:
- **Windows**: `%LOCALAPPDATA%\HawkOffer\hawkoffer.db`
- **macOS**: `~/Library/Application Support/HawkOffer/hawkoffer.db`
- **Linux**: `~/.local/share/HawkOffer/hawkoffer.db`

You can customize this by setting `HAWKOFFER_DATA_DIR` or `HAWKOFFER_DATABASE_URL` in `.env`.

---

## 🧪 Running Tests

To run the full automated test suite with coverage:

```bash
uv run pytest
```

Or using the virtualenv directly:
```bash
.venv\Scripts\pytest
```

---

## 🌐 Running the FastAPI REST Daemon

To start the local REST daemon serving the Tauri desktop application:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Or using the virtualenv directly:

```bash
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Once running:
- **Interactive OpenAPI UI**: [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs)
- **ReDoc Documentation**: [http://127.0.0.1:8765/redoc](http://127.0.0.1:8765/redoc)
- **Health Check**: [http://127.0.0.1:8765/api/v1/health](http://127.0.0.1:8765/api/v1/health)

### API v1 Surface Overview

| Resource | Method & Path | Description |
| :--- | :--- | :--- |
| **System** | `GET /api/v1/health` | Subsystem connectivity and scheduler health |
| | `GET /api/v1/system/status` | Metrics, entity counts, and worker job status |
| **Companies** | `GET /api/v1/companies` | Paginated company targets list |
| | `POST /api/v1/companies` | Register a new career portal target |
| | `GET /api/v1/companies/{id}` | Single company details |
| | `PATCH /api/v1/companies/{id}` | Update company settings |
| | `POST /api/v1/companies/{id}/activate` | Enable automated background scanning |
| | `POST /api/v1/companies/{id}/deactivate` | Disable automated background scanning |
| | `POST /api/v1/companies/{id}/trigger` | Immediate on-demand scrape and ingestion |
| | `GET /api/v1/companies/{id}/scan-state` | Persistent scheduler state and error log |
| | `DELETE /api/v1/companies/{id}` | Delete company and clean up schedules |
| **Profiles** | `GET /api/v1/profiles` | List user preference profiles |
| | `POST /api/v1/profiles` | Create preference profile |
| | `GET /api/v1/profiles/{id}` | Single profile details |
| | `PATCH /api/v1/profiles/{id}` | Update profile (increments revision, enqueues reeval) |
| | `POST /api/v1/profiles/{id}/activate` | Activate preference profile |
| | `POST /api/v1/profiles/{id}/deactivate` | Deactivate preference profile |
| | `DELETE /api/v1/profiles/{id}` | Delete preference profile |
| **Jobs** | `GET /api/v1/jobs` | Paginated listings with search & dynamic filters |
| | `GET /api/v1/jobs/{id}` | Full job details |
| **Notifications** | `GET /api/v1/notifications` | Paginated user alerts |
| | `GET /api/v1/notifications/unread-count` | Real-time badge counter |
| | `POST /api/v1/notifications/{id}/read` | Mark alert as READ |
| | `POST /api/v1/notifications/{id}/unread` | Mark alert as UNREAD |
| | `POST /api/v1/notifications/{id}/dismiss` | Dismiss alert |
| | `POST /api/v1/notifications/{id}/restore` | Restore dismissed alert |
| | `POST /api/v1/notifications/batch/read` | Batch mark as READ |
| | `POST /api/v1/notifications/batch/dismiss` | Batch dismiss alerts |
| **Monitoring** | `GET /api/v1/monitoring/status` | Detailed scheduler & pipeline status |
| | `POST /api/v1/monitoring/discovery/trigger` | Trigger JobDiscoveryWorker outbox sweep |
| | `POST /api/v1/monitoring/reevaluation/trigger` | Trigger ProfileReevaluationWorker |
| | `POST /api/v1/monitoring/drain/trigger` | Trigger BacklogDrainWorker promotion |
| | `POST /api/v1/monitoring/pause` | Pause worker job or company scan |
| | `POST /api/v1/monitoring/resume` | Resume worker job or company scan |

---

## 🗄️ Database Migrations with Alembic

To generate a new migration after model changes:
```bash
uv run alembic revision --autogenerate -m "describe_change"
```

To apply migrations:
```bash
uv run alembic upgrade head
```
