"""m16_web_push_delivery

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-04 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add delivery tracking columns to notifications table
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_attempts",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("next_delivery_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("delivery_lease_token", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("delivery_lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_delivery_error", sa.Text(), nullable=True)
        )
        batch_op.create_index(
            "ix_notifications_delivery_claim",
            ["delivery_status", "next_delivery_attempt_at", "delivery_lease_expires_at"],
            unique=False,
        )

    # 2. Create notification_push_deliveries table
    op.create_table(
        "notification_push_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["push_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id", "subscription_id", name="uq_push_delivery_notif_sub"
        ),
    )
    with op.batch_alter_table("notification_push_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notification_push_deliveries_notification_id",
            ["notification_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notification_push_deliveries_subscription_id",
            ["subscription_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("notification_push_deliveries")

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_notifications_delivery_claim")
        batch_op.drop_column("last_delivery_error")
        batch_op.drop_column("delivery_lease_expires_at")
        batch_op.drop_column("delivery_lease_token")
        batch_op.drop_column("next_delivery_attempt_at")
        batch_op.drop_column("delivery_attempts")
