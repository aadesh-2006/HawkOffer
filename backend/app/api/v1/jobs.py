"""API router for Job resources."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.filtering.engine import JobFilteringEngine
from app.models.preference import PreferenceProfile
from app.repositories.embedding_repo import EmbeddingRepository
from app.repositories.job_repo import JobRepository
from app.schemas.common import PaginatedResponse
from app.schemas.job import JobFilterParams, JobMatchDetail, JobRead
from app.services.semantic.matcher import SemanticJobMatcher
from app.services.semantic.model_manager import SemanticModelManager

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
    profile_id: Annotated[
        int | None, Query(description="Score relevance against specific profile ID")
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

    # Optional profile evaluation for semantic scoring badges
    profile = None
    if profile_id is not None:
        prof_res = await db.execute(
            select(PreferenceProfile).where(PreferenceProfile.id == profile_id)
        )
        profile = prof_res.scalar_one_or_none()
    else:
        # Load active profile by default if exists
        prof_res = await db.execute(
            select(PreferenceProfile).where(PreferenceProfile.is_active.is_(True)).limit(1)
        )
        profile = prof_res.scalar_one_or_none()

    engine = JobFilteringEngine()
    emb_repo = EmbeddingRepository(db)
    emb_svc = SemanticModelManager.get_instance().get_embedding_service()
    job_reads: list[JobRead] = []

    for item in items:
        job_read = JobRead.model_validate(item)
        if profile and getattr(profile, "semantic_enabled", True):
            try:
                job_text, _ = SemanticJobMatcher.build_job_text(item)
                job_vec = await emb_repo.get_or_compute_job_embedding(
                    job_id=item.id,
                    content_hash=item.content_hash,
                    job_text=job_text,
                    embedding_service=emb_svc,
                )
                eval_res = engine.evaluate(
                    item,
                    profile,
                    embedding_service=emb_svc,
                    job_embedding=job_vec,
                )
                if eval_res.semantic_match:
                    job_read.semantic_score = eval_res.semantic_match.similarity_score
                    job_read.match_tier = eval_res.semantic_match.relevance_tier
                    job_read.shared_skills = eval_res.semantic_match.shared_skills
                    job_read.missing_skills = eval_res.semantic_match.missing_skills
            except Exception:
                pass
        job_reads.append(job_read)
    await db.commit()

    return PaginatedResponse[JobRead](
        items=job_reads,
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


@router.get("/{job_id}/match", response_model=JobMatchDetail)
async def get_job_match_detail(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    profile_id: Annotated[int | None, Query(description="Target preference profile ID")] = None,
) -> JobMatchDetail:
    """Evaluate explainable deterministic and semantic match for a specific job."""
    repo = JobRepository(db)
    job = await repo.get_by_id(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found",
        )

    profile = None
    if profile_id is not None:
        prof_res = await db.execute(
            select(PreferenceProfile).where(PreferenceProfile.id == profile_id)
        )
        profile = prof_res.scalar_one_or_none()
    else:
        prof_res = await db.execute(
            select(PreferenceProfile).where(PreferenceProfile.is_active.is_(True)).limit(1)
        )
        profile = prof_res.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active preference profile found to match against",
        )

    # Use cached embedding or compute and persist
    emb_repo = EmbeddingRepository(db)
    emb_svc = SemanticModelManager.get_instance().get_embedding_service()
    job_text, _ = SemanticJobMatcher.build_job_text(job)
    job_vec = await emb_repo.get_or_compute_job_embedding(
        job_id=job.id,
        content_hash=job.content_hash,
        job_text=job_text,
        embedding_service=emb_svc,
    )
    await db.commit()

    engine = JobFilteringEngine()
    eval_res = engine.evaluate(
        job,
        profile,
        embedding_service=emb_svc,
        job_embedding=job_vec,
    )

    # Collect human-readable explainability reasons
    reasons: list[str] = [r.message for r in eval_res.matched_criteria]
    for r in eval_res.failed_criteria:
        reasons.append(f"Hard constraint unmet: {r.message}")

    sem = eval_res.semantic_match
    return JobMatchDetail(
        job_id=job.id,
        profile_id=profile.id,
        eligibility_status=eval_res.status.value,
        deterministic_score=round(eval_res.score, 1),
        semantic_score=round(sem.similarity_score, 1) if sem else None,
        blended_score=round(sem.blended_score, 1) if sem else round(eval_res.score, 1),
        relevance_tier=sem.relevance_tier if sem else "Deterministic Only",
        shared_skills=sem.shared_skills if sem else [],
        missing_skills=sem.missing_skills if sem else [],
        is_sparse_description=sem.is_sparse_description if sem else False,
        reasons=reasons,
        score_breakdown=eval_res.score_breakdown,
    )
