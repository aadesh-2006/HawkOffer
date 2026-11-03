"""Distributed lease model for cross-process coordination without external brokers."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utc_now


class DistributedLease(Base):
    """Zero-cost distributed lease entity for cross-process mutual exclusion.

    Guarantees single-owner execution across multiple application processes and containers
    using atomic database updates and expiration TTLs on SQLite WAL and PostgreSQL.
    """

    __tablename__ = "distributed_leases"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<DistributedLease(name='{self.name}', owner_token='{self.owner_token[:8]}...', "
            f"expires_at='{self.expires_at}')>"
        )
