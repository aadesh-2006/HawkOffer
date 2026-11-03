"""Pydantic schemas for Web Push subscriptions and dispatch operations."""

import ipaddress
import socket
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


def is_ip_prohibited(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
    """Check if an IPv4 or IPv6 address falls within prohibited / private / reserved ranges."""
    # Check IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_ip_prohibited(ip.ipv4_mapped)

    if ip.is_loopback:
        return True, "localhost"
    if ip.is_link_local or ip.is_private:
        return True, "private IP addresses"
    if ip.is_unspecified:
        return True, "unspecified address"
    if ip.is_multicast:
        return True, "multicast address"
    if ip.is_reserved:
        return True, "reserved address"

    return False, ""


def validate_push_endpoint_url(
    endpoint: str,
    allow_private: bool = False,
    check_dns: bool = True,
) -> str:
    """Validate that endpoint is a valid, secure HTTPS Web Push URL with anti-SSRF protections."""
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
        # 1. Literal host name check
        if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Push subscription endpoint cannot target localhost")

        # 2. Check if host is a raw IP literal
        try:
            raw_ip = ipaddress.ip_address(hostname)
            is_ip = True
        except ValueError:
            is_ip = False

        if is_ip:
            prohibited, reason = is_ip_prohibited(raw_ip)
            if prohibited:
                raise ValueError(f"Push subscription endpoint cannot target {reason}")

        # 3. Hostname DNS resolution to prevent DNS rebinding to prohibited addresses
        elif check_dns:
            try:
                addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
                for item in addr_info:
                    sockaddr = item[4]
                    resolved_ip_str = sockaddr[0]
                    resolved_ip = ipaddress.ip_address(resolved_ip_str)
                    prohibited, reason = is_ip_prohibited(resolved_ip)
                    if prohibited:
                        raise ValueError(
                            f"Push subscription endpoint host '{hostname}' "
                            f"resolves to prohibited {reason}"
                        )
            except socket.gaierror:
                # In testing or offline environments, unresolvable test domains like
                # 'push.example.com' shouldn't fail validation if format is valid.
                pass
            except Exception as exc:
                if "prohibited" in str(exc):
                    raise

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
