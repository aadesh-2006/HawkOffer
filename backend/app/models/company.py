"""Company SQLAlchemy model."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.log import MonitoringLog


class Company(Base, TimestampMixin):
    """Monitored company career portal."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    career_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(50), nullable=False, default="generic"
    )  # e.g., 'greenhouse', 'lever', 'ashby', 'workday', 'generic'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    monitoring_logs: Mapped[list["MonitoringLog"]] = relationship(
        "MonitoringLog", back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_companies_is_active", "is_active"),
        Index("ix_companies_platform", "platform"),
    )

    def __repr__(self) -> str:
        return f"<Company(id={self.id}, name='{self.name}', platform='{self.platform}')>"
