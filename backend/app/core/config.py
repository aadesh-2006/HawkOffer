"""Application configuration module for HawkOffer."""

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from platformdirs import user_data_dir
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

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

SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset({
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "private_key",
    "key",
})


def redact_db_url(url: str) -> str:
    """Safely redact credentials and sensitive query parameters from a database URL.

    - Isolates authority, path, query, and fragment before checking for userinfo delimiters.
    - Masks username and password as '***:***@<host>' without exposing either.
    - URL-decodes query parameter names before matching against sensitive keys.
    - Redacts sensitive query parameters (password, token, secret, api_key, etc.).
    - Preserves non-sensitive query parameters (e.g. sslmode, application_name) for diagnostics.
    - Redacts URL fragments to prevent token/secret exposure.
    - Never raises an exception on malformed or unexpected input strings.
    """
    if not url:
        return ""
    try:
        url_str = str(url).strip()
        if not url_str:
            return ""

        # 1. Scheme isolation
        if "://" in url_str:
            scheme_prefix, _, remainder = url_str.partition("://")
            scheme = scheme_prefix + "://"
        else:
            scheme = ""
            remainder = url_str

        # 2. Fragment isolation: mask fragment content to prevent token leakage
        fragment = ""
        if "#" in remainder:
            remainder, _, frag_content = remainder.partition("#")
            fragment = "#***" if frag_content else "#"

        # 3. Query string isolation and sensitive parameter redaction
        query_str = ""
        if "?" in remainder:
            remainder, _, query_content = remainder.partition("?")
            if query_content:
                redacted_params = []
                for param in query_content.split("&"):
                    if not param:
                        continue
                    if "=" in param:
                        k, v = param.split("=", 1)
                        k_norm = unquote(k).lower().replace("-", "_").strip()
                        is_sensitive = (
                            k_norm in SENSITIVE_QUERY_KEYS
                            or any(
                                s in k_norm
                                for s in (
                                    "password",
                                    "token",
                                    "secret",
                                    "api_key",
                                    "access_token",
                                    "refresh_token",
                                )
                            )
                        )
                        if is_sensitive:
                            redacted_params.append(f"{k}=***")
                        else:
                            redacted_params.append(f"{k}={v}")
                    else:
                        k_norm = unquote(param).lower().replace("-", "_").strip()
                        is_sensitive = (
                            k_norm in SENSITIVE_QUERY_KEYS
                            or any(
                                s in k_norm
                                for s in (
                                    "password",
                                    "token",
                                    "secret",
                                    "api_key",
                                    "access_token",
                                    "refresh_token",
                                )
                            )
                        )
                        if is_sensitive:
                            redacted_params.append(f"{param}=***")
                        else:
                            redacted_params.append(param)
                query_str = "?" + "&".join(redacted_params)
            else:
                query_str = "?"

        # 4. Path and Authority isolation: delimited by the first occurrence of '/'
        if "/" in remainder:
            authority, _, path = remainder.partition("/")
            path_str = "/" + path
        else:
            authority = remainder
            path_str = ""

        # 5. User-info redaction strictly within the isolated authority component
        if "@" in authority:
            _, host_port = authority.rsplit("@", 1)
            authority = f"***:***@{host_port}"

        return f"{scheme}{authority}{path_str}{query_str}{fragment}"
    except Exception:
        return "***:***@<redacted>"


