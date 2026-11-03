"""Pydantic schemas for Web Push subscriptions and dispatch operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class PushSubscriptionCreate(BaseModel):
    """Payload sent by the browser when registering a Web Push subscription."""

    endpoint: str = Field(description="Push provider gateway endpoint URL")
    keys: PushKeys = Field(description="Client cryptographic keys for payload encryption")
    user_agent: str | None = Field(default=None, description="Optional browser user-agent")

    @field_validator("endpoint")
    @classmethod
    def validate_https_endpoint(cls, v: str) -> str:
        val = v.strip()
        if not val.startswith("https://"):
            raise ValueError("Push subscription endpoint must use secure HTTPS")
        return val


class PushSubscriptionRead(BaseModel):
    """Subscription record returned to authenticated user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None
    failure_count: int
    created_at: datetime
    last_used_at: datetime


class PushUnsubscribeRequest(BaseModel):
    """Payload sent by the browser to unregister a Web Push subscription."""

    endpoint: str = Field(description="Push provider gateway endpoint URL to unregister")


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
