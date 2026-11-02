"""Application configuration module for HawkOffer."""

from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        """Resolve the async SQLite database URL."""
        if self.database_url:
            return self.database_url

        db_path = self.get_data_dir(create=False) / "hawkoffer.db"
        # Always use forward slashes for SQLite URLs across all OSes
        clean_path = db_path.as_posix()
        return f"sqlite+aiosqlite:///{clean_path}"

    def get_sync_database_url(self) -> str:
        """Resolve a synchronous database URL for Alembic migrations."""
        async_url = self.get_database_url()
        if async_url.startswith("sqlite+aiosqlite:///"):
            return async_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        return async_url


# Global settings singleton
settings = Settings()
