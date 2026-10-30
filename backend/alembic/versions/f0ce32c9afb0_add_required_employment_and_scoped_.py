"""add_required_employment_and_scoped_exclusions

Revision ID: f0ce32c9afb0
Revises: 4b1cf0525482
Create Date: 2026-10-31 00:58:08.581227

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = "f0ce32c9afb0"
down_revision: str | None = "4b1cf0525482"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "required_employment_types",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "excluded_title_keywords",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "excluded_content_keywords",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.drop_column("excluded_content_keywords")
        batch_op.drop_column("excluded_title_keywords")
        batch_op.drop_column("required_employment_types")
