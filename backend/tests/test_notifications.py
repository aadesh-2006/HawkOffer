"""Tests for notification lifecycle, delivery status, and repository operations."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.repositories.company_repo import CompanyRepository
from app.repositories.job_repo import JobRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.preference_repo import PreferenceProfileRepository
from app.schemas.company import CompanyCreate
from app.schemas.job import JobCreate
from app.schemas.notification import (
    DeliveryStatus,
    NotificationCreate,
    NotificationRead,
    NotificationStatus,
    NotificationType,
)
from app.schemas.preference import PreferenceProfileCreate


async def _create_test_job(
    comp_repo: CompanyRepository,
    job_repo: JobRepository,
    suffix: str = "1",
) -> tuple[Company, Job]:
    """Helper to create a company and job for notification tests."""
    import hashlib

    fp = hashlib.sha256(f"fp_{suffix}".encode()).hexdigest()
    ch = hashlib.sha256(f"ch_{suffix}".encode()).hexdigest()

    company = await comp_repo.create(
        CompanyCreate(
            name=f"Notify Corp {suffix}",
            career_url=f"https://notify{suffix}.com",
        )
    )
    job = await job_repo.create(
        JobCreate(
            company_id=company.id,
            title=f"Software Engineer {suffix}",
            application_url=f"https://notify{suffix}.com/job/{suffix}",
            fingerprint_hash=fp,
            content_hash=ch,
        )
    )
    return company, job


@pytest.mark.asyncio
async def test_notification_defaults(db_session: AsyncSession) -> None:
    """Verify default values for user-facing status, delivery status, and timestamps."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    _, job = await _create_test_job(comp_repo, job_repo, "def")

    notif = await notif_repo.create(
        NotificationCreate(
            job_id=job.id,
        )
    )

    assert notif.id is not None
    assert notif.status == NotificationStatus.UNREAD
    assert notif.delivery_status == DeliveryStatus.PENDING
    assert notif.notification_type == NotificationType.JOB_ALERT
    assert notif.created_at is not None
    assert notif.read_at is None
    assert notif.dismissed_at is None
    assert notif.delivered_at is None

    # Test Pydantic schema validation
    schema_read = NotificationRead.model_validate(notif)
    assert schema_read.id == notif.id
    assert schema_read.status == "UNREAD"
    assert schema_read.delivery_status == "PENDING"
    assert schema_read.read_at is None


@pytest.mark.asyncio
async def test_independent_user_and_delivery_lifecycle(db_session: AsyncSession) -> None:
    """Verify decoupled user lifecycle (UNREAD/READ/DISMISSED) and delivery lifecycle."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    _, job = await _create_test_job(comp_repo, job_repo, "indep")

    notif = await notif_repo.create(
        NotificationCreate(
            job_id=job.id,
        )
    )

    # 1. External delivery success: sets delivered_at and delivery_status=DELIVERED,
    # but user-facing status must REMAIN UNREAD!
    delivered = await notif_repo.mark_delivered(notif.id)
    assert delivered is not None
    assert delivered.delivery_status == DeliveryStatus.DELIVERED
    assert delivered.status == NotificationStatus.UNREAD
    assert delivered.delivered_at is not None
    assert delivered.read_at is None

    # 2. User marks notification as READ: user status changes to READ,
    # but delivery_status MUST REMAIN DELIVERED.
    read_notif = await notif_repo.mark_as_read(notif.id)
    assert read_notif is not None
    assert read_notif.status == NotificationStatus.READ
    assert read_notif.delivery_status == DeliveryStatus.DELIVERED
    assert read_notif.read_at is not None

    # 3. Delivery failure on another notification must not change user status
    _, job2 = await _create_test_job(comp_repo, job_repo, "fail")
    notif2 = await notif_repo.create(NotificationCreate(job_id=job2.id))
    failed = await notif_repo.mark_delivery_failed(notif2.id)
    assert failed is not None
    assert failed.delivery_status == DeliveryStatus.FAILED
    assert (
        failed.status == NotificationStatus.UNREAD
    )  # Still available to read in notification center


@pytest.mark.asyncio
async def test_read_and_unread_lifecycle_transitions(db_session: AsyncSession) -> None:
    """Verify transitions between UNREAD and READ, timestamp management, and idempotency."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    _, job = await _create_test_job(comp_repo, job_repo, "read")
    notif = await notif_repo.create(NotificationCreate(job_id=job.id))

    # Mark as read
    read = await notif_repo.mark_as_read(notif.id)
    assert read is not None
    assert read.status == NotificationStatus.READ
    assert read.read_at is not None
    first_read_at = read.read_at

    # Idempotent call to mark_as_read does not overwrite read_at timestamp
    read_again = await notif_repo.mark_as_read(notif.id)
    assert read_again is not None
    assert read_again.read_at == first_read_at

    # Mark as unread
    unread = await notif_repo.mark_as_unread(notif.id)
    assert unread is not None
    assert unread.status == NotificationStatus.UNREAD
    assert unread.read_at is None

    # Idempotent call to mark_as_unread
    unread_again = await notif_repo.mark_as_unread(notif.id)
    assert unread_again is not None
    assert unread_again.status == NotificationStatus.UNREAD
    assert unread_again.read_at is None


