"""Core deterministic job filtering and eligibility evaluation engine."""

import logging
from typing import Any

from app.database.base import utc_now
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

logger = logging.getLogger(__name__)


def _safe_int(val: Any) -> int | None:
    """Safely convert value to int or None if invalid."""
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


class JobFilteringEngine:
    """Rules-based, explainable job eligibility and relevance scoring engine."""

    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self.weights = weights or ScoringWeights()

    def _extract_job_data(self, job: Any) -> dict[str, Any]:
        """Extract standardized job attributes from ORM models, Pydantic schemas, or dicts."""
        if isinstance(job, dict):
            return {
                "id": _safe_int(job.get("id")),
                "title": str(job.get("title") or ""),
                "description": job.get("description"),
                "location": job.get("location"),
                "department": job.get("department"),
                "employment_type": job.get("employment_type"),
                "job_type": job.get("job_type"),
                "graduation_year": _safe_int(job.get("graduation_year")),
            }

        return {
            "id": _safe_int(getattr(job, "id", None)),
            "title": str(getattr(job, "title", "") or ""),
            "description": getattr(job, "description", None),
            "location": getattr(job, "location", None),
            "department": getattr(job, "department", None),
            "employment_type": getattr(job, "employment_type", None),
            "job_type": getattr(job, "job_type", None),
            "graduation_year": _safe_int(getattr(job, "graduation_year", None)),
        }

    def _extract_profile_data(self, profile: Any) -> dict[str, Any]:
        """Extract standardized profile preferences from ORM models, Pydantic schemas, or dicts."""
        if isinstance(profile, dict):
            return {
                "id": _safe_int(profile.get("id")),
                "name": profile.get("name", "Default"),
                "preferred_roles": list(profile.get("preferred_roles") or []),
                "required_roles": list(profile.get("required_roles") or []),
                "preferred_skills": list(profile.get("preferred_skills") or []),
                "preferred_locations": list(profile.get("preferred_locations") or []),
                "required_locations": list(profile.get("required_locations") or []),
                "preferred_employment_types": list(profile.get("preferred_employment_types") or []),
                "preferred_workplace_types": list(profile.get("preferred_workplace_types") or []),
                "excluded_keywords": list(profile.get("excluded_keywords") or []),
                "graduation_year": _safe_int(profile.get("graduation_year")),
                "require_graduation_year_match": bool(
                    profile.get("require_graduation_year_match", False)
                ),
                "minimum_match_score": float(profile.get("minimum_match_score", 0.0) or 0.0),
            }

        return {
            "id": _safe_int(getattr(profile, "id", None)),
            "name": getattr(profile, "name", "Default"),
            "preferred_roles": list(getattr(profile, "preferred_roles", []) or []),
            "required_roles": list(getattr(profile, "required_roles", []) or []),
            "preferred_skills": list(getattr(profile, "preferred_skills", []) or []),
            "preferred_locations": list(getattr(profile, "preferred_locations", []) or []),
            "required_locations": list(getattr(profile, "required_locations", []) or []),
            "preferred_employment_types": list(
                getattr(profile, "preferred_employment_types", []) or []
            ),
            "preferred_workplace_types": list(
                getattr(profile, "preferred_workplace_types", []) or []
            ),
            "excluded_keywords": list(getattr(profile, "excluded_keywords", []) or []),
            "graduation_year": _safe_int(getattr(profile, "graduation_year", None)),
            "require_graduation_year_match": bool(
                getattr(profile, "require_graduation_year_match", False)
            ),
            "minimum_match_score": float(getattr(profile, "minimum_match_score", 0.0) or 0.0),
        }

    def evaluate(self, job: Any, profile: Any) -> EvaluationResult:
        """Evaluate a Job against a PreferenceProfile deterministically and explainably."""
        job_data = self._extract_job_data(job)
        prof_data = self._extract_profile_data(profile)

        matched_criteria: list[EvaluationReason] = []
        failed_criteria: list[EvaluationReason] = []
        unknown_criteria: list[EvaluationReason] = []
        score_breakdown: dict[str, float] = {}

        title = job_data["title"]
        description = job_data["description"] or ""
        location = job_data["location"]
        department = job_data["department"] or ""
        employment_type = job_data["employment_type"]
        job_type = job_data["job_type"]
        graduation_year = job_data["graduation_year"]

        combined_text = f"{title} {department} {description}"
        is_remote_job = bool(
            job_type == "remote"
            or (location and "remote" in location.lower())
            or ("remote" in title.lower())
        )

        has_hard_failure = False
        has_critical_unknown = False

        # =========================================================================
        # PHASE 1: HARD ELIGIBILITY RULES
        # =========================================================================

        # 1. Exclusion keywords
        excluded_kws = prof_data["excluded_keywords"]
        if excluded_kws:
            matched_exclusions = [kw for kw in excluded_kws if contains_keyword(combined_text, kw)]
            if matched_exclusions:
                has_hard_failure = True
                failed_criteria.append(
                    EvaluationReason(
                        code="excluded_keyword_found",
                        category=CriterionCategory.EXCLUSION,
                        is_passing=False,
                        is_hard_constraint=True,
                        message=(
                            f"Posting contains excluded keyword(s): "
                            f"{', '.join(repr(k) for k in matched_exclusions)}"
                        ),
                        details={"excluded_keywords_found": matched_exclusions},
                    )
                )
            else:
                matched_criteria.append(
                    EvaluationReason(
                        code="no_excluded_keywords",
                        category=CriterionCategory.EXCLUSION,
                        is_passing=True,
                        is_hard_constraint=True,
                        message="No excluded keywords found",
                    )
                )

        # 2. Mandatory / Required Roles
        required_roles = prof_data["required_roles"]
        if required_roles:
            ratio, matched_role, _ = compute_title_similarity(title, required_roles)
            if ratio >= 0.7 and matched_role:
                matched_criteria.append(
                    EvaluationReason(
                        code="required_role_match",
                        category=CriterionCategory.ROLE,
                        is_passing=True,
                        is_hard_constraint=True,
                        message=f"Job title matches required role '{matched_role}'",
                        details={"matched_role": matched_role, "similarity_ratio": ratio},
                    )
                )
            else:
                has_hard_failure = True
                failed_criteria.append(
                    EvaluationReason(
                        code="required_role_mismatch",
                        category=CriterionCategory.ROLE,
                        is_passing=False,
                        is_hard_constraint=True,
                        message=f"Job title '{title}' does not match any required role",
                        details={"required_roles": required_roles},
                    )
                )

        # 3. Required Locations
        required_locations = prof_data["required_locations"]
        if required_locations:
            if not location and not is_remote_job:
                has_critical_unknown = True
                unknown_criteria.append(
                    EvaluationReason(
                        code="required_location_unknown",
                        category=CriterionCategory.LOCATION,
                        is_passing=None,
                        is_hard_constraint=True,
                        message="Job posting omitted location; cannot verify required location",
                    )
                )
            else:
                is_loc_match, matched_loc = match_locations(
                    location, is_remote_job, required_locations
                )
                if is_loc_match:
                    matched_criteria.append(
                        EvaluationReason(
                            code="required_location_match",
                            category=CriterionCategory.LOCATION,
                            is_passing=True,
                            is_hard_constraint=True,
                            message=f"Location matches required location '{matched_loc}'",
                            details={"matched_location": matched_loc},
                        )
                    )
                else:
                    has_hard_failure = True
                    failed_criteria.append(
                        EvaluationReason(
                            code="required_location_mismatch",
                            category=CriterionCategory.LOCATION,
                            is_passing=False,
                            is_hard_constraint=True,
                            message=f"Location '{location}' does not match required locations",
                            details={"required_locations": required_locations},
                        )
                    )

        # 4. Employment Type Constraint
        preferred_emp_types = [t.lower() for t in prof_data["preferred_employment_types"]]
        if preferred_emp_types:
            if not employment_type:
                # Missing employment type is an unknown, not a silent rejection
                has_critical_unknown = True
                unknown_criteria.append(
                    EvaluationReason(
                        code="employment_type_unknown",
                        category=CriterionCategory.EMPLOYMENT_TYPE,
                        is_passing=None,
                        is_hard_constraint=True,
                        message="Job posting omitted employment type",
                    )
                )
            else:
                if employment_type.lower() in preferred_emp_types:
                    matched_criteria.append(
                        EvaluationReason(
                            code="employment_type_match",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=True,
                            is_hard_constraint=True,
                            message=f"Employment type '{employment_type}' matches preference",
                            details={"employment_type": employment_type},
                        )
                    )
                else:
                    has_hard_failure = True
                    failed_criteria.append(
                        EvaluationReason(
                            code="employment_type_mismatch",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=False,
                            is_hard_constraint=True,
                            message=(
                                f"Employment type '{employment_type}' excluded by "
                                f"preferences {prof_data['preferred_employment_types']}"
                            ),
                            details={
                                "job_employment_type": employment_type,
                                "preferred_types": prof_data["preferred_employment_types"],
                            },
                        )
                    )

        # 5. Graduation Year Constraint
        prof_grad_year = prof_data["graduation_year"]
        require_grad_match = prof_data["require_graduation_year_match"]

        if prof_grad_year is not None:
            if graduation_year is None:
                # Distinguish unknown from confirmed mismatch
                unknown_reason = EvaluationReason(
                    code="graduation_year_unknown",
                    category=CriterionCategory.GRADUATION_YEAR,
                    is_passing=None,
                    is_hard_constraint=require_grad_match,
                    message="Job posting did not specify graduation year eligibility",
                )
                unknown_criteria.append(unknown_reason)
                if require_grad_match:
                    has_critical_unknown = True
            elif graduation_year == prof_grad_year:
                matched_criteria.append(
                    EvaluationReason(
                        code="graduation_year_match",
                        category=CriterionCategory.GRADUATION_YEAR,
                        is_passing=True,
                        is_hard_constraint=require_grad_match,
                        message=f"Graduation year {graduation_year} matches preference",
                        details={"graduation_year": graduation_year},
                    )
                )
            else:
                # Confirmed mismatch
                if require_grad_match:
                    has_hard_failure = True
                    failed_criteria.append(
                        EvaluationReason(
                            code="graduation_year_mismatch",
                            category=CriterionCategory.GRADUATION_YEAR,
                            is_passing=False,
                            is_hard_constraint=True,
                            message=(
                                f"Graduation year mismatch: job requires {graduation_year}, "
                                f"profile specifies {prof_grad_year}"
                            ),
                            details={
                                "job_graduation_year": graduation_year,
                                "profile_graduation_year": prof_grad_year,
                            },
                        )
                    )
                else:
                    # Soft mismatch
                    failed_criteria.append(
                        EvaluationReason(
                            code="graduation_year_differs",
                            category=CriterionCategory.GRADUATION_YEAR,
                            is_passing=False,
                            is_hard_constraint=False,
                            message=(
                                f"Graduation year {graduation_year} "
                                f"differs from preferred {prof_grad_year}"
                            ),
                            details={
                                "job_graduation_year": graduation_year,
                                "profile_graduation_year": prof_grad_year,
                            },
                        )
                    )

        # =========================================================================
        # PHASE 2: SOFT RELEVANCE SCORING
        # =========================================================================

        total_active_weight = 0.0
        earned_points = 0.0

        # Dimension A: Preferred Roles / Title Similarity
        pref_roles = prof_data["preferred_roles"]
        if pref_roles:
            total_active_weight += self.weights.role_weight
            sim_ratio, matched_role, match_type = compute_title_similarity(title, pref_roles)
            role_points = sim_ratio * self.weights.role_weight
            earned_points += role_points
            score_breakdown["role"] = round(role_points, 2)

            if sim_ratio >= 0.7 and matched_role:
                matched_criteria.append(
                    EvaluationReason(
                        code=f"title_{match_type}_match",
                        category=CriterionCategory.ROLE,
                        is_passing=True,
                        is_hard_constraint=False,
                        message=f"Matched preferred role '{matched_role}' ({match_type})",
                        details={"role": matched_role, "similarity": sim_ratio},
                    )
                )
            elif sim_ratio > 0.0 and matched_role:
                matched_criteria.append(
                    EvaluationReason(
                        code="title_partial_match",
                        category=CriterionCategory.ROLE,
                        is_passing=True,
                        is_hard_constraint=False,
                        message=f"Partial overlap with preferred role '{matched_role}'",
                        details={"role": matched_role, "similarity": sim_ratio},
                    )
                )
            else:
                failed_criteria.append(
                    EvaluationReason(
                        code="title_no_match",
                        category=CriterionCategory.ROLE,
                        is_passing=False,
                        is_hard_constraint=False,
                        message="Job title did not match preferred roles",
                        details={"preferred_roles": pref_roles},
                    )
                )

        # Dimension B: Skills and Technologies
        pref_skills = prof_data["preferred_skills"]
        if pref_skills:
            # Deduplicate preferred skills for fair ratio
            seen_sk: set[str] = set()
            unique_pref_skills: list[str] = []
            for sk in pref_skills:
                norm_sk = sk.strip().lower()
                if norm_sk and norm_sk not in seen_sk:
                    seen_sk.add(norm_sk)
                    unique_pref_skills.append(sk.strip())

            total_active_weight += self.weights.skills_weight
            matched_sk, unmatched_sk = match_skills(combined_text, unique_pref_skills)
            skill_ratio = len(matched_sk) / len(unique_pref_skills) if unique_pref_skills else 1.0
            skills_points = skill_ratio * self.weights.skills_weight
            earned_points += skills_points
            score_breakdown["skills"] = round(skills_points, 2)

            if matched_sk:
                matched_criteria.append(
                    EvaluationReason(
                        code="skills_match",
                        category=CriterionCategory.SKILLS,
                        is_passing=True,
                        is_hard_constraint=False,
                        message=(
                            f"Matched {len(matched_sk)}/{len(unique_pref_skills)} skills: "
                            f"{', '.join(matched_sk)}"
                        ),
                        details={"matched_skills": matched_sk, "unmatched_skills": unmatched_sk},
                    )
                )
            else:
                failed_criteria.append(
                    EvaluationReason(
                        code="skills_no_match",
                        category=CriterionCategory.SKILLS,
                        is_passing=False,
                        is_hard_constraint=False,
                        message="No preferred skills matched in job posting",
                        details={"unmatched_skills": unmatched_sk},
                    )
                )

        # Dimension C: Location Match
        pref_locs = prof_data["preferred_locations"]
        if pref_locs:
            total_active_weight += self.weights.location_weight
            is_loc_match, matched_loc = match_locations(location, is_remote_job, pref_locs)
            if is_loc_match:
                loc_points = self.weights.location_weight
                earned_points += loc_points
                score_breakdown["location"] = round(loc_points, 2)
                matched_criteria.append(
                    EvaluationReason(
                        code="location_match",
                        category=CriterionCategory.LOCATION,
                        is_passing=True,
                        is_hard_constraint=False,
                        message=f"Location matches preferred location '{matched_loc}'",
                        details={"matched_location": matched_loc},
                    )
                )
            else:
                score_breakdown["location"] = 0.0
                if location is None:
                    unknown_criteria.append(
                        EvaluationReason(
                            code="location_unknown",
                            category=CriterionCategory.LOCATION,
                            is_passing=None,
                            is_hard_constraint=False,
                            message="Job did not specify location; could not verify location match",
                        )
                    )
                else:
                    failed_criteria.append(
                        EvaluationReason(
                            code="location_no_match",
                            category=CriterionCategory.LOCATION,
                            is_passing=False,
                            is_hard_constraint=False,
                            message=f"Job location '{location}' does not match preferred locations",
                            details={"preferred_locations": pref_locs},
                        )
                    )

        # Dimension D: Workplace Arrangement (Remote / Hybrid / Onsite)
        pref_wp_types = [w.lower() for w in prof_data["preferred_workplace_types"]]
        if pref_wp_types:
            total_active_weight += self.weights.workplace_type_weight
            if job_type and job_type.lower() in pref_wp_types:
                wp_points = self.weights.workplace_type_weight
                earned_points += wp_points
                score_breakdown["workplace_type"] = round(wp_points, 2)
                matched_criteria.append(
                    EvaluationReason(
                        code="workplace_type_match",
                        category=CriterionCategory.WORKPLACE_TYPE,
                        is_passing=True,
                        is_hard_constraint=False,
                        message=f"Workplace arrangement '{job_type}' matches preference",
                        details={"workplace_type": job_type},
                    )
                )
            elif not job_type:
                # Neutral partial credit for unstated workplace type
                wp_points = self.weights.workplace_type_weight * 0.5
                earned_points += wp_points
                score_breakdown["workplace_type"] = round(wp_points, 2)
                unknown_criteria.append(
                    EvaluationReason(
                        code="workplace_type_unknown",
                        category=CriterionCategory.WORKPLACE_TYPE,
                        is_passing=None,
                        is_hard_constraint=False,
                        message="Job posting omitted workplace arrangement (neutral weight)",
                    )
                )
            else:
                score_breakdown["workplace_type"] = 0.0
                failed_criteria.append(
                    EvaluationReason(
                        code="workplace_type_no_match",
                        category=CriterionCategory.WORKPLACE_TYPE,
                        is_passing=False,
                        is_hard_constraint=False,
                        message=f"Workplace arrangement '{job_type}' does not match preferences",
                        details={
                            "preferred_workplace_types": prof_data["preferred_workplace_types"]
                        },
                    )
                )

        # Dimension E: Employment Type (Soft Score Contribution)
        if preferred_emp_types:
            total_active_weight += self.weights.employment_type_weight
            if employment_type and employment_type.lower() in preferred_emp_types:
                emp_points = self.weights.employment_type_weight
                earned_points += emp_points
                score_breakdown["employment_type"] = round(emp_points, 2)
            elif not employment_type:
                emp_points = self.weights.employment_type_weight * 0.5
                earned_points += emp_points
                score_breakdown["employment_type"] = round(emp_points, 2)
            else:
                score_breakdown["employment_type"] = 0.0

        # Calculate final normalized score (0.0 to 100.0)
        if total_active_weight > 0.0:
            final_score = round((earned_points / total_active_weight) * 100.0, 1)
        else:
            # Unconstrained profile: baseline 100.0 relevance
            final_score = 100.0

        score_breakdown["total_score"] = final_score

        # =========================================================================
        # PHASE 3: OUTCOME DETERMINATION
        # =========================================================================

        min_score = prof_data["minimum_match_score"]
        status: EligibilityStatus

        if has_hard_failure:
            status = EligibilityStatus.INELIGIBLE
        elif has_critical_unknown:
            # Hard eligibility could not be verified due to missing job data
            status = EligibilityStatus.NEEDS_REVIEW
        elif min_score > 0.0 and final_score < min_score:
            status = EligibilityStatus.INELIGIBLE
            failed_criteria.append(
                EvaluationReason(
                    code="minimum_score_unmet",
                    category=CriterionCategory.MINIMUM_SCORE,
                    is_passing=False,
                    is_hard_constraint=True,
                    message=(
                        f"Relevance score {final_score:.1f} is below "
                        f"profile minimum {min_score:.1f}"
                    ),
                    details={"score": final_score, "minimum_score": min_score},
                )
            )
        else:
            status = EligibilityStatus.ELIGIBLE

        return EvaluationResult(
            job_id=job_data["id"],
            profile_id=prof_data["id"],
            status=status,
            score=final_score,
            matched_criteria=matched_criteria,
            failed_criteria=failed_criteria,
            unknown_criteria=unknown_criteria,
            score_breakdown=score_breakdown,
            evaluated_at=utc_now(),
        )

    def evaluate_batch(self, jobs: list[Any], profile: Any) -> list[EvaluationResult]:
        """Evaluate a batch of jobs against a preference profile."""
        return [self.evaluate(job, profile) for job in jobs]
