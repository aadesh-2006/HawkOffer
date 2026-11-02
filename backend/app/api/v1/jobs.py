"""API router for Job resources."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.repositories.job_repo import JobRepository
from app.schemas.common import PaginatedResponse
from app.schemas.job import JobFilterParams, JobRead

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("", response_model=PaginatedResponse[JobRead])
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    company_id: Annotated[int | None, Query(description="Filter by company ID")] = None,
    status: Annotated[str | None, Query(description="Filter by job status")] = None,
    job_type: Annotated[str | None, Query(description="Filter by job category/type")] = None,
    employment_type: Annotated[str | None, Query(description="Filter by employment type")] = None,
    graduation_year: Annotated[
        int | None, Query(description="Filter by graduation year", ge=2000, le=2100)
    ] = None,
    search: Annotated[
        str | None, Query(description="Keyword search in title, location, department")
    ] = None,
    limit: Annotated[int, Query(description="Max items to return", ge=1, le=500)] = 50,
    offset: Annotated[int, Query(description="Pagination offset", ge=0)] = 0,
) -> PaginatedResponse[JobRead]:
    """Retrieve paginated jobs matching optional filter criteria."""
    params = JobFilterParams(
        company_id=company_id,
        status=status,
        job_type=job_type,
        employment_type=employment_type,
        graduation_year=graduation_year,
        search=search,
        limit=limit,
        offset=offset,
    )
    repo = JobRepository(db)
    items = await repo.filter_jobs(params)
    total = await repo.count_filtered(params)
    return PaginatedResponse[JobRead](
        items=[JobRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobRead:
    """Retrieve full details of a specific job by its ID."""
    repo = JobRepository(db)
    job = await repo.get_by_id(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found",
        )
    return JobRead.model_validate(job)