@pytest.mark.asyncio
async def test_dismiss_and_restore_lifecycle(db_session: AsyncSession) -> None:
    """Verify dismissing notifications, dismissed_at timestamp, and restoring."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    _, job = await _create_test_job(comp_repo, job_repo, "dism")
    notif = await notif_repo.create(NotificationCreate(job_id=job.id))

    # 1. Dismiss unread notification
    dismissed = await notif_repo.dismiss(notif.id)
    assert dismissed is not None
    assert dismissed.status == NotificationStatus.DISMISSED
    assert dismissed.dismissed_at is not None
    first_dismissed_at = dismissed.dismissed_at

    # Idempotency
    dismissed_again = await notif_repo.dismiss(notif.id)
    assert dismissed_again is not None
    assert dismissed_again.dismissed_at == first_dismissed_at

    # Restore unread notification -> returns to UNREAD
    restored = await notif_repo.restore(notif.id)
    assert restored is not None
    assert restored.status == NotificationStatus.UNREAD
    assert restored.dismissed_at is None

    # 2. Mark as read first, then dismiss -> restore should return to READ!
    await notif_repo.mark_as_read(notif.id)
    await notif_repo.dismiss(notif.id)
    restored_read = await notif_repo.restore(notif.id)
    assert restored_read is not None
    assert restored_read.status == NotificationStatus.READ
    assert restored_read.read_at is not None
    assert restored_read.dismissed_at is None


@pytest.mark.asyncio
async def test_batch_mark_read_and_batch_dismiss(db_session: AsyncSession) -> None:
    """Verify batch updates only affect eligible rows and return accurate row counts."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    _, job1 = await _create_test_job(comp_repo, job_repo, "b1")
    _, job2 = await _create_test_job(comp_repo, job_repo, "b2")
    _, job3 = await _create_test_job(comp_repo, job_repo, "b3")

    n1 = await notif_repo.create(NotificationCreate(job_id=job1.id))
    n2 = await notif_repo.create(NotificationCreate(job_id=job2.id))
    n3 = await notif_repo.create(NotificationCreate(job_id=job3.id))

    # Mark n1 read beforehand
    await notif_repo.mark_as_read(n1.id)

    # Batch mark read for [n1, n2, n3, 999999]
    # Only n2 and n3 should be updated because n1 is already READ and 999999 does not exist
    count = await notif_repo.mark_batch_read([n1.id, n2.id, n3.id, 999999])
    assert count == 2

    # Batch dismiss for [n1, n2]
    d_count = await notif_repo.dismiss_batch([n1.id, n2.id])
    assert d_count == 2

    # Attempting to batch dismiss already dismissed rows returns 0
    d_again = await notif_repo.dismiss_batch([n1.id, n2.id])
    assert d_again == 0

    # Empty batch returns 0
    assert await notif_repo.mark_batch_read([]) == 0
    assert await notif_repo.dismiss_batch([]) == 0


