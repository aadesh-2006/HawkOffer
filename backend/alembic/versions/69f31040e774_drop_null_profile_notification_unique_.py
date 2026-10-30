"""drop_null_profile_notification_unique_index

Revision ID: 69f31040e774
Revises: 342971ae49c6
Create Date: 2026-10-31 01:55:26.422341

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "69f31040e774"
down_revision: str | None = "342971ae49c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop obsolete null profile partial unique index to allow multiple notifications
    # for the same job from different profiles to survive deletion when ON DELETE SET NULL cascades.
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_notifications_job_null_profile_type",
            sqlite_where=sa.text("profile_id IS NULL"),
            postgresql_where=sa.text("profile_id IS NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            "uq_notifications_job_null_profile_type",
            ["job_id", "notification_type"],
            unique=True,
            sqlite_where=sa.text("profile_id IS NULL"),
            postgresql_where=sa.text("profile_id IS NULL"),
        )
