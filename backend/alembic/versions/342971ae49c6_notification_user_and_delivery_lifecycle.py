"""notification_user_and_delivery_lifecycle

Revision ID: 342971ae49c6
Revises: f0ce32c9afb0
Create Date: 2026-10-31 01:35:26.597544

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "342971ae49c6"
down_revision: str | None = "f0ce32c9afb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add delivery_status and timestamp columns, plus supporting indexes
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_status",
                sa.String(length=50),
                nullable=False,
                server_default=sa.text("'PENDING'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "read_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "dismissed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_notifications_delivery_status",
            ["delivery_status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notifications_read_at",
            ["read_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notifications_dismissed_at",
            ["dismissed_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notifications_profile_status_created",
            ["profile_id", "status", "created_at"],
            unique=False,
        )

    # 2. Data migration on existing legacy records
    # Map legacy status values into delivery_status:
    # - 'DELIVERED' -> 'DELIVERED'
    # - 'FAILED' -> 'FAILED'
    # - 'DISMISSED' -> 'DELIVERED' if delivered_at IS NOT NULL else 'NOT_REQUIRED'
    # - Others -> 'PENDING'
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET delivery_status = CASE
                WHEN status = 'DELIVERED' THEN 'DELIVERED'
                WHEN status = 'FAILED' THEN 'FAILED'
                WHEN status = 'DISMISSED' AND delivered_at IS NOT NULL THEN 'DELIVERED'
                WHEN status = 'DISMISSED' AND delivered_at IS NULL THEN 'NOT_REQUIRED'
                ELSE 'PENDING'
            END
            """
        )
    )

    # Set dismissed_at for legacy dismissed records
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET dismissed_at = created_at
            WHERE status = 'DISMISSED'
            """
        )
    )

    # Map user-facing status:
    # - DISMISSED stays DISMISSED
    # - All other legacy records (PENDING, DELIVERED, FAILED) become UNREAD
    # (Never map legacy DELIVERED directly to READ!)
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = CASE
                WHEN status = 'DISMISSED' THEN 'DISMISSED'
                ELSE 'UNREAD'
            END
            """
        )
    )


def downgrade() -> None:
    # Revert user status and delivery status back into legacy single status column
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = CASE
                WHEN status = 'DISMISSED' THEN 'DISMISSED'
                WHEN delivery_status = 'DELIVERED' THEN 'DELIVERED'
                WHEN delivery_status = 'FAILED' THEN 'FAILED'
                ELSE 'PENDING'
            END
            """
        )
    )

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_notifications_profile_status_created")
        batch_op.drop_index("ix_notifications_dismissed_at")
        batch_op.drop_index("ix_notifications_read_at")
        batch_op.drop_index("ix_notifications_delivery_status")
        batch_op.drop_column("dismissed_at")
        batch_op.drop_column("read_at")
        batch_op.drop_column("delivery_status")
