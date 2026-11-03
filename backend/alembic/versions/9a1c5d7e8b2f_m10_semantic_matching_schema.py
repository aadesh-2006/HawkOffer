"""m10_semantic_matching_schema

Revision ID: 9a1c5d7e8b2f
Revises: 8de4342be7a1
Create Date: 2026-11-03 19:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a1c5d7e8b2f"
down_revision: str | None = "8de4342be7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update preference_profiles: add resume_text and semantic_enabled
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resume_text", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "semantic_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    # 2. Create job_embeddings table
    op.create_table(
        "job_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sqlite.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "model_version", name="uq_job_embeddings_job_model"),
    )
    with op.batch_alter_table("job_embeddings", schema=None) as batch_op:
        batch_op.create_index("ix_job_embeddings_job_id", ["job_id"], unique=False)
        batch_op.create_index(
            "ix_job_embeddings_lookup",
            ["job_id", "content_hash", "model_version"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("job_embeddings")

    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.drop_column("semantic_enabled")
        batch_op.drop_column("resume_text")
