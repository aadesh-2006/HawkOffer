"""PreferenceProfile SQLAlchemy model."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.notification import Notification


class PreferenceProfile(Base, TimestampMixin):
    """User preferences and eligibility filters."""

    __tablename__ = "preference_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default="Default Profile", nullable=False)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # JSON stored lists of strings
    preferred_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_employment_types: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    required_employment_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_workplace_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_title_keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_content_keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_locations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    require_graduation_year_match: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    minimum_match_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="profile", passive_deletes=True, lazy="selectin"
    )

    __table_args__ = (Index("ix_preference_profiles_is_active", "is_active"),)

    def __repr__(self) -> str:
        return f"<PreferenceProfile(id={self.id}, name='{self.name}', is_active={self.is_active})>"
