"""MonitoringLog SQLAlchemy model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company


class MonitoringLog(Base):
    """Audit log for automated monitoring runs."""

    __tablename__ = "monitoring_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'SUCCESS', 'PARTIAL', 'FAILED', 'RATE_LIMITED', 'SKIPPED'
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    company: Mapped["Company"] = relationship(
        "Company", back_populates="monitoring_logs", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_monitoring_logs_company_id", "company_id"),
        Index("ix_monitoring_logs_status", "status"),
        Index("ix_monitoring_logs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<MonitoringLog(id={self.id}, company_id={self.company_id}, "
            f"status='{self.status}', jobs_found={self.jobs_found})>"
        )
