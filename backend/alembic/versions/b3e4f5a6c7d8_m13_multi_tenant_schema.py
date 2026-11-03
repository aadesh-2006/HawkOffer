"""m13_multi_tenant_schema

Revision ID: b3e4f5a6c7d8
Revises: 9a1c5d7e8b2f
Create Date: 2026-11-03 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e4f5a6c7d8"
down_revision: str | None = "9a1c5d7e8b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=50), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("verification_token", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index("ix_users_email", ["email"], unique=True)
        batch_op.create_index("ix_users_is_active", ["is_active"], unique=False)
        batch_op.create_index("ix_users_role", ["role"], unique=False)

    # 2. Create user_company_subscriptions table
    op.create_table(
        "user_company_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "company_id", name="uq_user_company_subscription"),
    )
    with op.batch_alter_table("user_company_subscriptions", schema=None) as batch_op:
        batch_op.create_index("ix_subscriptions_user_id", ["user_id"], unique=False)
        batch_op.create_index("ix_subscriptions_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_subscriptions_user_company", ["user_id", "company_id"], unique=False
        )

    # 3. Create job_applications table
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="SAVED", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job_application"),
    )
    with op.batch_alter_table("job_applications", schema=None) as batch_op:
        batch_op.create_index("ix_job_applications_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_job_applications_user_status", ["user_id", "status"], unique=False
        )

    # 4. Create push_subscriptions table
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    with op.batch_alter_table("push_subscriptions", schema=None) as batch_op:
        batch_op.create_index("ix_push_subscriptions_user_id", ["user_id"], unique=False)

    # 5. Enrich companies table
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_curated", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
        batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_companies_created_by_user", "users", ["created_by_user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_companies_is_curated", ["is_curated"], unique=False)
        batch_op.create_index("ix_companies_created_by_user_id", ["created_by_user_id"], unique=False)

    # 6. Enrich preference_profiles table
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_preference_profiles_user", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index("ix_preference_profiles_user_id", ["user_id"], unique=False)
        batch_op.create_index(
            "ix_preference_profiles_user_active", ["user_id", "is_active"], unique=False
        )

    # 7. Enrich notifications table
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_notifications_user", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index("ix_notifications_user_id", ["user_id"], unique=False)
        batch_op.create_index("ix_notifications_user_id_status", ["user_id", "status"], unique=False)
        batch_op.create_index("ix_notifications_user_created", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index("ix_notifications_user_created")
        batch_op.drop_index("ix_notifications_user_id_status")
        batch_op.drop_index("ix_notifications_user_id")
        batch_op.drop_constraint("fk_notifications_user", type_="foreignkey")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_preference_profiles_user_active")
        batch_op.drop_index("ix_preference_profiles_user_id")
        batch_op.drop_constraint("fk_preference_profiles_user", type_="foreignkey")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_index("ix_companies_created_by_user_id")
        batch_op.drop_index("ix_companies_is_curated")
        batch_op.drop_constraint("fk_companies_created_by_user", type_="foreignkey")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("is_curated")

    op.drop_table("push_subscriptions")
    op.drop_table("job_applications")
    op.drop_table("user_company_subscriptions")
    op.drop_table("users")
