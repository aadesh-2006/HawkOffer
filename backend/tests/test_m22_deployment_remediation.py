"""Tests for Milestone 22: Deployment Remediation.

Validates:
1. PostgreSQL URL normalization for asyncpg (postgres:// and postgresql://).
2. Query parameter compatibility: translates sslmode=require to ssl=require for asyncpg.
3. Authority-scoped credential repair: multiple literal @ in passwords repaired to %40.
4. Preserves @ in query parameters (e.g. application_name=hawk@offer).
5. Fails clearly on ambiguous unencoded @ in username.
6. Special character handling in credentials: percent-encoded passwords (%40, %23, %25).
7. Alembic ConfigParser interpolation safety: percent signs do not raise interpolation errors.
8. Supabase transaction pooler vs session pooler statement_cache_size handling.
9. SQLite URL preservation: file paths, memory databases, and default settings fallback.
"""

from unittest.mock import MagicMock, patch

import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core.config import Settings, redact_db_url, sanitize_exception_text
from app.database.session import create_engine_instance, is_transaction_pooler_url
from app.database.wait_for_db import sanitize_db_url_for_logging


def test_sqlite_url_normalization() -> None:
    """Verify SQLite URLs are preserved without escaping or corruption."""
    s1 = Settings(database_url="sqlite:///var/data/hawkoffer.db")
    assert s1.get_database_url() == "sqlite+aiosqlite:///var/data/hawkoffer.db"
    assert s1.get_sync_database_url() == "sqlite:///var/data/hawkoffer.db"

    s2 = Settings(database_url="sqlite+aiosqlite:///var/data/hawkoffer.db")
    assert s2.get_database_url() == "sqlite+aiosqlite:///var/data/hawkoffer.db"

    s3 = Settings(database_url="sqlite:///:memory:")
    assert s3.get_database_url() == "sqlite+aiosqlite:///:memory:"

    s_default = Settings(database_url=None)
    assert s_default.get_database_url().startswith("sqlite+aiosqlite:///")
    assert s_default.get_database_url().endswith("hawkoffer.db")


def test_postgresql_scheme_normalization() -> None:
    """Verify postgres:// and postgresql:// are normalized to postgresql+asyncpg://."""
    s1 = Settings(database_url="postgres://user:password@localhost:5432/hawkoffer")
    assert s1.get_database_url() == "postgresql+asyncpg://user:password@localhost:5432/hawkoffer"

    s2 = Settings(database_url="postgresql://user:password@localhost:5432/hawkoffer")
    assert s2.get_database_url() == "postgresql+asyncpg://user:password@localhost:5432/hawkoffer"

    s3 = Settings(database_url="postgresql+asyncpg://user:password@localhost:5432/hawkoffer")
    assert s3.get_database_url() == "postgresql+asyncpg://user:password@localhost:5432/hawkoffer"


def test_sslmode_to_ssl_normalization_for_asyncpg() -> None:
    """Verify sslmode=... is translated to ssl=... so asyncpg.connect does not reject it."""
    raw = "postgresql://postgres:secret@db.project.supabase.co:5432/postgres?sslmode=require"
    s = Settings(database_url=raw)
    resolved = s.get_database_url()

    assert resolved.startswith("postgresql+asyncpg://")
    assert "ssl=require" in resolved
    assert "sslmode" not in resolved

    sync_url = s.get_sync_database_url()
    assert sync_url.startswith("postgresql://")
    assert "sslmode=require" in sync_url


def test_supabase_pooler_connection_string() -> None:
    """Verify Supabase connection pooler with dotted username and port 6543."""
    pooler_raw = (
        "postgresql://postgres.myprojectref:StrongP%40ssw0rd!"
        "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
    )
    s = Settings(database_url=pooler_raw)
    resolved = s.get_database_url()

    parsed = make_url(resolved)
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.username == "postgres.myprojectref"
    assert parsed.host == "aws-0-us-east-1.pooler.supabase.com"
    assert parsed.port == 6543
    assert parsed.database == "postgres"
    assert parsed.query.get("ssl") == "require"


def test_query_string_at_character_preservation() -> None:
    """Verify @ character in query parameters is never misinterpreted as credentials delimiter."""
    raw = (
        "postgresql://user:password@db.example.com:5432/hawkoffer"
        "?application_name=hawk@offer&source=test@unit&sslmode=require"
    )
    s = Settings(database_url=raw)
    resolved = s.get_database_url()

    parsed = make_url(resolved)
    assert parsed.username == "user"
    assert parsed.password == "password"
    assert parsed.host == "db.example.com"
    assert parsed.port == 5432
    assert parsed.query.get("application_name") == "hawk@offer"
    assert parsed.query.get("source") == "test@unit"
    assert parsed.query.get("ssl") == "require"


