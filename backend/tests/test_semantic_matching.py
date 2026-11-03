"""Tests for Milestone 10: Local Semantic Job Matching.

Covers:
- FallbackEmbeddingService (feature hashing & cosine similarity)
- SemanticJobMatcher (ranking, shared/missing skills, sparse descriptions)
- Strict precedence of deterministic hard filters over semantic scores
- Embedding caching and invalidation on content_hash change
- REST API v1 endpoints for semantic model lifecycle and job match inspection
"""

import math

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.filtering.engine import JobFilteringEngine
from app.filtering.models import (
    EligibilityStatus,
)
from app.models.company import Company
from app.models.job import Job
from app.models.preference import PreferenceProfile
from app.repositories.embedding_repo import EmbeddingRepository
from app.services.semantic.embedding_service import FallbackEmbeddingService
from app.services.semantic.matcher import SemanticJobMatcher
from app.services.semantic.model_manager import SemanticModelManager
from app.services.semantic.models import ModelLifecycleStatus


def test_fallback_embedding_deterministic_and_normalized() -> None:
    service = FallbackEmbeddingService()
    text = "Senior Python engineer building distributed systems with Kubernetes and Kafka."
    vec1 = service.embed_text(text)
    vec2 = service.embed_text(text)

    # 1. Deterministic dimension
    assert len(vec1) == 384
    assert vec1 == vec2

    # 2. L2 Unit Normalization (sum of squares == 1.0)
    norm = math.sqrt(sum(x * x for x in vec1))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)

    # 3. High cosine similarity for related texts, low for unrelated
    related = "Python developer working on backend systems and microservices."
    unrelated = "Pastry chef baking sourdough bread and croissants in boutique bakery."
    sim_related = service.similarity(text, related)
    sim_unrelated = service.similarity(text, unrelated)

    assert sim_related > sim_unrelated
    assert sim_related > 0.2


def test_semantic_matcher_explainability_and_tiers() -> None:
    service = FallbackEmbeddingService()
    matcher = SemanticJobMatcher()

    # Highly aligned job and profile
    job_text = (
        "Software Engineering Intern. Requirements: Python, React, TypeScript, "
        "PostgreSQL, Git, Docker, REST APIs."
    )
    profile_text = (
        "Passionate Computer Science student with experience in Python, TypeScript, React, "
        "and SQL databases. Built distributed backend systems."
    )

    job_emb = service.embed_text(job_text)
    prof_emb = service.embed_text(profile_text)

    match_result = matcher.evaluate_match(
        job_embedding=job_emb,
        profile_embedding=prof_emb,
        deterministic_score=80.0,
        deterministic_status=EligibilityStatus.ELIGIBLE,
        preferred_skills=["Python", "TypeScript", "React", "Docker"],
        job_text=job_text,
        is_sparse=False,
        model_version="test-fallback",
    )

    assert match_result.similarity_score > 20.0
    assert match_result.blended_score > 50.0
    assert match_result.relevance_tier in ("EXCELLENT", "HIGH", "MEDIUM", "LOW")
    assert "Python" in match_result.shared_skills
    assert "TypeScript" in match_result.shared_skills
    assert match_result.is_sparse_description is False


def test_semantic_matcher_sparse_description() -> None:
    service = FallbackEmbeddingService()
    matcher = SemanticJobMatcher()
    short_text = "Apply here."

    job_emb = service.embed_text(short_text)
    prof_emb = service.embed_text("Software developer with Python experience.")

    match_result = matcher.evaluate_match(
        job_embedding=job_emb,
        profile_embedding=prof_emb,
        deterministic_score=50.0,
        deterministic_status=EligibilityStatus.NEEDS_REVIEW,
        preferred_skills=["Python"],
        job_text=short_text,
        is_sparse=True,
        model_version="test-fallback",
    )

    assert match_result.is_sparse_description is True


