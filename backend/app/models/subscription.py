"""UserCompanySubscription SQLAlchemy model mapping users to followed companies."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.user import User


class UserCompanySubscription(Base):
    """Many-to-many subscription linking a user to a monitored company."""

    __tablename__ = "user_company_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="subscriptions", lazy="selectin")
    company: Mapped["Company"] = relationship(
        "Company", back_populates="subscribers", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company_subscription"),
        Index("ix_subscriptions_user_company", "user_id", "company_id"),
    )

    def __repr__(self) -> str:
        return f"<UserCompanySubscription(user_id={self.user_id}, company_id={self.company_id})>"