def test_multiple_literal_at_in_password_repaired() -> None:
    """Verify multiple unencoded @ characters in password are repaired without touching host."""
    raw = (
        "postgresql://user:p@ss@word!@aws-0.pooler.supabase.com:6543/postgres"
        "?application_name=hawk@offer&sslmode=require"
    )
    s = Settings(database_url=raw)
    resolved = s.get_database_url()

    parsed = make_url(resolved)
    assert parsed.username == "user"
    assert parsed.password == "p@ss@word!"
    assert parsed.host == "aws-0.pooler.supabase.com"
    assert parsed.port == 6543
    assert parsed.query.get("application_name") == "hawk@offer"
    assert parsed.query.get("ssl") == "require"


def test_unencoded_at_in_username_fails_clearly() -> None:
    """Verify unencoded @ in username fails clearly with ValueError instead of silent corruption."""
    raw = "postgresql://user@tenant:password@host:5432/db"
    s = Settings(database_url=raw)
    with pytest.raises(
        ValueError,
        match="Ambiguous database credentials: username contains an unencoded '@'",
    ) as exc_info:
        s.get_database_url()
    # Confirm credentials are not leaked in the exception message
    assert "user@tenant" not in str(exc_info.value)
    assert "password" not in str(exc_info.value)


def test_percent_encoded_credentials_preservation() -> None:
    """Verify percent-encoded characters in passwords are preserved accurately."""
    raw = (
        "postgresql://postgres.myprojectref:p%40ss%23word%25123"
        "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
    )
    s = Settings(database_url=raw)
    resolved = s.get_database_url()

    parsed = make_url(resolved)
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.password == "p@ss#word%123"
    rendered = parsed.render_as_string(hide_password=False)
    assert "p%40ss%23word%25123" in rendered


def test_alembic_config_percent_interpolation_safety() -> None:
    """Verify Alembic ConfigParser does not crash on percent signs in credentials."""
    db_url_with_percent = (
        "postgresql+asyncpg://postgres.ref:p%40ss%25word"
        "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?ssl=require"
    )

    cfg = Config("alembic.ini")
    safe_for_config = db_url_with_percent.replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", safe_for_config)

    retrieved = cfg.get_main_option("sqlalchemy.url")
    assert retrieved == db_url_with_percent


def test_wait_for_db_credential_sanitization() -> None:
    """Verify password masking in wait_for_db logging does not leak credentials."""
    raw = (
        "postgresql+asyncpg://postgres.projectref:SecretPass123!"
        "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?ssl=require"
    )
    sanitized = sanitize_db_url_for_logging(raw)
    assert "SecretPass123!" not in sanitized
    assert "postgres.projectref" not in sanitized
    assert "***:***@" in sanitized
    assert "aws-0-us-east-1.pooler.supabase.com:6543/postgres" in sanitized


def test_is_transaction_pooler_url_detection() -> None:
    """Verify accurate transaction pooler detection across hostnames and ports."""
    # Supabase transaction pooler on port 6543
    t1 = "postgresql+asyncpg://user:pass@aws-0.pooler.supabase.com:6543/postgres"
    assert is_transaction_pooler_url(t1) is True

    # Supabase session pooler on port 5432 (supports prepared statements)
    s1 = "postgresql+asyncpg://user:pass@aws-0.pooler.supabase.com:5432/postgres"
    assert is_transaction_pooler_url(s1) is False

    # Direct PostgreSQL on port 5432
    d1 = "postgresql+asyncpg://user:pass@db.myproject.supabase.co:5432/postgres"
    assert is_transaction_pooler_url(d1) is False

    # Explicit pgbouncer=true query parameter on port 5432
    p1 = "postgresql+asyncpg://user:pass@localhost:5432/postgres?pgbouncer=true"
    assert is_transaction_pooler_url(p1) is True

    # Explicit pool_mode=transaction
    p2 = "postgresql+asyncpg://user:pass@localhost:5432/postgres?pool_mode=transaction"
    assert is_transaction_pooler_url(p2) is True


