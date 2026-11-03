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


def verify_vapid_keypair_match(pub_key: str, priv_key: str) -> tuple[bool, str]:
    """Cryptographically parse and confirm that a VAPID public key corresponds to the private key.

    Uses cryptography SECP256R1 and rejects malformed, invalid, or mismatched keys.
    """
    if not pub_key or not isinstance(pub_key, str):
        return False, "Public key must be a non-empty string"
    if not priv_key or not isinstance(priv_key, str):
        return False, "Private key must be a non-empty string"

    pub_clean = pub_key.strip()
    priv_clean = priv_key.strip()

    # 1. Parse Private Key
    try:
        if priv_clean.startswith("-----BEGIN"):
            ec_priv = serialization.load_pem_private_key(priv_clean.encode("utf-8"), password=None)
        else:
            # Raw base64/base64url encoded scalar or DER
            pad = "=" * ((4 - len(priv_clean) % 4) % 4)
            raw_priv = base64.urlsafe_b64decode((priv_clean + pad).encode("ascii"))
            if len(raw_priv) == 32:
                ec_priv = ec.derive_private_key(int.from_bytes(raw_priv, "big"), ec.SECP256R1())
            else:
                ec_priv = serialization.load_der_private_key(raw_priv, password=None)
    except Exception as exc:
        return False, f"Failed to parse VAPID private key: {exc}"

    if not isinstance(ec_priv, ec.EllipticCurvePrivateKey):
        return False, "Private key is not an elliptic curve key"
    if not isinstance(ec_priv.curve, ec.SECP256R1):
        return False, "VAPID private key must use the NIST P-256 (SECP256R1) curve"

    # 2. Parse Public Key
    try:
        pub_pad = "=" * ((4 - len(pub_clean) % 4) % 4)
        raw_pub = base64.urlsafe_b64decode((pub_clean + pub_pad).encode("ascii"))
    except Exception as exc:
        return False, f"Failed to decode base64url public key: {exc}"

    if len(raw_pub) != 65:
        return False, f"VAPID public key must be 65 bytes uncompressed point (got {len(raw_pub)})"
    if raw_pub[0] != 0x04:
        return False, "VAPID public key must start with 0x04 (uncompressed point format)"

    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw_pub)
    except Exception as exc:
        return False, f"Failed to load public key point on SECP256R1: {exc}"

    # 3. Confirm mathematical correspondence
    derived_pub_bytes = ec_priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    if raw_pub != derived_pub_bytes:
        return False, "VAPID public key does not correspond to the private key"

    return True, "Valid"


# Ephemeral in-memory keypair for development/test mode when keys are not explicitly set
_dev_keypair: VapidKeyPair | None = None


def get_effective_vapid_keys() -> VapidKeyPair:
    """Resolve the active VAPID keypair.

    In production: Requires explicitly configured and mathematically matching
    HAWKOFFER_VAPID_PUBLIC_KEY and HAWKOFFER_VAPID_PRIVATE_KEY.
    In development/testing: Automatically generates an ephemeral keypair if none is
    configured, ensuring frictionless local testing.
    """
    global _dev_keypair

    pub = settings.vapid_public_key
    priv = settings.vapid_private_key

    if pub and priv:
        pub_clean = pub.strip()
        priv_clean = priv.strip()
        is_valid, err = verify_vapid_keypair_match(pub_clean, priv_clean)
        if not is_valid:
            logger.error("Configured VAPID keypair failed verification: %s", err)
            raise RuntimeError(f"Configured VAPID keypair is invalid or mismatched: {err}")
        return VapidKeyPair(public_key=pub_clean, private_key=priv_clean)

    if settings.is_production:
        logger.error(
            "VAPID keys not configured in production environment! "
            "Set HAWKOFFER_VAPID_PUBLIC_KEY and HAWKOFFER_VAPID_PRIVATE_KEY."
        )
        raise RuntimeError(
            "CRITICAL: VAPID keys are missing in production environment! "
            "Configure HAWKOFFER_VAPID_PUBLIC_KEY and HAWKOFFER_VAPID_PRIVATE_KEY."
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
    """Validate that VAPID configuration exists and meets production cryptographic standards."""
    if not settings.vapid_public_key:
        return False, "HAWKOFFER_VAPID_PUBLIC_KEY is not configured"
    if not settings.vapid_private_key:
        return False, "HAWKOFFER_VAPID_PRIVATE_KEY is not configured"

    return verify_vapid_keypair_match(
        settings.vapid_public_key.strip(),
        settings.vapid_private_key.strip(),
    )
