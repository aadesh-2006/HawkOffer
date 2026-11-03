"""PushSubscription SQLAlchemy model storing RFC 8291 Web Push credentials."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.user import User


class PushSubscription(Base):
    """Browser push subscription endpoint and cryptographic keys for Web Push."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User", back_populates="push_subscriptions", lazy="selectin"
    )

    __table_args__ = (Index("ix_push_subscriptions_user_id", "user_id"),)

    def __repr__(self) -> str:
        return (
            f"<PushSubscription(id={self.id}, user_id={self.user_id}, "
            f"failure_count={self.failure_count})>"
        )
