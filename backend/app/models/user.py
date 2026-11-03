"""User SQLAlchemy model for multi-tenant identity and authentication."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application import JobApplication
    from app.models.notification import Notification
    from app.models.preference import PreferenceProfile
    from app.models.push import PushSubscription
    from app.models.subscription import UserCompanySubscription
    from app.models.token import RefreshToken


class User(Base, TimestampMixin):
    """User account entity for multi-tenant authentication and profile ownership."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="user", nullable=False)  # 'user', 'admin'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    profiles: Mapped[list["PreferenceProfile"]] = relationship(
        "PreferenceProfile", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    subscriptions: Mapped[list["UserCompanySubscription"]] = relationship(
        "UserCompanySubscription",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    applications: Mapped[list["JobApplication"]] = relationship(
        "JobApplication", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        "PushSubscription", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_is_active", "is_active"),
        Index("ix_users_role", "role"),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}', role='{self.role}')>"
