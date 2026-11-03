"""VAPID (RFC 8292) key management and cryptographic utilities for Web Push."""

import base64
import logging
from typing import NamedTuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import settings

logger = logging.getLogger(__name__)


class VapidKeyPair(NamedTuple):
    """Pair of public (raw base64url) and private (PEM) VAPID keys."""

    public_key: str
    private_key: str


def generate_vapid_keypair() -> VapidKeyPair:
    """Generate a standard NIST P-256 (SECP256R1) ECDSA key pair for VAPID.

    Returns:
        VapidKeyPair with:
        - public_key: Base64url-encoded uncompressed point (raw 65 bytes, ~87 chars).
        - private_key: PKCS#8 PEM string.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())

    # 1. Uncompressed point (0x04 || X || Y) per RFC 8291
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    b64url_public = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii")

    # 2. PKCS#8 PEM private key
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    return VapidKeyPair(public_key=b64url_public, private_key=pem_private)


# Ephemeral in-memory keypair for development/test mode when keys are not explicitly set
_dev_keypair: VapidKeyPair | None = None


def get_effective_vapid_keys() -> VapidKeyPair:
    """Resolve the active VAPID keypair.

    In production: Requires explicitly configured HAWKOFFER_VAPID_PUBLIC_KEY and
    HAWKOFFER_VAPID_PRIVATE_KEY.
    In development/testing: Automatically generates an ephemeral keypair if none is
    configured, ensuring frictionless local testing.
    """
    global _dev_keypair

    pub = settings.vapid_public_key
    priv = settings.vapid_private_key

    if pub and priv:
        return VapidKeyPair(public_key=pub.strip(), private_key=priv.strip())

    if settings.is_production:
        logger.error(
            "VAPID keys not configured in production environment! "
            "Set HAWKOFFER_VAPID_PUBLIC_KEY and HAWKOFFER_VAPID_PRIVATE_KEY."
        )
        raise RuntimeError(
            "VAPID keys are missing in production. Generate them using generate_vapid_keypair() "
            "and configure HAWKOFFER_VAPID_PUBLIC_KEY and HAWKOFFER_VAPID_PRIVATE_KEY."
        )

    # Development / Testing fallback
    if _dev_keypair is None:
        logger.info("Generating ephemeral VAPID keypair for non-production environment")
        _dev_keypair = generate_vapid_keypair()

    return _dev_keypair


def get_vapid_public_key() -> str:
    """Return the active base64url-encoded VAPID public key for browser registration."""
    return get_effective_vapid_keys().public_key


def get_vapid_private_key() -> str:
    """Return the active VAPID private key (PEM format). Must NEVER be sent to clients."""
    return get_effective_vapid_keys().private_key


def get_vapid_claims() -> dict[str, str]:
    """Return the VAPID JWT claims dictionary containing the contact subject."""
    sub = settings.vapid_claims_email or "mailto:support@hawkoffer.dev"
    if not sub.startswith("mailto:") and not sub.startswith("https://"):
        sub = f"mailto:{sub}"
    return {"sub": sub}


def validate_production_vapid() -> tuple[bool, str]:
    """Validate that VAPID configuration meets production security standards."""
    if not settings.vapid_public_key:
        return False, "HAWKOFFER_VAPID_PUBLIC_KEY is not configured"
    if not settings.vapid_private_key:
        return False, "HAWKOFFER_VAPID_PRIVATE_KEY is not configured"

    pub = settings.vapid_public_key.strip()
    priv = settings.vapid_private_key.strip()

    if len(pub) < 64:
        return False, "HAWKOFFER_VAPID_PUBLIC_KEY is invalid (too short for base64url P-256)"
    if "PRIVATE KEY" not in priv and len(priv) < 32:
        return False, "HAWKOFFER_VAPID_PRIVATE_KEY is invalid"

    return True, "Valid"
