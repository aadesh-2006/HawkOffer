"""Profile update service managing revision increments and reevaluation tasks."""

import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utc_now
from app.database.session import async_session_factory
from app.models.job import Job
from app.models.outbox import (
    BacklogStatus,
    NotificationBacklog,
    ProfileReevaluationTask,
    ReevaluationStatus,
)
from app.models.preference import PreferenceProfile
from app.schemas.preference import PreferenceProfileUpdate

logger = logging.getLogger(__name__)

FILTERING_PREFERENCE_FIELDS: set[str] = {
    "graduation_year",
    "preferred_roles",
    "preferred_locations",
    "preferred_skills",
    "preferred_employment_types",
    "required_employment_types",
    "preferred_workplace_types",
    "excluded_keywords",
    "excluded_title_keywords",
    "excluded_content_keywords",
    "required_roles",
    "required_locations",
    "require_graduation_year_match",
    "minimum_match_score",
}


def _is_value_different(current_val: Any, new_val: Any) -> bool:
    """Determine if a proposed preference field value differs from current value."""
    if isinstance(current_val, list) and isinstance(new_val, list):
        return bool(current_val != new_val)
    if isinstance(current_val, float) and isinstance(new_val, (int, float)):
        return bool(abs(float(current_val) - float(new_val)) > 1e-6)
    return bool(current_val != new_val)


class ProfileUpdateService:
    """Service that executes atomic preference profile updates.

    When filtering criteria change:
    1. Validates the requested update.
    2. Increments profile revision exactly once.
    3. Persists the updated profile fields.
    4. Invalidates obsolete QUEUED backlog candidates belonging to previous revisions (sets STALE).
    5. Supersedes any active reevaluation tasks for the profile (sets SUPERSEDED).
    6. Creates a new ProfileReevaluationTask bound to the new revision and frozen max_job_id.
    7. Performs all changes atomically in a single database transaction under immediate write lock.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory or async_session_factory

    async def update_profile(
        self,
        profile_id: int,
        update_data: PreferenceProfileUpdate | dict[str, Any],
    ) -> tuple[PreferenceProfile | None, ProfileReevaluationTask | None]:
        """Atomically update profile preferences, incrementing revision and enqueuing task."""
        if self._session is not None:
            return await self._update_profile_in_session(self._session, profile_id, update_data)

        async with self._session_factory() as session:
            return await self._update_profile_in_session(session, profile_id, update_data)

    async def _update_profile_in_session(
        self,
        session: AsyncSession,
        profile_id: int,
        update_data: PreferenceProfileUpdate | dict[str, Any],
    ) -> tuple[PreferenceProfile | None, ProfileReevaluationTask | None]:
        if not session.in_transaction():
            await session.execute(text("BEGIN IMMEDIATE"))

        try:
            # 1. Fetch profile under immediate write lock
            res = await session.execute(
                select(PreferenceProfile).where(PreferenceProfile.id == profile_id)
            )
            profile = res.scalar_one_or_none()
            if not profile:
                return None, None

            # Extract validated update fields
            if isinstance(update_data, BaseModel):
                data = update_data.model_dump(exclude_unset=True)
            elif isinstance(update_data, dict):
                # Validate against schema
                validated = PreferenceProfileUpdate(**update_data)
                data = validated.model_dump(exclude_unset=True)
            else:
                data = dict(update_data)

            if not data:
                # No fields specified; no-op
                return profile, None

            # 2. Check for meaningful preference changes
            has_filtering_change = False
            has_activation = False
            has_deactivation = False

            for field, new_val in data.items():
                if not hasattr(profile, field):
                    continue
                curr_val = getattr(profile, field)
                if field in FILTERING_PREFERENCE_FIELDS and _is_value_different(curr_val, new_val):
                    has_filtering_change = True
                elif field == "is_active":
                    if curr_val is False and new_val is True:
                        has_activation = True
                    elif curr_val is True and new_val is False:
                        has_deactivation = True

            # Case: Profile deactivated
            if has_deactivation:
                for field, new_val in data.items():
                    if hasattr(profile, field):
                        setattr(profile, field, new_val)

                # Invalidate queued backlog items
                await session.execute(
                    update(NotificationBacklog)
                    .where(
                        NotificationBacklog.profile_id == profile.id,
                        NotificationBacklog.status == BacklogStatus.QUEUED,
                    )
                    .values(status=BacklogStatus.STALE)
                )

                # Supersede active reevaluation tasks
                await session.execute(
                    update(ProfileReevaluationTask)
                    .where(
                        ProfileReevaluationTask.profile_id == profile.id,
                        ProfileReevaluationTask.status.in_(
                            [ReevaluationStatus.PENDING, ReevaluationStatus.RUNNING]
                        ),
                    )
                    .values(
                        status=ReevaluationStatus.SUPERSEDED,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )

                await session.commit()
                await session.refresh(profile)
                return profile, None

            # Case: Meaningful filtering change or reactivation
            is_meaningful = has_filtering_change or has_activation
            if not is_meaningful:
                # Only non-filtering fields changed (e.g. name) or values were identical
                has_any_change = False
                for field, new_val in data.items():
                    if hasattr(profile, field):
                        curr_val = getattr(profile, field)
                        if _is_value_different(curr_val, new_val):
                            setattr(profile, field, new_val)
                            has_any_change = True

                if has_any_change:
                    await session.commit()
                    await session.refresh(profile)
                return profile, None

            # 3. Meaningful update: Increment revision exactly once
            profile.revision += 1
            for field, new_val in data.items():
                if hasattr(profile, field):
                    setattr(profile, field, new_val)

            # 4. Invalidate obsolete queued backlog candidates
            await session.execute(
                update(NotificationBacklog)
                .where(
                    NotificationBacklog.profile_id == profile.id,
                    NotificationBacklog.status == BacklogStatus.QUEUED,
                )
                .values(status=BacklogStatus.STALE)
            )

            # 5. Supersede any active reevaluation task for that profile
            await session.execute(
                update(ProfileReevaluationTask)
                .where(
                    ProfileReevaluationTask.profile_id == profile.id,
                    ProfileReevaluationTask.status.in_(
                        [ReevaluationStatus.PENDING, ReevaluationStatus.RUNNING]
                    ),
                )
                .values(
                    status=ReevaluationStatus.SUPERSEDED,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )

            # 6. Capture max_job_id as consistent upper bound
            max_res = await session.execute(select(func.coalesce(func.max(Job.id), 0)))
            max_job_id = int(max_res.scalar_one())

            # 7. Create new ProfileReevaluationTask
            new_task = ProfileReevaluationTask(
                profile_id=profile.id,
                profile_revision=profile.revision,
                max_job_id=max_job_id,
                last_processed_job_id=0,
                status=ReevaluationStatus.PENDING,
                attempt_count=0,
                lease_token=None,
                lease_expires_at=None,
                created_at=utc_now(),
            )
            session.add(new_task)

            # 8. Commit all operations atomically
            await session.commit()
            await session.refresh(profile)
            await session.refresh(new_task)

            logger.info(
                "Updated profile %d to rev %d; queued reeval task %d (max_job_id=%d)",
                profile.id,
                profile.revision,
                new_task.id,
                max_job_id,
            )
            return profile, new_task

        except Exception:
            await session.rollback()
            raise
