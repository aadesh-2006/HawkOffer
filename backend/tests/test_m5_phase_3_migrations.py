"""Comprehensive migration tests for M5 Phase 3 schema (revision 8de4342be7a1)."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


def test_m5_phase_3_full_migration_lifecycle(tmp_path: Path) -> None:
    """Test full upgrade from base to head, constraint checks, and downgrade to base."""
    db_path = tmp_path / "test_migration_m5_phase_3.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    backend_dir = Path(__file__).resolve().parent.parent
    ini_path = backend_dir / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    # 1. Upgrade to pre-Phase-3 head (69f31040e774)
    command.upgrade(cfg, "69f31040e774")

    # 2. Insert representative records in previous schema (including a PreferenceProfile)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute(
        "INSERT INTO companies (name, career_url, platform, is_active, created_at, updated_at) "
        "VALUES ('Alpha Co', 'https://alpha.com', 'greenhouse', 1, "
        "'2026-10-31 00:00:00', '2026-10-31 00:00:00')"
    )
    company_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO jobs (company_id, title, application_url, fingerprint_hash, content_hash, "
        "status, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (?, 'Software Engineer', 'https://alpha.com/job1', 'fp101', 'ch101', 'NEW', "
        "'2026-10-31 00:00:00', '2026-10-31 00:00:00', "
        "'2026-10-31 00:00:00', '2026-10-31 00:00:00')",
        (company_id,),
    )
    job_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO preference_profiles (name, is_active, minimum_match_score, "
        "preferred_roles, preferred_locations, preferred_skills, "
        "preferred_employment_types, required_employment_types, "
        "preferred_workplace_types, excluded_keywords, excluded_title_keywords, "
        "excluded_content_keywords, required_roles, required_locations, "
        "require_graduation_year_match, created_at, updated_at) "
        "VALUES ('Legacy Profile', 1, 0.0, '[]', '[]', '[]', '[]', '[]', '[]', "
        "'[]', '[]', '[]', '[]', '[]', 0, '2026-10-31 00:00:00', '2026-10-31 00:00:00')"
    )
    profile_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # 3. Upgrade to latest head (8de4342be7a1)
    command.upgrade(cfg, "8de4342be7a1")

    # 4. Verify schema objects and existing data updates
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Check preference_profiles.revision populated as 1 for pre-existing row
    cursor.execute("SELECT id, name, revision FROM preference_profiles WHERE id = ?", (profile_id,))
    prof_row = cursor.fetchone()
    assert prof_row is not None
    assert prof_row[1] == "Legacy Profile"
    assert prof_row[2] == 1  # non-null default 1 populated!

    # Check inserting a new profile defaults revision to 1
    cursor.execute(
        "INSERT INTO preference_profiles (name, is_active, minimum_match_score, "
        "preferred_roles, preferred_locations, preferred_skills, "
        "preferred_employment_types, required_employment_types, "
        "preferred_workplace_types, excluded_keywords, excluded_title_keywords, "
        "excluded_content_keywords, required_roles, required_locations, "
        "require_graduation_year_match, created_at, updated_at) "
        "VALUES ('New Profile', 1, 0.0, '[]', '[]', '[]', '[]', '[]', '[]', "
        "'[]', '[]', '[]', '[]', '[]', 0, '2026-10-31 00:00:00', '2026-10-31 00:00:00')"
    )
    new_profile_id = cursor.lastrowid
    cursor.execute("SELECT revision FROM preference_profiles WHERE id = ?", (new_profile_id,))
    assert cursor.fetchone()[0] == 1

    # Check system_drain_state singleton seeded
    cursor.execute("SELECT id, drain_counter FROM system_drain_state")
    drain_rows = cursor.fetchall()
    assert len(drain_rows) == 1
    assert drain_rows[0][0] == 1
    assert drain_rows[0][1] == 0

    # 5. Check job_discovery_outbox insertion and uniqueness
    cursor.execute(
        "INSERT INTO job_discovery_outbox (job_id, status, attempt_count, created_at) "
        "VALUES (?, 'PENDING', 0, '2026-10-31 00:00:00')",
        (job_id,),
    )
    outbox_id = cursor.lastrowid
    assert outbox_id is not None

    # Duplicate job_id must fail UNIQUE constraint
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO job_discovery_outbox (job_id, status, attempt_count, created_at) "
            "VALUES (?, 'PENDING', 0, '2026-10-31 00:00:00')",
            (job_id,),
        )
    conn.rollback()

    # 6. Check notification_backlog insertion and (job_id, profile_id, profile_revision) uniqueness
    cursor.execute(
        "INSERT INTO notification_backlog "
        "(job_id, profile_id, profile_revision, score, status, attempt_count, created_at) "
        "VALUES (?, ?, 1, 85, 'QUEUED', 0, '2026-10-31 00:00:00')",
        (job_id, profile_id),
    )
    backlog_id = cursor.lastrowid
    assert backlog_id is not None

    # Duplicate (job_id, profile_id, profile_revision) must fail UNIQUE constraint
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO notification_backlog "
            "(job_id, profile_id, profile_revision, score, status, attempt_count, created_at) "
            "VALUES (?, ?, 1, 90, 'QUEUED', 0, '2026-10-31 00:00:00')",
            (job_id, profile_id),
        )
    conn.rollback()

    # Different profile_revision is permitted
    cursor.execute(
        "INSERT INTO notification_backlog "
        "(job_id, profile_id, profile_revision, score, status, attempt_count, created_at) "
        "VALUES (?, ?, 2, 90, 'QUEUED', 0, '2026-10-31 00:00:00')",
        (job_id, profile_id),
    )
    assert cursor.lastrowid is not None

    # 7. Check profile_reevaluation_tasks insertion and partial unique index on active states
    cursor.execute(
        "INSERT INTO profile_reevaluation_tasks "
        "(profile_id, profile_revision, max_job_id, last_processed_job_id, status, "
        "attempt_count, created_at) "
        "VALUES (?, 1, 100, 0, 'PENDING', 0, '2026-10-31 00:00:00')",
        (profile_id,),
    )
    task1_id = cursor.lastrowid
    assert task1_id is not None

    # Adding another RUNNING task for same profile must violate uq_active_task_per_profile
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO profile_reevaluation_tasks "
            "(profile_id, profile_revision, max_job_id, last_processed_job_id, status, "
            "attempt_count, created_at) "
            "VALUES (?, 1, 150, 0, 'RUNNING', 0, '2026-10-31 00:00:00')",
            (profile_id,),
        )
    conn.rollback()

    # Once task1 is updated to COMPLETED, adding a new PENDING task succeeds
    cursor.execute(
        "UPDATE profile_reevaluation_tasks SET status = 'COMPLETED' WHERE id = ?", (task1_id,)
    )
    cursor.execute(
        "INSERT INTO profile_reevaluation_tasks "
        "(profile_id, profile_revision, max_job_id, last_processed_job_id, status, "
        "attempt_count, created_at) "
        "VALUES (?, 2, 200, 0, 'PENDING', 0, '2026-10-31 00:00:00')",
        (profile_id,),
    )
    assert cursor.lastrowid is not None

    # 8. Check Foreign Key Cascades
    # Deleting job cascades to job_discovery_outbox and notification_backlog
    cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    cursor.execute("SELECT id FROM job_discovery_outbox WHERE job_id = ?", (job_id,))
    assert cursor.fetchone() is None
    cursor.execute("SELECT id FROM notification_backlog WHERE job_id = ?", (job_id,))
    assert cursor.fetchone() is None

    # Deleting profile cascades to profile_reevaluation_tasks
    cursor.execute("DELETE FROM preference_profiles WHERE id = ?", (profile_id,))
    cursor.execute("SELECT id FROM profile_reevaluation_tasks WHERE profile_id = ?", (profile_id,))
    assert cursor.fetchone() is None

    conn.close()

    # 9. Downgrade from head all the way back to base
    command.downgrade(cfg, "base")

    # Confirm tables are removed
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "job_discovery_outbox" not in tables
    assert "notification_backlog" not in tables
    assert "profile_reevaluation_tasks" not in tables
    assert "system_drain_state" not in tables
    assert "jobs" not in tables
    conn.close()

    # 10. Re-upgrade from base all the way back to head
    command.upgrade(cfg, "8de4342be7a1")

    # Confirm tables exist again and singleton row is seeded
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    recreated_tables = {row[0] for row in cursor.fetchall()}
    assert "job_discovery_outbox" in recreated_tables
    assert "notification_backlog" in recreated_tables
    assert "profile_reevaluation_tasks" in recreated_tables
    assert "system_drain_state" in recreated_tables
    assert "jobs" in recreated_tables

    cursor.execute("SELECT id, drain_counter FROM system_drain_state")
    recreated_drain = cursor.fetchall()
    assert len(recreated_drain) == 1
    assert recreated_drain[0][0] == 1
    assert recreated_drain[0][1] == 0
    conn.close()
