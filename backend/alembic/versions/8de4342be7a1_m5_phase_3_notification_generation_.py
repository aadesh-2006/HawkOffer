"""m5_phase_3_notification_generation_schema

Revision ID: 8de4342be7a1
Revises: 69f31040e774
Create Date: 2026-10-31 12:04:35.637318

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8de4342be7a1"
down_revision: str | None = "69f31040e774"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update preference_profiles: add revision non-null integer column defaulting to 1
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    # 2. Create job_discovery_outbox table
    op.create_table(
        "job_discovery_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    with op.batch_alter_table("job_discovery_outbox", schema=None) as batch_op:
        batch_op.create_index(
            "ix_job_discovery_outbox_job_id",
            ["job_id"],
            unique=True,
        )
        batch_op.create_index(
            "ix_job_discovery_outbox_status",
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_job_discovery_outbox_lease_token",
            ["lease_token"],
            unique=False,
        )
        batch_op.create_index(
            "ix_outbox_claim_pending",
            ["status", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'PENDING'"),
            postgresql_where=sa.text("status = 'PENDING'"),
        )
        batch_op.create_index(
            "ix_outbox_claim_expired",
            ["status", "lease_expires_at", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'PROCESSING'"),
            postgresql_where=sa.text("status = 'PROCESSING'"),
        )

    # 3. Create notification_backlog table
    op.create_table(
        "notification_backlog",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["preference_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "profile_id", "profile_revision", name="uq_backlog_job_profile_rev"
        ),
    )
    with op.batch_alter_table("notification_backlog", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notification_backlog_job_id",
            ["job_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notification_backlog_profile_id",
            ["profile_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notification_backlog_status",
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_notification_backlog_lease_token",
            ["lease_token"],
            unique=False,
        )
        # Partial ordered indexes for active QUEUED candidates
        batch_op.create_index(
            "ix_backlog_queued_relevance",
            [sa.desc("score"), "created_at", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'QUEUED'"),
            postgresql_where=sa.text("status = 'QUEUED'"),
        )
        batch_op.create_index(
            "ix_backlog_queued_fifo",
            ["created_at", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'QUEUED'"),
            postgresql_where=sa.text("status = 'QUEUED'"),
        )
        # Partial ordered indexes for PROCESSING crash recovery
        batch_op.create_index(
            "ix_backlog_processing_relevance",
            [sa.desc("score"), "created_at", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'PROCESSING'"),
            postgresql_where=sa.text("status = 'PROCESSING'"),
        )
        batch_op.create_index(
            "ix_backlog_processing_fifo",
            ["created_at", "id"],
            unique=False,
            sqlite_where=sa.text("status = 'PROCESSING'"),
            postgresql_where=sa.text("status = 'PROCESSING'"),
        )

    # 4. Create profile_reevaluation_tasks table
    op.create_table(
        "profile_reevaluation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("max_job_id", sa.Integer(), nullable=False),
        sa.Column("last_processed_job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["preference_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("profile_reevaluation_tasks", schema=None) as batch_op:
        batch_op.create_index(
            "ix_profile_reevaluation_tasks_profile_id",
            ["profile_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_profile_reevaluation_tasks_status",
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            "uq_active_task_per_profile",
            ["profile_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('PENDING', 'RUNNING')"),
            postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
        )

    # 5. Create system_drain_state table and seed singleton row (id=1, drain_counter=0)
    op.create_table(
        "system_drain_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drain_counter", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Idempotent seed insert for singleton row id=1
    op.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO system_drain_state (id, drain_counter, updated_at)
            VALUES (1, 0, CURRENT_TIMESTAMP)
            """
        )
    )


def downgrade() -> None:
    # 1. Drop system_drain_state
    op.drop_table("system_drain_state")

    # 2. Drop profile_reevaluation_tasks
    op.drop_table("profile_reevaluation_tasks")

    # 3. Drop notification_backlog
    op.drop_table("notification_backlog")

    # 4. Drop job_discovery_outbox
    op.drop_table("job_discovery_outbox")

    # 5. Drop revision column from preference_profiles
    with op.batch_alter_table("preference_profiles", schema=None) as batch_op:
        batch_op.drop_column("revision")
