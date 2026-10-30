"""Job eligibility and deterministic relevance filtering engine."""

from app.filtering.engine import JobFilteringEngine
from app.filtering.models import (
    CriterionCategory,
    EligibilityStatus,
    EvaluationReason,
    EvaluationResult,
)
from app.filtering.scoring import (
    ScoringWeights,
    compute_title_similarity,
    contains_keyword,
    match_locations,
    match_skills,
)

__all__ = [
    "CriterionCategory",
    "EligibilityStatus",
    "EvaluationReason",
    "EvaluationResult",
    "JobFilteringEngine",
    "ScoringWeights",
    "compute_title_similarity",
    "contains_keyword",
    "match_locations",
    "match_skills",
]
