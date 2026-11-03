"""Pydantic schemas for Web Push subscriptions and dispatch operations."""

import ipaddress
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PushDeliveryStatus(StrEnum):
    """Device-level Web Push delivery lifecycle states."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DELIVERED = "DELIVERED"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PERMANENTLY_INVALID = "PERMANENTLY_INVALID"
    EXHAUSTED = "EXHAUSTED"
    FAILED = "FAILED"  # Backward compatibility alias for transient/historical failure


class VapidPublicKeyResponse(BaseModel):
    """Public VAPID applicationServerKey for browser pushManager subscription."""

    public_key: str = Field(description="Base64url-encoded P-256 public key")


class PushKeys(BaseModel):
    """Cryptographic client keys required by RFC 8291."""

    p256dh: str = Field(description="Client P-256 public key (base64url)")
    auth: str = Field(description="Client authentication secret (base64url)")

    @field_validator("p256dh", "auth")
    @classmethod
    def validate_key_non_empty(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("Push cryptographic key must not be empty")
        return val


def validate_push_endpoint_url(endpoint: str, allow_private: bool = False) -> str:
    """Validate that endpoint is a valid, secure HTTPS Web Push URL."""
    val = endpoint.strip()
    if not val:
        raise ValueError("Push endpoint cannot be empty")

    try:
        parsed = urlparse(val)
    except Exception as exc:
        raise ValueError(f"Malformed push endpoint URL: {exc}") from exc

    if parsed.scheme.lower() != "https":
        raise ValueError("Push subscription endpoint must use secure 'https' scheme")

    if parsed.username or parsed.password:
        raise ValueError("Push subscription endpoint must not include user credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Push subscription endpoint must include a valid host")

    if not allow_private:
        if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Push subscription endpoint cannot target localhost")
        try:
            ip = ipaddress.ip_address(hostname)
            is_ip = True
        except ValueError:
            is_ip = False

        if is_ip and (ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local):
            raise ValueError("Push subscription endpoint cannot target private IP addresses")

    return val


class PushSubscriptionCreate(BaseModel):
    """Payload sent by the browser when registering a Web Push subscription."""

    endpoint: str = Field(description="Push provider gateway endpoint URL")
    keys: PushKeys = Field(description="Client cryptographic keys for payload encryption")
    user_agent: str | None = Field(default=None, description="Optional browser user-agent")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        return validate_push_endpoint_url(v)


class PushSubscriptionRead(BaseModel):
    """Subscription record returned to authenticated user (sensitive keys omitted)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    endpoint: str
    p256dh: str | None = Field(default=None, description="Redacted for security")
    auth: str | None = Field(default=None, description="Redacted for security")
    user_agent: str | None = None
    failure_count: int = 0
    created_at: datetime
    last_used_at: datetime


class PushUnsubscribeRequest(BaseModel):
    """Payload sent by the browser to unregister a Web Push subscription."""

    endpoint: str = Field(description="Push provider gateway endpoint URL to unregister")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        return validate_push_endpoint_url(v)


class PushDispatchResponse(BaseModel):
    """Execution statistics for a background push dispatch run."""

    status: str
    notifications_claimed: int
    attempted_pushes: int
    successful_pushes: int
    transient_failures: int
    pruned_subscriptions: int
    completed_notifications: int
    retry_notifications: int
    failed_notifications: int
    duration_ms: int
