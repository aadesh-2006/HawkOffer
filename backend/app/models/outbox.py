"""Phase 3 models: Discovery Outbox, Notification Backlog, Reevaluation Task, System Drain State."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, utc_now

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.job import Job
    from app.models.preference import PreferenceProfile


class OutboxStatus(StrEnum):
    """Lifecycle status for JobDiscoveryOutbox."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class BacklogStatus(StrEnum):
    """Lifecycle status for NotificationBacklog."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    STALE = "STALE"
    DROPPED = "DROPPED"


class ReevaluationStatus(StrEnum):
    """Lifecycle status for ProfileReevaluationTask."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class JobDiscoveryOutbox(Base):
    """Durable discovery outbox for newly inserted jobs awaiting profile evaluation."""

    __tablename__ = "job_discovery_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=OutboxStatus.PENDING,
        nullable=False,
        index=True,
    )
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    job: Mapped["Job"] = relationship("Job", lazy="selectin")

    __table_args__ = (
        Index(
            "ix_outbox_claim_pending",
            "status",
            "id",
            sqlite_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_outbox_claim_expired",
            "status",
            "lease_expires_at",
            "id",
            sqlite_where=text("status = 'PROCESSING'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<JobDiscoveryOutbox(id={self.id}, job_id={self.job_id}, status='{self.status}')>"


class NotificationBacklog(Base):
    """Internal candidate notifications backlog awaiting deterministic fair promotion."""

    __tablename__ = "notification_backlog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("preference_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=BacklogStatus.QUEUED,
        nullable=False,
        index=True,
    )
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    job: Mapped["Job"] = relationship("Job", lazy="selectin")
    profile: Mapped["PreferenceProfile"] = relationship("PreferenceProfile", lazy="selectin")

    __table_args__ = (
        # Partial indexes for active QUEUED items (zero temporary B-tree sorting)
        Index(
            "ix_backlog_queued_relevance",
            desc("score"),
            "created_at",
            "id",
            sqlite_where=text("status = 'QUEUED'"),
        ),
        Index(
            "ix_backlog_queued_fifo",
            "created_at",
            "id",
            sqlite_where=text("status = 'QUEUED'"),
        ),
        # Partial indexes for PROCESSING crash recovery
        Index(
            "ix_backlog_processing_relevance",
            desc("score"),
            "created_at",
            "id",
            sqlite_where=text("status = 'PROCESSING'"),
        ),
        Index(
            "ix_backlog_processing_fifo",
            "created_at",
            "id",
            sqlite_where=text("status = 'PROCESSING'"),
        ),
        # Unique candidate per job, profile, and revision
        UniqueConstraint(
            "job_id",
            "profile_id",
            "profile_revision",
            name="uq_backlog_job_profile_rev",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationBacklog(id={self.id}, job_id={self.job_id}, "
            f"profile_id={self.profile_id}, score={self.score}, status='{self.status}')>"
        )


class ProfileReevaluationTask(Base):
    """Historical profile re-evaluation task operating on a frozen max_job_id snapshot."""

    __tablename__ = "profile_reevaluation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("preference_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    max_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_processed_job_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=ReevaluationStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    profile: Mapped["PreferenceProfile"] = relationship("PreferenceProfile", lazy="selectin")

    __table_args__ = (
        Index(
            "uq_active_task_per_profile",
            "profile_id",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ProfileReevaluationTask(id={self.id}, profile_id={self.profile_id}, "
            f"revision={self.profile_revision}, status='{self.status}')>"
        )


class SystemDrainState(Base):
    """Singleton persistent drain state tracking sequential fairness modulo counter."""

    __tablename__ = "system_drain_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    drain_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    @classmethod
    async def get_or_create(cls, session: "AsyncSession") -> "SystemDrainState":
        """Get or reliably initialize the singleton drain state row with id=1."""
        from sqlalchemy import select
        from sqlalchemy.dialects.sqlite import insert

        # Atomic insert ... on conflict do nothing to guarantee singleton row existence
        stmt = (
            insert(cls).values(id=1, drain_counter=0).on_conflict_do_nothing(index_elements=["id"])
        )
        await session.execute(stmt)

        res = await session.execute(select(cls).where(cls.id == 1))
        state = res.scalar_one()
        return state

    def __repr__(self) -> str:
        return f"<SystemDrainState(id={self.id}, drain_counter={self.drain_counter})>"
