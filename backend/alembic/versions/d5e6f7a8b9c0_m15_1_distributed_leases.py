"""m15_1_distributed_leases

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create distributed_leases table
    op.create_table(
        "distributed_leases",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    with op.batch_alter_table("distributed_leases", schema=None) as batch_op:
        batch_op.create_index("ix_distributed_leases_expires_at", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("distributed_leases")
