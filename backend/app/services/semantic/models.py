"""Data models and enums for semantic job matching and model lifecycle."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.database.base import utc_now


class ModelLifecycleStatus(StrEnum):
    """Lifecycle state of the local sentence embedding model."""

    NOT_DOWNLOADED = "NOT_DOWNLOADED"
    DOWNLOADING = "DOWNLOADING"
    READY = "READY"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class ModelStatusInfo(BaseModel):
    """Status and runtime metadata of the local embedding model."""

    model_config = ConfigDict(frozen=True)

    status: ModelLifecycleStatus = Field(..., description="Current model state")
    model_name: str = Field(default="all-MiniLM-L6-v2", description="Hugging Face model identifier")
    model_path: str | None = Field(default=None, description="Local disk storage path")
    dimension: int = Field(default=384, description="Embedding vector dimension")
    is_ready: bool = Field(
        default=False, description="Whether the neural model is ready for inference"
    )
    is_fallback: bool = Field(
        default=False,
        description="Whether a deterministic fallback embedding service is currently active",
    )
    download_progress: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Download completion percentage",
    )
    error_message: str | None = Field(default=None, description="Error detail if status is ERROR")


class SemanticMatchResult(BaseModel):
    """Explainable result of semantic resume/profile to job matching."""

    model_config = ConfigDict(frozen=True)

    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Raw cosine similarity percentage (0.0 to 100.0)",
    )
    blended_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Combined deterministic and semantic relevance score",
    )
    shared_skills: list[str] = Field(
        default_factory=list,
        description="Skills or key concepts present in both profile/resume and job",
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description="Profile preferred skills not identified in the job description",
    )
    relevance_tier: str = Field(
        ...,
        description="Categorical semantic relevance tier: Strong Match, Moderate Match, Low Match",
    )
    is_sparse_description: bool = Field(
        default=False,
        description="True if the job description was missing or too sparse for high confidence",
    )
    model_version: str = Field(..., description="Embedding model version used for matching")
    evaluated_at: datetime = Field(
        default_factory=utc_now,
        description="Timestamp when semantic evaluation was performed",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional technical debug context (e.g. text lengths, weight breakdown)",
    )