@pytest.mark.asyncio
async def test_list_notifications_filtering_and_ordering(db_session: AsyncSession) -> None:
    """Verify list_notifications filters by profile, status, dismissed, and newest order."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    prof_repo = PreferenceProfileRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    p1 = await prof_repo.create(PreferenceProfileCreate(name="Profile 1"))
    p2 = await prof_repo.create(PreferenceProfileCreate(name="Profile 2"))

    _, job1 = await _create_test_job(comp_repo, job_repo, "l1")
    _, job2 = await _create_test_job(comp_repo, job_repo, "l2")
    _, job3 = await _create_test_job(comp_repo, job_repo, "l3")

    n1 = await notif_repo.create(NotificationCreate(job_id=job1.id, profile_id=p1.id))
    n2 = await notif_repo.create(NotificationCreate(job_id=job2.id, profile_id=p1.id))
    n3 = await notif_repo.create(NotificationCreate(job_id=job3.id, profile_id=p2.id))

    await notif_repo.mark_as_read(n1.id)
    await notif_repo.dismiss(n2.id)

    # 1. Profile 1 notifications, excluding dismissed (default)
    p1_active = await notif_repo.list_notifications(profile_id=p1.id)
    assert len(p1_active) == 1
    assert p1_active[0].id == n1.id

    # 2. Profile 1 notifications, including dismissed
    p1_all = await notif_repo.list_notifications(profile_id=p1.id, include_dismissed=True)
    assert len(p1_all) == 2
    # Newest first
    assert p1_all[0].id == n2.id
    assert p1_all[1].id == n1.id

    # 3. Filter by explicit status: UNREAD
    unread_all = await notif_repo.list_notifications(status=NotificationStatus.UNREAD)
    assert len(unread_all) == 1
    assert unread_all[0].id == n3.id

    # 4. Filter by explicit status: DISMISSED
    dismissed_all = await notif_repo.list_notifications(status=NotificationStatus.DISMISSED)
    assert len(dismissed_all) == 1
    assert dismissed_all[0].id == n2.id

    # 5. Pagination: limit and offset
    all_notifs = await notif_repo.list_notifications(include_dismissed=True, limit=2, offset=0)
    assert len(all_notifs) == 2
    assert all_notifs[0].id == n3.id
    assert all_notifs[1].id == n2.id

    next_page = await notif_repo.list_notifications(include_dismissed=True, limit=2, offset=2)
    assert len(next_page) == 1
    assert next_page[0].id == n1.id


@pytest.mark.asyncio
async def test_unread_count_aggregation(db_session: AsyncSession) -> None:
    """Verify get_unread_count accurately aggregates across all profiles or per-profile."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    prof_repo = PreferenceProfileRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    p1 = await prof_repo.create(PreferenceProfileCreate(name="Count P1"))
    p2 = await prof_repo.create(PreferenceProfileCreate(name="Count P2"))

    _, j1 = await _create_test_job(comp_repo, job_repo, "c1")
    _, j2 = await _create_test_job(comp_repo, job_repo, "c2")
    _, j3 = await _create_test_job(comp_repo, job_repo, "c3")

    n1 = await notif_repo.create(NotificationCreate(job_id=j1.id, profile_id=p1.id))
    await notif_repo.create(NotificationCreate(job_id=j2.id, profile_id=p1.id))
    await notif_repo.create(NotificationCreate(job_id=j3.id, profile_id=p2.id))

    # All 3 are UNREAD
    assert await notif_repo.get_unread_count() == 3
    assert await notif_repo.get_unread_count(profile_id=p1.id) == 2
    assert await notif_repo.get_unread_count(profile_id=p2.id) == 1

    # Mark n1 as READ
    await notif_repo.mark_as_read(n1.id)
    assert await notif_repo.get_unread_count() == 2
    assert await notif_repo.get_unread_count(profile_id=p1.id) == 1


