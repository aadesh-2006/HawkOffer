"""Pydantic schemas for authentication and session management."""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserRead


class UserRegisterRequest(BaseModel):
    """Payload for user registration."""

    email: str = Field(..., max_length=255, description="User email address")
    password: str = Field(..., min_length=8, max_length=128, description="Plaintext password")
    full_name: str | None = Field(default=None, max_length=255, description="Full name")


class UserLoginRequest(BaseModel):
    """Payload for user authentication."""

    email: str = Field(..., max_length=255, description="User email address")
    password: str = Field(..., max_length=128, description="User password")


class TokenResponse(BaseModel):
    """Access and refresh token payload returned upon authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead

    model_config = ConfigDict(from_attributes=True)


class TokenRefreshRequest(BaseModel):
    """Payload to refresh an access token using a refresh token."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Payload for session logout and refresh token revocation."""

    refresh_token: str | None = None
