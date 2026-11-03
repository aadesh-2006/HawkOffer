"""Semantic job matcher computing vector similarity and explainability."""

import logging
import re
from typing import Any

from app.filtering.models import EligibilityStatus
from app.models.job import Job
from app.models.preference import PreferenceProfile
from app.services.semantic.models import SemanticMatchResult

logger = logging.getLogger(__name__)


class SemanticJobMatcher:
    """Evaluates semantic similarity between user profiles/resumes and job postings."""

    @staticmethod
    def build_profile_text(profile: PreferenceProfile | Any) -> str:
        """Construct canonical text representation of a user's preference profile and resume."""
        parts: list[str] = []

        preferred_roles = getattr(profile, "preferred_roles", []) or []
        if preferred_roles:
            parts.append(f"Target Roles: {', '.join(preferred_roles)}.")

        preferred_skills = getattr(profile, "preferred_skills", []) or []
        if preferred_skills:
            parts.append(f"Technical Skills: {', '.join(preferred_skills)}.")

        preferred_locations = getattr(profile, "preferred_locations", []) or []
        if preferred_locations:
            parts.append(f"Locations: {', '.join(preferred_locations)}.")

        resume_text = getattr(profile, "resume_text", None)
        if resume_text and resume_text.strip():
            # Include custom resume or summary text
            parts.append(f"Resume and Experience:\n{resume_text.strip()}")

        return "\n".join(parts).strip()

    @staticmethod
    def build_job_text(job: Job | Any) -> tuple[str, bool]:
        """Construct canonical text representation of a job posting.

        Returns (text, is_sparse_description).
        """
        title = str(getattr(job, "title", "") or "").strip()
        location = str(getattr(job, "location", "") or "").strip()
        department = str(getattr(job, "department", "") or "").strip()
        description = str(getattr(job, "description", "") or "").strip()

        parts: list[str] = [title]
        if department:
            parts.append(f"Department: {department}")
        if location:
            parts.append(f"Location: {location}")

        is_sparse = False
        if description and len(description) >= 40:
            parts.append(description)
        else:
            is_sparse = True
            if description:
                parts.append(description)

        return "\n".join(parts).strip(), is_sparse

    @staticmethod
    def compute_cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two float vectors.

        Assumes unit vectors; falls back to explicit norm if unnormalized.
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0

        dot = 0.0
        norm1 = 0.0
        norm2 = 0.0

        for a, b in zip(vec1, vec2, strict=False):
            dot += a * b
            norm1 += a * a
            norm2 += b * b

        if norm1 <= 1e-12 or norm2 <= 1e-12:
            return 0.0

        similarity = dot / (math_sqrt(norm1) * math_sqrt(norm2))
        # Clamp to [-1.0, 1.0] to guard against floating point inaccuracies
        similarity = max(-1.0, min(1.0, similarity))

        # Convert to 0.0 - 100.0 scale
        return max(0.0, min(100.0, similarity * 100.0))

    @staticmethod
    def extract_skills_analysis(
        preferred_skills: list[str],
        job_text: str,
    ) -> tuple[list[str], list[str]]:
        """Identify matching skills found in job text versus missing preferences."""
        if not preferred_skills:
            return [], []

        clean_job = job_text.lower()
        shared: list[str] = []
        missing: list[str] = []

        for skill in preferred_skills:
            if not skill or not skill.strip():
                continue
            skill_clean = skill.strip().lower()
            pattern = rf"\b{re.escape(skill_clean)}\b"
            if re.search(pattern, clean_job):
                shared.append(skill.strip())
            else:
                missing.append(skill.strip())

        return shared, missing

    @staticmethod
    def determine_relevance_tier(
        similarity_score: float, status: EligibilityStatus | None = None
    ) -> str:
        """Categorize similarity score into human-readable relevance tier."""
        if status == EligibilityStatus.INELIGIBLE:
            return "INELIGIBLE"
        if similarity_score >= 70.0:
            return "EXCELLENT"
        if similarity_score >= 50.0:
            return "HIGH"
        if similarity_score >= 35.0:
            return "MEDIUM"
        return "LOW"

    @classmethod
    def evaluate_match(
        cls,
        job_embedding: list[float],
        profile_embedding: list[float],
        deterministic_score: float,
        deterministic_status: EligibilityStatus,
        preferred_skills: list[str],
        job_text: str,
        is_sparse: bool,
        model_version: str,
    ) -> SemanticMatchResult:
        """Combine embeddings, deterministic filtering outcome, and explainability."""
        similarity = cls.compute_cosine_similarity(profile_embedding, job_embedding)

        # ---------------------------------------------------------------------
        # HARD FILTER PRECEDENCE:
        # If the job failed hard deterministic criteria (INELIGIBLE),
        # its blended score must NOT override ineligibility!
        # ---------------------------------------------------------------------
        if deterministic_status == EligibilityStatus.INELIGIBLE:
            blended = 0.0  # Ineligible postings receive 0.0 ranking score
            tier = "INELIGIBLE"
        else:
            # For ELIGIBLE and NEEDS_REVIEW jobs:
            # 55% deterministic criteria score + 45% semantic similarity score
            blended = (0.55 * deterministic_score) + (0.45 * similarity)
            blended = round(max(0.0, min(100.0, blended)), 1)
            tier = cls.determine_relevance_tier(similarity, deterministic_status)

        shared, missing = cls.extract_skills_analysis(preferred_skills, job_text)

        return SemanticMatchResult(
            similarity_score=round(similarity, 1),
            blended_score=blended,
            shared_skills=shared,
            missing_skills=missing,
            relevance_tier=tier,
            is_sparse_description=is_sparse,
            model_version=model_version,
            details={
                "deterministic_status": deterministic_status.value,
                "deterministic_score": round(deterministic_score, 1),
                "deterministic_weight": 0.55,
                "semantic_weight": 0.45,
            },
        )


def math_sqrt(x: float) -> float:
    """Fast math.sqrt helper."""
    import math

    return math.sqrt(x)
