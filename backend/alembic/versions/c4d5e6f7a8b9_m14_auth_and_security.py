"""m14_auth_and_security

Revision ID: c4d5e6f7a8b9
Revises: b3e4f5a6c7d8
Create Date: 2026-11-03 23:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3e4f5a6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create refresh_tokens table
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_refresh_tokens_token_jti"),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index("ix_refresh_tokens_user_id", ["user_id"], unique=False)
        batch_op.create_index("ix_refresh_tokens_token_jti", ["token_jti"], unique=True)

    # 2. Add per-user notification deduplication unique index
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            "uq_notifications_job_user_type",
            ["job_id", "user_id", "notification_type"],
            unique=True,
            sqlite_where=sa.text("user_id IS NOT NULL"),
            postgresql_where=sa.text("user_id IS NOT NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index("uq_notifications_job_user_type")

    op.drop_table("refresh_tokens")
