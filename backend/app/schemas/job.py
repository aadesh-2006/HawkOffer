"""Pydantic schemas for Job."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobBase(BaseModel):
    company_id: int
    title: str = Field(..., min_length=1, max_length=255)
    location: str | None = Field(None, max_length=255)
    department: str | None = Field(None, max_length=255)
    external_id: str | None = Field(None, max_length=255)
    description: str | None = None
    application_url: str = Field(..., min_length=1, max_length=2048)
    employment_type: str | None = Field(None, max_length=50)
    job_type: str | None = Field(None, max_length=50)
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    posted_at: datetime | None = None
    status: str = Field(default="NEW", max_length=50)


class JobCreate(JobBase):
    fingerprint_hash: str = Field(..., min_length=64, max_length=64)
    content_hash: str = Field(..., min_length=64, max_length=64)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class JobUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    location: str | None = Field(None, max_length=255)
    department: str | None = Field(None, max_length=255)
    external_id: str | None = Field(None, max_length=255)
    description: str | None = None
    application_url: str | None = Field(None, min_length=1, max_length=2048)
    employment_type: str | None = Field(None, max_length=50)
    job_type: str | None = Field(None, max_length=50)
    graduation_year: int | None = Field(None, ge=2000, le=2100)
    posted_at: datetime | None = None
    fingerprint_hash: str | None = Field(None, min_length=64, max_length=64)
    content_hash: str | None = Field(None, min_length=64, max_length=64)
    status: str | None = Field(None, max_length=50)
    last_seen_at: datetime | None = None


class JobRead(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fingerprint_hash: str
    content_hash: str
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    semantic_score: float | None = None
    match_tier: str | None = None
    shared_skills: list[str] | None = None
    missing_skills: list[str] | None = None


class JobMatchDetail(BaseModel):
    """Detailed explainable matching result for a job against a preference profile."""

    job_id: int
    profile_id: int
    eligibility_status: str
    deterministic_score: float
    semantic_score: float | None = None
    blended_score: float
    relevance_tier: str
    shared_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    is_sparse_description: bool = False
    reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class JobFilterParams(BaseModel):
    company_id: int | None = None
    status: str | None = None
    job_type: str | None = None
    employment_type: str | None = None
    graduation_year: int | None = None
    search: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
