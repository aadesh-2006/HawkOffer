"""add_preference_profile_filter_fields

Revision ID: 4b1cf0525482
Revises: 46bc793a66c2
Create Date: 2026-10-31 00:38:09.825281

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = "4b1cf0525482"
down_revision: str | None = "46bc793a66c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "preferred_employment_types",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "preferred_workplace_types",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "excluded_keywords",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "required_roles",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "required_locations",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "require_graduation_year_match",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.drop_column("require_graduation_year_match")
        batch_op.drop_column("required_locations")
        batch_op.drop_column("required_roles")
        batch_op.drop_column("excluded_keywords")
        batch_op.drop_column("preferred_workplace_types")
        batch_op.drop_column("preferred_employment_types")
