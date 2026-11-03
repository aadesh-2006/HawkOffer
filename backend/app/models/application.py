"""JobApplication SQLAlchemy model for tracking user job pipeline statuses."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.user import User


class JobApplication(Base, TimestampMixin):
    """User-specific application tracking for discovered career opportunities."""

    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="SAVED", nullable=False
    )  # 'SAVED', 'APPLIED', 'INTERVIEWING', 'OFFER', 'REJECTED'
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="applications", lazy="selectin")
    job: Mapped["Job"] = relationship("Job", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_application"),
        Index("ix_job_applications_user_status", "user_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<JobApplication(id={self.id}, user_id={self.user_id}, "
            f"job_id={self.job_id}, status='{self.status}')>"
        )
