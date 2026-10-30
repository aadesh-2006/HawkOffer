"""Job repository implementation."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.repositories.base import BaseRepository
from app.schemas.job import JobCreate, JobFilterParams, JobUpdate


class JobRepository(BaseRepository[Job, JobCreate, JobUpdate]):
    """Data access repository for Job entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Job, session)

    async def get_by_fingerprint(self, fingerprint_hash: str) -> Job | None:
        """Look up a job by its unique SHA-256 fingerprint hash."""
        result = await self.session.execute(
            select(Job).where(Job.fingerprint_hash == fingerprint_hash)
        )
        return result.scalars().first()

    async def get_by_external_id(self, company_id: int, external_id: str) -> Job | None:
        """Look up a job by company ID and ATS external identifier."""
        result = await self.session.execute(
            select(Job).where(Job.company_id == company_id, Job.external_id == external_id)
        )
        return result.scalars().first()

    async def get_by_application_url(self, application_url: str) -> Job | None:
        """Look up a job by its application URL."""
        result = await self.session.execute(
            select(Job).where(Job.application_url == application_url)
        )
        return result.scalars().first()

    async def list_by_company(
        self, company_id: int, status: str | None = None, skip: int = 0, limit: int = 100
    ) -> list[Job]:
        """List jobs belonging to a specific company."""
        stmt = select(Job).where(Job.company_id == company_id)
        if status:
            stmt = stmt.where(Job.status == status)
        stmt = stmt.order_by(Job.first_seen_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def filter_jobs(self, params: JobFilterParams) -> list[Job]:
        """Query jobs with dynamic filtering criteria."""
        stmt = select(Job)

        if params.company_id is not None:
            stmt = stmt.where(Job.company_id == params.company_id)
        if params.status:
            stmt = stmt.where(Job.status == params.status)
        if params.job_type:
            stmt = stmt.where(Job.job_type == params.job_type)
        if params.employment_type:
            stmt = stmt.where(Job.employment_type == params.employment_type)
        if params.graduation_year is not None:
            stmt = stmt.where(Job.graduation_year == params.graduation_year)
        if params.search:
            escaped = params.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            search_pattern = f"%{escaped}%"
            stmt = stmt.where(
                Job.title.ilike(search_pattern, escape="\\")
                | Job.location.ilike(search_pattern, escape="\\")
            )

        stmt = stmt.order_by(Job.first_seen_at.desc()).offset(params.offset).limit(params.limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self, job_id: int, status: str, last_seen_at: datetime | None = None
    ) -> Job | None:
        """Update job lifecycle status and last_seen timestamp."""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.status = status
        job.last_seen_at = last_seen_at or datetime.now(UTC)
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job
