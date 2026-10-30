"""Notification SQLAlchemy model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.preference import PreferenceProfile


class Notification(Base):
    """Notification history and dispatch status."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("preference_profiles.id", ondelete="SET NULL"), nullable=True
    )
    notification_type: Mapped[str] = mapped_column(String(50), default="job_alert", nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="UNREAD", nullable=False
    )  # 'UNREAD', 'READ', 'DISMISSED'
    delivery_status: Mapped[str] = mapped_column(
        String(50), default="PENDING", nullable=False
    )  # 'PENDING', 'DELIVERED', 'FAILED', 'NOT_REQUIRED'

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="notifications", lazy="selectin")
    profile: Mapped["PreferenceProfile | None"] = relationship(
        "PreferenceProfile", back_populates="notifications", lazy="selectin"
    )

    __table_args__ = (
        Index(
            "uq_notifications_job_profile_type",
            "job_id",
            "profile_id",
            "notification_type",
            unique=True,
            sqlite_where=text("profile_id IS NOT NULL"),
            postgresql_where=text("profile_id IS NOT NULL"),
        ),
        Index("ix_notifications_job_id", "job_id"),
        Index("ix_notifications_status", "status"),
        Index("ix_notifications_delivery_status", "delivery_status"),
        Index("ix_notifications_delivered_at", "delivered_at"),
        Index("ix_notifications_read_at", "read_at"),
        Index("ix_notifications_dismissed_at", "dismissed_at"),
        Index(
            "ix_notifications_profile_status_created",
            "profile_id",
            "status",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification(id={self.id}, job_id={self.job_id}, "
            f"status='{self.status}', delivery='{self.delivery_status}')>"
        )