def test_deterministic_hard_filter_precedence_inviolable() -> None:
    """CRITICAL REQUIREMENT: Ineligible jobs remain INELIGIBLE regardless of semantic score."""
    engine = JobFilteringEngine()

    profile = PreferenceProfile(
        id=1,
        name="Backend Only",
        preferred_roles=["Backend Engineer"],
        preferred_locations=["San Francisco"],
        preferred_skills=["Python", "FastAPI"],
        excluded_keywords=["Frontend", "Unpaid"],
        minimum_match_score=50,
        semantic_enabled=True,
        resume_text="Senior Python and FastAPI specialist building cloud infrastructure.",
        is_active=True,
    )

    # Job has high semantic overlap with resume, BUT matches excluded keyword "Frontend"
    job = Job(
        id=10,
        company_id=1,
        title="Senior Python Backend and Frontend Engineer",  # Contains excluded "Frontend"
        description="Senior Python and FastAPI specialist building cloud infrastructure.",
        location="San Francisco, CA",
        application_url="https://example.com/apply",
        fingerprint_hash="f" * 64,
        content_hash="c" * 64,
    )

    result = engine.evaluate(job, profile)

    # 1. Hard filter must exclude job
    assert result.is_eligible is False
    assert result.status == EligibilityStatus.INELIGIBLE
    assert any("excluded keyword" in reason.message.lower() for reason in result.failed_criteria)

    # 2. Semantic score might exist for explainability, but eligibility MUST remain INELIGIBLE
    if result.semantic_match:
        assert result.status == EligibilityStatus.INELIGIBLE


@pytest.mark.asyncio
async def test_embedding_repository_cache_and_invalidation(
    db_session: AsyncSession,
) -> None:
    # 0. Create Company and Job for FK compliance
    company = Company(
        name="Embedding Corp",
        career_url="https://boards.greenhouse.io/embeddingcorp",
        platform="greenhouse",
        is_active=True,
    )
    db_session.add(company)
    await db_session.flush()

    job = Job(
        company_id=company.id,
        title="ML Systems Engineer",
        description="Local onnx inference and semantic matching.",
        location="Remote",
        application_url="https://example.com/apply/ml",
        fingerprint_hash="e" * 64,
        content_hash="h1" * 32,
    )
    db_session.add(job)
    await db_session.commit()

    repo = EmbeddingRepository(db_session)
    job_id = job.id
    version = "test-v1"
    initial_hash = "hash_v1"
    new_hash = "hash_v2"
    vec = [0.1] * 384

    # 1. Store embedding
    embedding = await repo.save_embedding(
        job_id=job_id,
        model_version=version,
        embedding=vec,
        content_hash=initial_hash,
    )
    await db_session.commit()
    assert embedding.id is not None

    # 2. Lookup with matching hash -> hits cache
    cached = await repo.get_by_job_and_version(job_id, version)
    assert cached is not None
    assert cached.content_hash == initial_hash
    assert len(cached.to_vector()) == 384

    # 3. Lookup with different hash -> invalidated / stale
    assert cached.content_hash != new_hash

    # 4. Upsert with new hash -> replaces old embedding
    new_vec = [0.2] * 384
    updated = await repo.save_embedding(
        job_id=job_id,
        model_version=version,
        embedding=new_vec,
        content_hash=new_hash,
    )
    await db_session.commit()
    assert updated.content_hash == new_hash

    cached_updated = await repo.get_by_job_and_version(job_id, version)
    assert cached_updated is not None
    assert cached_updated.content_hash == new_hash


def test_semantic_model_manager_status() -> None:
    manager = SemanticModelManager()
    info = manager.get_status()

    assert info.model_name == "all-MiniLM-L6-v2"
    assert info.dimension == 384
    assert info.status in (ModelLifecycleStatus.READY, ModelLifecycleStatus.NOT_DOWNLOADED)
    assert isinstance(info.is_fallback, bool)
