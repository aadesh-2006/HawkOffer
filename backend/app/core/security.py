"""Cryptographic security, Argon2 password hashing, and JWT token management."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

# Argon2id password hasher with secure, production-grade memory and time costs
password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
)

# Dummy hash for constant-time comparison when email is not found
DUMMY_HASH = password_hasher.hash("dummy_constant_time_pass_for_timing_mitigation")


def normalize_email(email: str) -> str:
    """Normalize email address consistently by trimming and lowercasing."""
    return email.strip().lower()


def validate_password_strength(password: str) -> tuple[bool, str | None]:
    """Validate password against baseline strength requirements."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if len(password) > 128:
        return False, "Password must not exceed 128 characters."
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."
    if not re.search(r"[\d\W]", password):
        return False, "Password must contain at least one digit or special character."
    return True, None


def hash_password(password: str) -> str:
    """Hash plaintext password securely using Argon2id."""
    return password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify plaintext password against an Argon2 hash using constant-time comparison."""
    try:
        return password_hasher.verify(hashed_password, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> bool:
    """Perform constant-time dummy verification to mitigate user-enumeration timing attacks."""
    try:
        password_hasher.verify(DUMMY_HASH, password)
        return False
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(
    user_id: int,
    email: str,
    role: str = "user",
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Create short-lived JWT access token. Returns (encoded_jwt, token_jti)."""
    now = datetime.now(UTC)
    delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    expires_at = now + delta
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, jti


def create_refresh_token(
    user_id: int,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    """Create long-lived JWT refresh token. Returns (encoded_jwt, token_jti, expires_at)."""
    now = datetime.now(UTC)
    delta = expires_delta or timedelta(days=settings.refresh_token_expire_days)
    expires_at = now + delta
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, jti, expires_at


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    """Decode and validate a signed JWT token."""
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    if expected_type and payload.get("type") != expected_type:
        raise ValueError(
            f"Invalid token type: expected {expected_type}, got {payload.get('type')}"
        )
    return payload
