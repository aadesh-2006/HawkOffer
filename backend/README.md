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

## 🗄️ Database Migrations with Alembic

To generate a new migration after model changes:
```bash
uv run alembic revision --autogenerate -m "describe_change"
```

To apply migrations:
```bash
uv run alembic upgrade head
```
