"""Pydantic models and enums for job filtering and eligibility evaluation."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.database.base import utc_now


class EligibilityStatus(StrEnum):
    """Categorical outcome of evaluating a job against a preference profile."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class CriterionCategory(StrEnum):
    """Functional category of a matching rule or evaluation criterion."""

    ROLE = "role"
    SKILLS = "skills"
    LOCATION = "location"
    EMPLOYMENT_TYPE = "employment_type"
    WORKPLACE_TYPE = "workplace_type"
    GRADUATION_YEAR = "graduation_year"
    EXCLUSION = "exclusion"
    MINIMUM_SCORE = "minimum_score"


class EvaluationReason(BaseModel):
    """Structured, explainable evaluation detail for a specific criterion."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(..., description="Stable machine-readable identifier")
    category: CriterionCategory = Field(..., description="Criterion dimension")
    is_passing: bool | None = Field(
        ...,
        description="True if satisfied, False if failed, None if unknown/unverified",
    )
    is_hard_constraint: bool = Field(
        default=False,
        description="Whether failure of this criterion forces hard ineligibility",
    )
    message: str = Field(..., description="Human-readable explanation of the evaluation")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context such as matched keywords or missing attributes",
    )


class EvaluationResult(BaseModel):
    """Complete, explainable result of evaluating a Job against a PreferenceProfile."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: int | None = Field(default=None, description="Evaluated Job ID if available")
    profile_id: int | None = Field(
        default=None, description="Evaluated PreferenceProfile ID if available"
    )
    status: EligibilityStatus = Field(..., description="Final eligibility classification")
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Deterministic soft relevance score (0.0 to 100.0)",
    )
    matched_criteria: list[EvaluationReason] = Field(
        default_factory=list, description="Satisfied criteria reasons"
    )
    failed_criteria: list[EvaluationReason] = Field(
        default_factory=list, description="Failed criteria reasons"
    )
    unknown_criteria: list[EvaluationReason] = Field(
        default_factory=list, description="Criteria that could not be verified due to missing data"
    )
    score_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Breakdown of points awarded across scoring dimensions",
    )
    evaluated_at: datetime = Field(
        default_factory=utc_now, description="Timestamp when evaluation was executed"
    )

    @property
    def is_eligible(self) -> bool:
        """Return True if the job satisfies all eligibility requirements."""
        return self.status == EligibilityStatus.ELIGIBLE

    @property
    def is_ineligible(self) -> bool:
        """Return True if the job fails hard eligibility requirements."""
        return self.status == EligibilityStatus.INELIGIBLE

    @property
    def needs_review(self) -> bool:
        """Return True if the job requires human review due to ambiguous or omitted data."""
        return self.status == EligibilityStatus.NEEDS_REVIEW