def test_engine_connect_args_for_poolers() -> None:
    """Genuinely verify connect_args passed to create_async_engine for various connection modes."""
    with patch("app.database.session.create_async_engine") as mock_engine:
        mock_engine.return_value = MagicMock()

        # 1. Supabase transaction pooler on port 6543 -> receives statement_cache_size=0
        create_engine_instance("postgresql+asyncpg://user:pass@aws-0.pooler.supabase.com:6543/db")
        _, kwargs_tx = mock_engine.call_args
        assert kwargs_tx.get("connect_args") == {"statement_cache_size": 0}

        # 2. Supabase session pooler on port 5432 -> does NOT receive statement_cache_size=0
        create_engine_instance("postgresql+asyncpg://user:pass@aws-0.pooler.supabase.com:5432/db")
        _, kwargs_session = mock_engine.call_args
        assert "connect_args" not in kwargs_session

        # 3. Direct PostgreSQL on port 5432 -> does NOT receive statement_cache_size=0
        create_engine_instance("postgresql+asyncpg://user:pass@db.project.supabase.co:5432/db")
        _, kwargs_direct = mock_engine.call_args
        assert "connect_args" not in kwargs_direct

        # 4. Explicit pgbouncer=true -> receives statement_cache_size=0
        create_engine_instance("postgresql+asyncpg://user:pass@localhost:5432/db?pgbouncer=true")
        _, kwargs_explicit = mock_engine.call_args
        assert kwargs_explicit.get("connect_args") == {"statement_cache_size": 0}


def test_database_url_environment_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify standard DATABASE_URL is picked up if HAWKOFFER_DATABASE_URL is unset."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://env_user:env_pass@localhost:5432/env_db?sslmode=require",
    )
    s = Settings(database_url=None)
    resolved = s.get_database_url()
    assert resolved.startswith("postgresql+asyncpg://env_user:env_pass@localhost:5432/env_db")
    assert "ssl=require" in resolved


def test_malformed_postgresql_urls_raise_clear_error() -> None:
    """Verify malformed PostgreSQL URLs fail fast with ValueError instead of silent fallback."""
    # 1. Invalid port non-integer
    s1 = Settings(database_url="postgresql://user:pass@localhost:not_a_port/hawkoffer")
    with pytest.raises(ValueError, match="Invalid or malformed PostgreSQL database URL"):
        s1.get_database_url()

    # 2. Unclosed IPv6 bracket
    s2 = Settings(database_url="postgresql://user:pass@[::1/hawkoffer")
    with pytest.raises(ValueError, match="Invalid or malformed PostgreSQL database URL"):
        s2.get_database_url()

    # 3. Empty PostgreSQL URL without host or database
    s3 = Settings(database_url="postgresql://")
    with pytest.raises(ValueError, match="Host or database name is required"):
        s3.get_database_url()

    # 4. Authority with multiple @ and missing colon separator
    s4 = Settings(database_url="postgresql://user_without_colon@second@host:5432/db")
    with pytest.raises(ValueError, match="Ambiguous database credentials in authority"):
        s4.get_database_url()


def test_exception_messages_never_leak_credentials() -> None:
    """Verify credentials and passwords are never exposed in ValueError messages."""
    secret_pw = "SuperSecret_P@ss_123!"
    secret_user = "prod_hawk_user"

    # 1. Malformed port: credentials must be masked as ***:***@
    s1 = Settings(
        database_url=f"postgresql://{secret_user}:{secret_pw}@db.example.com:not_a_port/hawkoffer"
    )
    with pytest.raises(ValueError) as exc1:
        s1.get_database_url()
    assert secret_pw not in str(exc1.value)
    assert secret_user not in str(exc1.value)
    assert "***:***@db.example.com" in str(exc1.value)

    # 2. Unclosed IPv6 host with credentials
    s2 = Settings(
        database_url=f"postgresql://{secret_user}:{secret_pw}@[::1/hawkoffer"
    )
    with pytest.raises(ValueError) as exc2:
        s2.get_database_url()
    assert secret_pw not in str(exc2.value)
    assert secret_user not in str(exc2.value)
    assert "***:***@" in str(exc2.value)

    # 3. Ambiguous authority with multiple @ and no colon separator
    s3 = Settings(
        database_url=f"postgresql://{secret_user}@{secret_pw}@db.example.com:5432/hawkoffer"
    )
    with pytest.raises(ValueError) as exc3:
        s3.get_database_url()
    assert secret_pw not in str(exc3.value)
    assert secret_user not in str(exc3.value)

    # 4. Ambiguous authority with unencoded @ in username
    s4 = Settings(
        database_url=f"postgresql://{secret_user}@domain:{secret_pw}@db.example.com:5432/hawkoffer"
    )
    with pytest.raises(ValueError) as exc4:
        s4.get_database_url()
    assert secret_pw not in str(exc4.value)
    assert secret_user not in str(exc4.value)


