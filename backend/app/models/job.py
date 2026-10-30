"""Job SQLAlchemy model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, utc_now

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.notification import Notification


class Job(Base, TimestampMixin):
    """Normalized career opportunity."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    employment_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g., 'full_time', 'internship', 'co_op', 'contract'
    job_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g., 'remote', 'hybrid', 'onsite'
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    fingerprint_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256(company_id + normalized_title + location + clean_url)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256(description)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default="NEW", nullable=False
    )  # 'NEW', 'ACTIVE', 'UPDATED', 'CLOSED'

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="jobs", lazy="selectin")
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_jobs_company_status", "company_id", "status"),
        Index("ix_jobs_application_url", "application_url"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_first_seen_at", "first_seen_at"),
        Index("ix_jobs_fingerprint_hash", "fingerprint_hash", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<Job(id={self.id}, title='{self.title}', "
            f"company_id={self.company_id}, status='{self.status}')>"
        )
