"""CompanyScanState SQLAlchemy model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.company import Company


class CompanyScanState(Base, TimestampMixin):
    """Persistent scheduler and health state for monitored companies."""

    __tablename__ = "company_scan_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="IDLE", nullable=False
    )  # 'IDLE', 'RUNNING', 'FAILED', 'RATE_LIMITED'

    last_scan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_scan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_scans: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    company: Mapped["Company"] = relationship(
        "Company", back_populates="scan_state", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_company_scan_states_company_id", "company_id", unique=True),
        Index("ix_company_scan_states_status", "status"),
        Index("ix_company_scan_states_next_scan_at", "next_scan_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<CompanyScanState(company_id={self.company_id}, status='{self.status}', "
            f"failures={self.consecutive_failures})>"
        )