def sanitize_exception_text(exc: object) -> str:
    """Sanitize arbitrary exception text or messages so no credentials or secrets leak.

    - Never conditionally skips redaction based on '@'.
    - Finds and redacts full database URLs embedded in the exception text.
    - Redacts sensitive query-parameter or key-value pairs (password, token, secret, api_key, etc.),
      including URL-encoded variants (e.g. %74oken=secret).
    - Masks any raw user:password@ credentials.
    - Never raises an exception itself; falls back safely to the exception class name.
    """
    if exc is None:
        return ""
    try:
        raw_text = str(exc)
        if not raw_text:
            return ""

        # 1. If text contains a URL (scheme://...), redact the embedded URL(s)
        def _replace_url(match: re.Match[str]) -> str:
            return redact_db_url(match.group(0))

        sanitized = re.sub(r"[a-zA-Z0-9+.-]+://[^\s\'\",;()]+", _replace_url, raw_text)

        # 2. Redact sensitive key-value pairs even if standalone in the message
        def _replace_sensitive_kv(match: re.Match[str]) -> str:
            k = match.group(1)
            sep = match.group(2)
            k_norm = unquote(k).lower().replace("-", "_").strip()
            is_sensitive = (
                k_norm in SENSITIVE_QUERY_KEYS
                or any(
                    s in k_norm
                    for s in (
                        "password",
                        "token",
                        "secret",
                        "api_key",
                        "access_token",
                        "refresh_token",
                    )
                )
            )
            if is_sensitive:
                return f"{k}{sep}***"
            return match.group(0)

        sanitized = re.sub(
            r"([a-zA-Z0-9_%-]+)(=|:\s*)([^\s&\'\",;#]+)",
            _replace_sensitive_kv,
            sanitized,
        )

        # 3. Mask any remaining user:pass@ patterns in the text
        sanitized = re.sub(r"(?:[^\s/@:]+):(?:[^\s/@:]+)@([^\s/@:]+)", r"***:***@\1", sanitized)

        return sanitized
    except Exception:
        try:
            return type(exc).__name__
        except Exception:
            return "DatabaseConfigurationError"


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
    cors_origins: list[str] = []

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

    # M18 Cloud Deployment & Scheduling controls
    start_internal_scheduler: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Parse comma-separated strings or string lists into normalized origin URLs."""
        raw_list: list[str] = []
        if isinstance(v, str):
            raw_list = [o.strip().rstrip("/") for o in v.split(",") if o.strip()]
        elif isinstance(v, list):
            raw_list = [str(o).strip().rstrip("/") for o in v if str(o).strip()]

        cleaned: list[str] = []
        for origin in raw_list:
            if origin == "*":
                raise ValueError(
                    "Wildcard '*' CORS origin is strictly prohibited when credentials are enabled. "
                    "Specify explicit origins (e.g., https://yourdomain.com)."
                )
            cleaned.append(origin)
        return cleaned

    def get_cors_origins(self) -> list[str]:
        """Return list of configured external CORS origins."""
        return list(self.cors_origins)

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
        raw_url = self.database_url or os.environ.get("DATABASE_URL")
        if raw_url:
            cleaned = raw_url.strip()
            # SQLite URLs: maintain raw file/memory paths without encoding colons or slashes
            if cleaned.startswith("sqlite://"):
                return cleaned.replace("sqlite://", "sqlite+aiosqlite://", 1)
            if cleaned.startswith("sqlite+aiosqlite://"):
                return cleaned

            # Restrict credential repair strictly to the authority/user-info section.
            # Assumptions & Guarantees:
            # 1. Authority isolation: Delimited by '://' and the first occurrence of '/',
            #    '?', or '#'. URL query parameters (e.g. application_name=hawk@offer) and
            #    paths are never inspected or altered by credential repair.
            # 2. User-host delimiter: In authority.rsplit("@", 1), the rightmost '@' character
            #    in the authority section is assumed to separate user-info from host:port.
            # 3. User-password delimiter: The first ':' separates username from password.
            # 4. Username integrity: Usernames must not contain unencoded '@' characters.
            #    If an unencoded '@' appears in the username, the authority is ambiguous
            #    and raises ValueError.
            # 5. Password repair: Literal '@' characters inside the password component are
            #    assumed to be unencoded password characters and safely converted to '%40'
            #    without altering the host. Pre-encoded passwords (%40) contain only a single
            #    '@' delimiter, so no rewriting occurs.
            if "://" in cleaned:
                prefix, sep, remainder = cleaned.partition("://")
                # Authority is delimited by the first occurrence of '/', '?', or '#'
                end_delims = [remainder.find(d) for d in ("/", "?", "#") if d in remainder]
                auth_end = min(end_delims) if end_delims else len(remainder)
                authority = remainder[:auth_end]
                suffix = remainder[auth_end:]

                # Only repair if the authority component itself has multiple '@' signs
                if authority.count("@") > 1:
                    userinfo, host_port = authority.rsplit("@", 1)
                    if ":" not in userinfo:
                        raise ValueError(
                            "Ambiguous database credentials in authority: multiple '@' characters "
                            "found without a valid 'user:password' separator. "
                            "Please URL-encode credentials."
                        )
                    user, password = userinfo.split(":", 1)
                    if "@" in user:
                        raise ValueError(
                            "Ambiguous database credentials: username contains an unencoded '@' "
                            "character. Please URL-encode credentials "
                            "(e.g. replace '@' with '%40')."
                        )
                    password = password.replace("@", "%40")
                    authority = f"{user}:{password}@{host_port}"
                    cleaned = f"{prefix}://{authority}{suffix}"

            # PostgreSQL URL handling: parse and normalize via SQLAlchemy make_url.
            # Supported schemes: postgres://, postgresql://, postgresql+asyncpg://
            is_pg_scheme = cleaned.startswith(
                ("postgres://", "postgresql://", "postgresql+asyncpg://")
            )

            try:
                url_obj = make_url(cleaned)
            except Exception as exc:
                if is_pg_scheme:
                    safe_url = redact_db_url(cleaned)
                    safe_exc = sanitize_exception_text(exc)
                    raise ValueError(
                        f"Invalid or malformed PostgreSQL database URL: {safe_url}. "
                        f"Could not parse database connection string: {safe_exc}"
                    ) from exc
                raise

            driver = url_obj.drivername
            if is_pg_scheme or driver.startswith(("postgres", "postgresql")):
                # Require host or database name for PostgreSQL connection strings
                if not url_obj.host and not url_obj.database:
                    safe_url = redact_db_url(cleaned)
                    raise ValueError(
                        f"Invalid or malformed PostgreSQL database URL: {safe_url}. "
                        "Host or database name is required."
                    )
                query_dict = dict(url_obj.query)
                # Translate sslmode to ssl for asyncpg driver compatibility
                if "sslmode" in query_dict:
                    ssl_val = query_dict.pop("sslmode")
                    if "ssl" not in query_dict:
                        query_dict["ssl"] = ssl_val
                url_obj = url_obj.set(drivername="postgresql+asyncpg", query=query_dict)
                return url_obj.render_as_string(hide_password=False)

            return cleaned

        db_path = self.get_data_dir(create=False) / "hawkoffer.db"
        # Always use forward slashes for SQLite URLs across all OSes
        clean_path = db_path.as_posix()
        return f"sqlite+aiosqlite:///{clean_path}"

    def get_sync_database_url(self) -> str:
        """Resolve a synchronous database URL for Alembic migrations or sync utilities."""
        async_url = self.get_database_url()
        if async_url.startswith("sqlite+aiosqlite:///"):
            return async_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        if async_url.startswith("sqlite:///"):
            return async_url

        try:
            url_obj = make_url(async_url)
            driver = url_obj.drivername
            if driver.startswith(("postgres", "postgresql")):
                query_dict = dict(url_obj.query)
                # Convert ssl query parameter to sslmode for synchronous drivers
                if "ssl" in query_dict:
                    ssl_val = query_dict.pop("ssl")
                    if "sslmode" not in query_dict:
                        query_dict["sslmode"] = ssl_val
                url_obj = url_obj.set(drivername="postgresql", query=query_dict)
                return url_obj.render_as_string(hide_password=False)
        except Exception:
            pass

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

    def validate_production_jwt_secret(self) -> tuple[bool, str]:
        """Validate whether the configured JWT secret key meets production security standards.

        Returns:
            Tuple of (is_valid, reason).
        """
        if not self.jwt_secret_key:
            return False, "HAWKOFFER_JWT_SECRET_KEY is not configured"
        secret = self.jwt_secret_key.strip()
        if (
            secret.lower() in KNOWN_INSECURE_CRON_SECRETS
            or secret == "hawkoffer-dev-secret-key-change-in-production-min-32-chars"
        ):
            return False, "HAWKOFFER_JWT_SECRET_KEY uses a known insecure development default"
        if len(secret) < 32:
            return False, "HAWKOFFER_JWT_SECRET_KEY must be at least 32 characters in production"
        return True, "Valid"

    def validate_production_configuration(self) -> list[str]:
        """Validate all critical production settings. Returns list of error reasons."""
        errors: list[str] = []
        is_jwt_valid, jwt_reason = self.validate_production_jwt_secret()
        if not is_jwt_valid:
            errors.append(jwt_reason)
        is_cron_valid, cron_reason = self.validate_production_cron_secret()
        if not is_cron_valid:
            errors.append(cron_reason)
        for origin in self.get_cors_origins():
            if origin == "*":
                errors.append("Wildcard '*' CORS origin is not permitted with credentials")
        return errors


# Global settings singleton
settings = Settings()