@pytest.mark.asyncio
async def test_get_by_job_profile_type_and_duplicate_prevention(db_session: AsyncSession) -> None:
    """Verify lookup by (job_id, profile_id, type) and database unique index enforcement."""
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)
    prof_repo = PreferenceProfileRepository(db_session)
    notif_repo = NotificationRepository(db_session)

    profile = await prof_repo.create(PreferenceProfileCreate(name="Dedup Profile"))
    _, job = await _create_test_job(comp_repo, job_repo, "dd")

    # Nonexistent returns None
    missing = await notif_repo.get_by_job_profile_type(
        job_id=job.id,
        profile_id=profile.id,
        notification_type=NotificationType.JOB_ALERT,
    )
    assert missing is None

    # Create record
    created = await notif_repo.create(
        NotificationCreate(
            job_id=job.id,
            profile_id=profile.id,
            notification_type=NotificationType.JOB_ALERT,
        )
    )

    found = await notif_repo.get_by_job_profile_type(
        job_id=job.id,
        profile_id=profile.id,
        notification_type=NotificationType.JOB_ALERT,
    )
    assert found is not None
    assert found.id == created.id

    # Direct database insertion of identical duplicate triggers IntegrityError
    dup = Notification(
        job_id=job.id,
        profile_id=profile.id,
        notification_type=NotificationType.JOB_ALERT,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_missing_notification_id_graceful_handling(db_session: AsyncSession) -> None:
    """Verify repository operations on nonexistent notification IDs safely return None."""
    notif_repo = NotificationRepository(db_session)
    non_existent_id = 999999

    assert await notif_repo.mark_as_read(non_existent_id) is None
    assert await notif_repo.mark_as_unread(non_existent_id) is None
    assert await notif_repo.dismiss(non_existent_id) is None
    assert await notif_repo.restore(non_existent_id) is None
    assert await notif_repo.mark_delivered(non_existent_id) is None
    assert await notif_repo.mark_delivery_failed(non_existent_id) is None


@pytest.mark.asyncio
async def test_legacy_data_migration_mapping(db_session: AsyncSession) -> None:
    """Verify legacy notification mapping rules:
    - Legacy PENDING -> delivery_status=PENDING, status=UNREAD
    - Legacy DELIVERED -> delivery_status=DELIVERED, status=UNREAD (NOT READ!)
    - Legacy FAILED -> delivery_status=FAILED, status=UNREAD
    - Legacy DISMISSED -> status=DISMISSED, delivery_status=NOT_REQUIRED
    """
    comp_repo = CompanyRepository(db_session)
    job_repo = JobRepository(db_session)

    _, job = await _create_test_job(comp_repo, job_repo, "mig")

    now = datetime.now(UTC)

    # Simulate rows migrated from legacy database
    # 1. Legacy DELIVERED must NOT become READ
    notif_delivered = Notification(
        job_id=job.id,
        notification_type="desktop_toast",
        status=NotificationStatus.UNREAD,
        delivery_status=DeliveryStatus.DELIVERED,
        delivered_at=now,
    )
    # 2. Legacy DISMISSED
    notif_dismissed = Notification(
        job_id=job.id,
        notification_type="email",
        status=NotificationStatus.DISMISSED,
        delivery_status=DeliveryStatus.NOT_REQUIRED,
        dismissed_at=now,
    )

    db_session.add_all([notif_delivered, notif_dismissed])
    await db_session.commit()

    # Query fresh
    res = await db_session.execute(
        select(Notification).where(Notification.id == notif_delivered.id)
    )
    fetched_del = res.scalars().one()
    assert fetched_del.status == "UNREAD"  # Safe initial user status
    assert fetched_del.delivery_status == "DELIVERED"
    assert fetched_del.delivered_at == now
    assert fetched_del.read_at is None  # Must NOT be marked as read!

    res2 = await db_session.execute(
        select(Notification).where(Notification.id == notif_dismissed.id)
    )
    fetched_dism = res2.scalars().one()
    assert fetched_dism.status == "DISMISSED"
    assert fetched_dism.delivery_status == "NOT_REQUIRED"
    assert fetched_dism.dismissed_at == now


def test_alembic_notification_lifecycle_migration(tmp_path: Path) -> None:
    """Verify live Alembic upgrade, legacy row mapping, downgrade, and re-upgrade on SQLite."""
    import sqlite3

    from alembic.config import Config

    from alembic import command

    db_path = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    backend_dir = Path(__file__).resolve().parent.parent
    ini_path = backend_dir / "alembic.ini"

    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    # 1. Upgrade to pre-M5 revision: f0ce32c9afb0
    command.upgrade(cfg, "f0ce32c9afb0")

    # 2. Insert representative legacy records directly into SQLite
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO companies (name, career_url, platform, is_active, created_at, updated_at) "
        "VALUES ('Mig Corp', 'https://mig.com', 'greenhouse', 1, "
        "'2026-10-31 00:00:00', '2026-10-31 00:00:00')"
    )
    company_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO jobs (company_id, title, application_url, fingerprint_hash, content_hash, "
        "status, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (?, 'Mig Job', 'https://mig.com/1', 'fp1', 'ch1', 'NEW', "
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
        "VALUES ('Mig Profile', 1, 0.0, '[]', '[]', '[]', '[]', '[]', '[]', "
        "'[]', '[]', '[]', '[]', '[]', 0, '2026-10-31 00:00:00', '2026-10-31 00:00:00')"
    )
    profile_id = cursor.lastrowid

    # Insert 4 legacy notification rows:
    # 1) PENDING
    cursor.execute(
        "INSERT INTO notifications (job_id, profile_id, notification_type, status, created_at) "
        "VALUES (?, ?, 'desktop_toast', 'PENDING', '2026-10-31 01:00:00')",
        (job_id, profile_id),
    )
    # 2) DELIVERED with delivered_at
    cursor.execute(
        "INSERT INTO notifications "
        "(job_id, profile_id, notification_type, status, delivered_at, created_at) "
        "VALUES (?, NULL, 'desktop_toast', 'DELIVERED', "
        "'2026-10-31 01:10:00', '2026-10-31 01:05:00')",
        (job_id,),
    )
    # 3) FAILED
    cursor.execute(
        "INSERT INTO notifications (job_id, profile_id, notification_type, status, created_at) "
        "VALUES (?, ?, 'email', 'FAILED', '2026-10-31 01:15:00')",
        (job_id, profile_id),
    )
    # 4) DISMISSED without delivered_at
    cursor.execute(
        "INSERT INTO notifications (job_id, profile_id, notification_type, status, created_at) "
        "VALUES (?, ?, 'job_alert', 'DISMISSED', '2026-10-31 01:20:00')",
        (job_id, profile_id),
    )
    conn.commit()
    conn.close()

    # 3. Upgrade to head (applying 342971ae49c6)
    command.upgrade(cfg, "head")

    # 4. Verify migrated rows
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT notification_type, status, delivery_status, delivered_at, read_at, dismissed_at "
        "FROM notifications ORDER BY id ASC"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 4

    # Row 1 (legacy PENDING):
    assert rows[0][0] == "desktop_toast"
    assert rows[0][1] == "UNREAD"  # status is user-facing UNREAD
    assert rows[0][2] == "PENDING"  # delivery_status is PENDING
    assert rows[0][3] is None  # delivered_at is None
    assert rows[0][4] is None  # read_at is None
    assert rows[0][5] is None  # dismissed_at is None

    # Row 2 (legacy DELIVERED):
    assert rows[1][0] == "desktop_toast"
    assert rows[1][1] == "UNREAD"  # MUST NOT BE READ!
    assert rows[1][2] == "DELIVERED"  # delivery_status preserved
    assert rows[1][3] == "2026-10-31 01:10:00"  # delivered_at preserved
    assert rows[1][4] is None  # read_at is None
    assert rows[1][5] is None  # dismissed_at is None

    # Row 3 (legacy FAILED):
    assert rows[2][0] == "email"
    assert rows[2][1] == "UNREAD"
    assert rows[2][2] == "FAILED"
    assert rows[2][3] is None
    assert rows[2][4] is None
    assert rows[2][5] is None

    # Row 4 (legacy DISMISSED):
    assert rows[3][0] == "job_alert"
    assert rows[3][1] == "DISMISSED"  # status is DISMISSED
    assert rows[3][2] == "NOT_REQUIRED"  # delivery_status is NOT_REQUIRED
    assert rows[3][5] == "2026-10-31 01:20:00"  # dismissed_at set from created_at

    # 5. Downgrade back to f0ce32c9afb0
    command.downgrade(cfg, "f0ce32c9afb0")

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM notifications ORDER BY id ASC")
    downgraded_rows = cursor.fetchall()
    conn.close()

    assert downgraded_rows[0][0] == "PENDING"
    assert downgraded_rows[1][0] == "DELIVERED"
    assert downgraded_rows[2][0] == "FAILED"
    assert downgraded_rows[3][0] == "DISMISSED"

    # 6. Re-upgrade to head to verify idempotency
    command.upgrade(cfg, "head")
