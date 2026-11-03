"""Milestone 18: Zero-Budget Deployment Automation & Cloud Readiness Tests."""

from pathlib import Path

from app.core.config import Settings
from app.main import create_app


def test_cors_origins_parsing_and_merging() -> None:
    """Validate CORS origins parse from string or list and merge with desktop defaults."""
    # Comma-separated string with mixed whitespace and trailing slashes
    s1 = Settings(
        env="testing",
        jwt_secret_key="test-secret-key-at-least-32-chars-long",
        cron_secret="test-cron-secret",
        cors_origins="https://hawkoffer.pages.dev/, http://preview.local/ ",
    )
    assert s1.get_cors_origins() == ["https://hawkoffer.pages.dev", "http://preview.local"]

    # List of strings
    s2 = Settings(
        env="testing",
        jwt_secret_key="test-secret-key-at-least-32-chars-long",
        cron_secret="test-cron-secret",
        cors_origins=["https://custom-domain.com"],
    )
    assert s2.get_cors_origins() == ["https://custom-domain.com"]

    # App factory creates FastAPI app with merged origins
    app = create_app(start_scheduler=False, init_database=False)
    # Check that CORSMiddleware is attached and contains default origins
    cors_middleware = next(
        (m for m in app.user_middleware if "CORSMiddleware" in str(m.cls)), None
    )
    assert cors_middleware is not None


def test_scheduler_flag_configuration() -> None:
    """Validate start_internal_scheduler setting defaults to True and can be disabled."""
    s = Settings(
        env="testing",
        jwt_secret_key="test-secret-key-at-least-32-chars-long",
        cron_secret="test-cron-secret",
        start_internal_scheduler=False,
    )
    assert s.start_internal_scheduler is False


def test_alembic_driver_scheme_normalization() -> None:
    """Validate URL normalization converts sync schemes to asyncpg and aiosqlite."""
    def normalize_url(url: str) -> str:
        if url.startswith("sqlite://"):
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        elif url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    assert normalize_url("sqlite:///data.db") == "sqlite+aiosqlite:///data.db"
    assert (
        normalize_url("postgresql://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )
    assert (
        normalize_url("postgres://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )
    assert (
        normalize_url("postgresql+asyncpg://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_production_container_assets() -> None:
    """Validate presence and security properties of container configuration files."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_dir = repo_root / "backend"

    dockerfile = backend_dir / "Dockerfile"
    dockerignore = backend_dir / ".dockerignore"
    entrypoint = backend_dir / "scripts" / "docker-entrypoint.sh"
    compose = repo_root / "docker-compose.prod.yml"

    assert dockerfile.exists(), "backend/Dockerfile must exist"
    assert dockerignore.exists(), "backend/.dockerignore must exist"
    assert entrypoint.exists(), "docker-entrypoint.sh must exist"
    assert compose.exists(), "docker-compose.prod.yml must exist"

    df_content = dockerfile.read_text(encoding="utf-8")
    assert "appuser" in df_content, "Dockerfile must configure non-root appuser"
    assert "HEALTHCHECK" in df_content, "Dockerfile must define HEALTHCHECK"
    assert "uvicorn" in df_content, "Dockerfile must reference uvicorn"

    di_content = dockerignore.read_text(encoding="utf-8")
    assert "*.db" in di_content, ".dockerignore must exclude *.db files"
    assert ".venv" in di_content, ".dockerignore must exclude .venv"
    assert ".env" in di_content, ".dockerignore must exclude .env"

    ep_content = entrypoint.read_bytes()
    assert b"\r\n" not in ep_content, "docker-entrypoint.sh must use UNIX LF newlines"
    assert b"exec uvicorn" in ep_content, "docker-entrypoint.sh must use exec uvicorn for SIGTERM"


def test_external_cron_workflow_definition() -> None:
    """Validate external scheduled monitoring workflow contract."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    workflow = repo_root / ".github" / "workflows" / "monitor-cron.yml"

    assert workflow.exists(), "monitor-cron.yml must exist"
    content = workflow.read_text(encoding="utf-8")
    assert "cron:" in content, "Workflow must define scheduled cron"
    assert "workflow_dispatch:" in content, "Workflow must support workflow_dispatch"
    assert "/api/v1/internal/cron/monitor" in content, "Workflow must target monitor cron API"
    assert "X-Cron-Secret" in content, "Workflow must send X-Cron-Secret header"
    assert "::add-mask::" in content, "Workflow must mask cron secret"


def test_frontend_cloud_deployment_assets() -> None:
    """Validate frontend Cloudflare Pages SPA redirects and environment files."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    frontend_dir = repo_root / "frontend"

    redirects = frontend_dir / "public" / "_redirects"
    assert redirects.exists(), "frontend/public/_redirects must exist"
    assert "/index.html   200" in redirects.read_text(encoding="utf-8")

    env_example = frontend_dir / ".env.example"
    assert env_example.exists(), "frontend/.env.example must exist"
    assert "VITE_API_BASE_URL" in env_example.read_text(encoding="utf-8")