def test_sync_database_url_ssl_parameter_conversion() -> None:
    """Verify get_sync_database_url accurately converts ssl to sslmode via make_url."""
    # 1. Normal ssl=require conversion
    raw1 = "postgresql://user:pass@localhost:5432/db?sslmode=require"
    s1 = Settings(database_url=raw1)
    sync1 = s1.get_sync_database_url()
    assert sync1.startswith("postgresql://")
    parsed1 = make_url(sync1)
    assert parsed1.query.get("sslmode") == "require"
    assert "ssl" not in parsed1.query

    # 2. Existing sslmode preservation (verify-full)
    raw2 = "postgresql://user:pass@localhost:5432/db?sslmode=verify-full"
    s2 = Settings(database_url=raw2)
    sync2 = s2.get_sync_database_url()
    parsed2 = make_url(sync2)
    assert parsed2.query.get("sslmode") == "verify-full"
    assert "ssl" not in parsed2.query

    # 3. Unrelated query values containing 'ssl=' substring must NOT be corrupted
    raw3 = (
        "postgresql://user:pass@localhost:5432/db"
        "?application_name=hawkssl=best&search=ssl=true&sslmode=require"
    )
    s3 = Settings(database_url=raw3)
    sync3 = s3.get_sync_database_url()
    parsed3 = make_url(sync3)
    assert parsed3.query.get("application_name") == "hawkssl=best"
    assert parsed3.query.get("search") == "ssl=true"
    assert parsed3.query.get("sslmode") == "require"
    assert "ssl" not in parsed3.query

    # 4. Password containing 'ssl=' substring must NOT be corrupted
    raw4 = "postgresql://user:p@ss_ssl=123@localhost:5432/db?sslmode=require"
    s4 = Settings(database_url=raw4)
    sync4 = s4.get_sync_database_url()
    parsed4 = make_url(sync4)
    assert parsed4.password == "p@ss_ssl=123"
    assert parsed4.query.get("sslmode") == "require"

    # 5. Encoded credentials with multiple query parameters preserved
    raw5 = (
        "postgresql://postgres.ref:p%40ss%23word%25123"
        "@aws-0.pooler.supabase.com:6543/postgres"
        "?sslmode=require&application_name=hawk@offer&channel_binding=prefer"
    )
    s5 = Settings(database_url=raw5)
    sync5 = s5.get_sync_database_url()
    parsed5 = make_url(sync5)
    assert parsed5.drivername == "postgresql"
    assert parsed5.password == "p@ss#word%123"
    assert parsed5.query.get("application_name") == "hawk@offer"
    assert parsed5.query.get("channel_binding") == "prefer"
    assert parsed5.query.get("sslmode") == "require"
    assert "p%40ss%23word%25123" in sync5


def test_redact_db_url_helper() -> None:
    """Verify redact_db_url helper masks credentials across all formats."""
    # Standard URL
    assert redact_db_url("postgresql://u:p@host:5432/db") == "postgresql://***:***@host:5432/db"
    # Dotted username with special characters in password
    pooler = "postgresql://postgres.ref:Str0ng%40P%23ss@aws-0.pooler.supabase.com:6543/postgres"
    assert redact_db_url(pooler) == "postgresql://***:***@aws-0.pooler.supabase.com:6543/postgres"
    # Bare authority string
    assert redact_db_url("user:pass@localhost:5432") == "***:***@localhost:5432"
    # SQLite URL without credentials
    assert redact_db_url("sqlite:///data/hawkoffer.db") == "sqlite:///data/hawkoffer.db"
    # Empty string
    assert redact_db_url("") == ""


