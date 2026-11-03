"""SQLAlchemy model for cached semantic job embeddings."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utc_now


class JobEmbedding(Base):
    """Cached semantic vector embedding for a normalized job."""

    __tablename__ = "job_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", "model_version", name="uq_job_embeddings_job_model"),
        Index("ix_job_embeddings_lookup", "job_id", "content_hash", "model_version"),
    )

    def to_vector(self) -> list[float]:
        """Return the deserialized float vector."""
        return list(self.embedding)

    def __repr__(self) -> str:
        return (
            f"<JobEmbedding(id={self.id}, job_id={self.job_id}, "
            f"model_version='{self.model_version}')>"
        )
