"""Application configuration module for HawkOffer."""

from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_INSECURE_CRON_SECRETS: frozenset[str] = frozenset({
    "hawkoffer-dev-cron-secret-token",
    "hawkoffer-dev-secret-key-change-in-production-min-32-chars",
    "changeme",
    "secret",
    "password",
    "admin",
    "12345678",
    "cron-secret",
})


class Settings(BaseSettings):
    """HawkOffer application settings."""

    model_config = SettingsConfigDict(
        env_prefix="HAWKOFFER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "HawkOffer"
    env: str = "development"
    log_level: str = "INFO"

    # API configuration
    api_host: str = "127.0.0.1"
    api_port: int = 8765

    # Storage and directory configuration (Cross-platform)
    data_dir: Path | None = None
    database_url: str | None = None
    db_echo: bool = False

    # Database connection pool settings (for PostgreSQL)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 300
    db_pool_pre_ping: bool = True

    # Security and Authentication settings
    jwt_secret_key: str = "hawkoffer-dev-secret-key-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    auth_required: bool = False
    cron_secret: str | None = None

    # M15 & M15.1 Multi-Tenant Centralized Monitoring
    monitor_curated_without_subscribers: bool = False
    monitoring_lease_ttl_seconds: int = 300

    # M16 Web Push Notifications (RFC 8291 / RFC 8292) & Background Dispatch
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_claims_email: str = "mailto:support@hawkoffer.dev"
    push_dispatch_batch_size: int = 50
    push_dispatch_interval_seconds: int = 30
    push_lease_ttl_seconds: int = 120
    push_max_attempts: int = 3

    # M5 Background Pipeline Scheduling
    discovery_interval_seconds: int = 30
    reevaluation_interval_seconds: int = 60
    drain_interval_seconds: int = 15

    discovery_batch_size: int = 50
    reevaluation_chunk_size: int = 50
    drain_batch_size: int = 50

    scheduler_misfire_grace_time_seconds: int = 60
    scheduler_max_instances: int = 1

    def get_data_dir(self, create: bool = False) -> Path:
        """Resolve the cross-platform application data directory."""
        if self.data_dir:
            path = Path(self.data_dir).expanduser().resolve()
        else:
            # Resolves on Windows (%LOCALAPPDATA%), macOS, and Linux (~/.local/share)
            path = Path(user_data_dir(appname=self.app_name, appauthor=self.app_name))

        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_data_dir(self) -> Path:
        """Explicitly create and return the application data directory."""
        return self.get_data_dir(create=True)

    def get_database_url(self) -> str:
        """Resolve the async database URL (SQLite or PostgreSQL)."""
        if self.database_url:
            url = self.database_url.strip()
            # Normalize PostgreSQL prefixes for asyncpg
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgresql://") and "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url

        db_path = self.get_data_dir(create=False) / "hawkoffer.db"
        # Always use forward slashes for SQLite URLs across all OSes
        clean_path = db_path.as_posix()
        return f"sqlite+aiosqlite:///{clean_path}"

    def get_sync_database_url(self) -> str:
        """Resolve a synchronous database URL for Alembic migrations."""
        async_url = self.get_database_url()
        if async_url.startswith("sqlite+aiosqlite:///"):
            return async_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        if async_url.startswith("postgresql+asyncpg://"):
            return async_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if async_url.startswith("postgres://"):
            return async_url.replace("postgres://", "postgresql://", 1)
        return async_url

    def model_post_init(self, __context: object) -> None:
        super().model_post_init(__context)
        # In development and test environments, provide a fallback secret if none configured
        # so local development and non-production tests work without friction.
        # In production, leave it as None if not explicitly provided so it fails closed.
        if self.cron_secret is None and not self.is_production:
            self.cron_secret = "hawkoffer-dev-cron-secret-token"

    @property
    def is_production(self) -> bool:
        """Return True if the application is running in production environment."""
        return self.env.strip().lower() in ("production", "prod")

    @property
    def is_saas_mode(self) -> bool:
        """Return True if running as a multi-tenant SaaS deployment."""
        return self.auth_required or self.is_production

    def validate_production_cron_secret(self) -> tuple[bool, str]:
        """Validate whether the configured cron secret meets production security standards.

        Returns:
            Tuple of (is_valid, reason).
        """
        if not self.cron_secret:
            return False, "HAWKOFFER_CRON_SECRET is not configured"
        secret = self.cron_secret.strip()
        if secret.lower() in KNOWN_INSECURE_CRON_SECRETS:
            return False, "HAWKOFFER_CRON_SECRET uses a known insecure development default"
        if len(secret) < 32:
            return False, "HAWKOFFER_CRON_SECRET must be at least 32 characters in production"
        return True, "Valid"


# Global settings singleton
settings = Settings()