def test_redact_db_url_sensitive_query_parameters_and_delimiters() -> None:
    """Verify redact_db_url hardens query params, isolates authority, and redacts fragments."""
    # 1. URL with credentials and ordinary query parameters (preserves diagnostics)
    url1 = "postgresql://db_user:db_password@host:5432/dbname?sslmode=require&connect_timeout=10"
    redacted1 = redact_db_url(url1)
    assert redacted1 == "postgresql://***:***@host:5432/dbname?sslmode=require&connect_timeout=10"
    assert "db_user" not in redacted1
    assert "db_password" not in redacted1

    # 2. URL without credentials but with a sensitive query parameter
    url2 = "postgresql://localhost:5432/dbname?token=secret_jwt_token_123&sslmode=require"
    redacted2 = redact_db_url(url2)
    assert redacted2 == "postgresql://localhost:5432/dbname?token=***&sslmode=require"
    assert "secret_jwt_token_123" not in redacted2
    assert "localhost:5432" in redacted2

    # 3. Query parameter containing an '@' character (must NOT be treated as authority delimiter)
    url3 = "postgresql://localhost:5432/dbname?application_name=hawk@offer&source=test@unit"
    redacted3 = redact_db_url(url3)
    assert redacted3 == "postgresql://localhost:5432/dbname?application_name=hawk@offer&source=test@unit"
    assert "localhost:5432" in redacted3
    assert "***" not in redacted3  # No credentials present, so no authority masking

    # 4. Multiple sensitive query parameters
    url4 = (
        "postgresql://prod_user:prod_pass@host:5432/db"
        "?api_key=key_val&secret=secret_val&access_token=token_val"
        "&refresh_token=refresh_val&password=pass_val&debug=true"
    )
    redacted4 = redact_db_url(url4)
    assert "prod_user" not in redacted4
    assert "prod_pass" not in redacted4
    assert "key_val" not in redacted4
    assert "secret_val" not in redacted4
    assert "token_val" not in redacted4
    assert "refresh_val" not in redacted4
    assert "pass_val" not in redacted4
    assert "api_key=***" in redacted4
    assert "secret=***" in redacted4
    assert "access_token=***" in redacted4
    assert "refresh_token=***" in redacted4
    assert "password=***" in redacted4
    assert "debug=true" in redacted4

    # 5. Malformed URL inputs do not throw exceptions
    assert redact_db_url("malformed_junk_url:::@@@") == "***:***@"
    assert redact_db_url("postgresql://user:pass@host:bad_port/db") == "postgresql://***:***@host:bad_port/db"
    assert redact_db_url("postgresql://user:pass@[::1/db") == "postgresql://***:***@[::1/db"
    assert redact_db_url("   ") == ""

    # 6. URL fragment content is masked to prevent token leakage
    url6 = "postgresql://user:pass@host:5432/db?ssl=require#access_token=secret_frag_val"
    redacted6 = redact_db_url(url6)
    assert "secret_frag_val" not in redacted6
    assert redacted6 == "postgresql://***:***@host:5432/db?ssl=require#***"


def test_malformed_url_exception_sanitization_without_credentials() -> None:
    """Verify exceptions sanitize sensitive query keys even when no username/password exists."""
    # 1. Malformed PostgreSQL URL with no credentials but ?password=secret
    raw1 = "postgresql://localhost:not_a_port/db?password=secret_db_pass_123"
    s1 = Settings(database_url=raw1)
    with pytest.raises(ValueError) as exc1:
        s1.get_database_url()
    msg1 = str(exc1.value)
    assert "secret_db_pass_123" not in msg1
    assert "password=***" in msg1
    assert raw1 not in msg1

    # 2. Malformed URL containing ?access_token=secret
    raw2 = "postgresql://localhost:not_a_port/db?access_token=secret_access_token_456"
    s2 = Settings(database_url=raw2)
    with pytest.raises(ValueError) as exc2:
        s2.get_database_url()
    msg2 = str(exc2.value)
    assert "secret_access_token_456" not in msg2
    assert "access_token=***" in msg2
    assert raw2 not in msg2

    # 3. URL-encoded sensitive query key (%74oken=secret)
    raw3 = "postgresql://localhost:not_a_port/db?%74oken=secret_encoded_token_789"
    s3 = Settings(database_url=raw3)
    with pytest.raises(ValueError) as exc3:
        s3.get_database_url()
    msg3 = str(exc3.value)
    assert "secret_encoded_token_789" not in msg3
    assert "%74oken=***" in msg3
    assert raw3 not in msg3

    # 4. Direct verification of sanitize_exception_text helper
    embedded_err = ValueError(
        "Failed connection: postgresql://localhost:5432/db?%74oken=raw_secret_leak"
    )
    sanitized_msg = sanitize_exception_text(embedded_err)
    assert "raw_secret_leak" not in sanitized_msg
    assert "%74oken=***" in sanitized_msg

    # 5. Non-exception and failsafe handling
    assert sanitize_exception_text(None) == ""
    assert sanitize_exception_text("") == ""




