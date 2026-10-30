"""Core deterministic job filtering and eligibility evaluation engine."""

import logging
import re
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
from app.services.job_normalizer import JobNormalizer

logger = logging.getLogger(__name__)

# Common seniority and executive level words for legacy scope fallback
SENIORITY_KEYWORDS: set[str] = {
    "senior",
    "sr",
    "principal",
    "staff",
    "lead",
    "director",
    "manager",
    "head",
    "vp",
    "chief",
    "executive",
}

# Non-remote domain phrase patterns in job titles
NON_REMOTE_TITLE_PATTERNS: tuple[str, ...] = (
    r"\bremote\s+sensing\b",
    r"\bremote\s+pilot\b",
    r"\bremote\s+vehicle\b",
    r"\bremote\s+operations?\b",
    r"\bremote\s+control\b",
)


def _safe_int(val: Any) -> int | None:
    """Safely convert value to int or None if invalid."""
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def infer_remote(job_type: str | None, location: str | None, title: str) -> bool:
    """Accurately determine whether a job is remote without false-positive domain matches."""
    if job_type and job_type.strip().lower() == "remote":
        return True

    if location and re.search(r"\b(remote|virtual|wfh)\b", location, re.IGNORECASE):
        return True

    clean_title = title.lower()
    # Check if title contains a non-remote domain compound like 'remote sensing'
    if any(re.search(pat, clean_title) for pat in NON_REMOTE_TITLE_PATTERNS):
        return False

    return bool(re.search(r"\b(remote|virtual|wfh|work\s+from\s+home)\b", clean_title))


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
                "required_employment_types": list(profile.get("required_employment_types") or []),
                "preferred_workplace_types": list(profile.get("preferred_workplace_types") or []),
                "excluded_keywords": list(profile.get("excluded_keywords") or []),
                "excluded_title_keywords": list(profile.get("excluded_title_keywords") or []),
                "excluded_content_keywords": list(profile.get("excluded_content_keywords") or []),
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
            "required_employment_types": list(
                getattr(profile, "required_employment_types", []) or []
            ),
            "preferred_workplace_types": list(
                getattr(profile, "preferred_workplace_types", []) or []
            ),
            "excluded_keywords": list(getattr(profile, "excluded_keywords", []) or []),
            "excluded_title_keywords": list(getattr(profile, "excluded_title_keywords", []) or []),
            "excluded_content_keywords": list(
                getattr(profile, "excluded_content_keywords", []) or []
            ),
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
        job_grad_year = job_data["graduation_year"]

        combined_text = f"{title} {department} {description}"
        is_remote_job = infer_remote(job_type, location, title)

        has_hard_failure = False
        has_critical_unknown = False

        # =========================================================================
        # PHASE 1: HARD ELIGIBILITY RULES
        # =========================================================================

        # 1. Scoped Exclusion Keywords
        title_exclusions = list(prof_data["excluded_title_keywords"])
        content_exclusions = list(prof_data["excluded_content_keywords"])

        # Backward compatibility for legacy excluded_keywords
        if not title_exclusions and not content_exclusions:
            for kw in prof_data["excluded_keywords"]:
                norm_kw = kw.strip().lower()
                if norm_kw in SENIORITY_KEYWORDS:
                    title_exclusions.append(kw)
                else:
                    content_exclusions.append(kw)

        # Evaluate title exclusions
        if title_exclusions:
            matched_title_ex = [kw for kw in title_exclusions if contains_keyword(title, kw)]
            if matched_title_ex:
                has_hard_failure = True
                failed_criteria.append(
                    EvaluationReason(
                        code="excluded_title_keyword_found",
                        category=CriterionCategory.EXCLUSION,
                        is_passing=False,
                        is_hard_constraint=True,
                        message=(
                            f"Job title contains excluded keyword(s): "
                            f"{', '.join(repr(k) for k in matched_title_ex)}"
                        ),
                        details={"excluded_title_keywords_found": matched_title_ex},
                    )
                )

        # Evaluate content exclusions
        if content_exclusions:
            matched_content_ex = [
                kw for kw in content_exclusions if contains_keyword(combined_text, kw)
            ]
            if matched_content_ex:
                has_hard_failure = True
                failed_criteria.append(
                    EvaluationReason(
                        code="excluded_content_keyword_found",
                        category=CriterionCategory.EXCLUSION,
                        is_passing=False,
                        is_hard_constraint=True,
                        message=(
                            f"Posting content contains excluded keyword(s): "
                            f"{', '.join(repr(k) for k in matched_content_ex)}"
                        ),
                        details={"excluded_content_keywords_found": matched_content_ex},
                    )
                )

        if not title_exclusions and not content_exclusions:
            matched_criteria.append(
                EvaluationReason(
                    code="no_excluded_keywords",
                    category=CriterionCategory.EXCLUSION,
                    is_passing=True,
                    is_hard_constraint=True,
                    message="No exclusion keywords configured",
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

        # 3. Mandatory / Required Locations
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

        # 4. Mandatory / Required Employment Types
        required_emp_types = [t.lower() for t in prof_data["required_employment_types"]]
        if required_emp_types:
            if not employment_type:
                has_critical_unknown = True
                unknown_criteria.append(
                    EvaluationReason(
                        code="required_employment_type_unknown",
                        category=CriterionCategory.EMPLOYMENT_TYPE,
                        is_passing=None,
                        is_hard_constraint=True,
                        message=(
                            "Job posting omitted employment type; cannot verify required constraint"
                        ),
                    )
                )
            else:
                if employment_type.lower() in required_emp_types:
                    matched_criteria.append(
                        EvaluationReason(
                            code="required_employment_type_match",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=True,
                            is_hard_constraint=True,
                            message=f"Employment type '{employment_type}' matches required types",
                            details={"employment_type": employment_type},
                        )
                    )
                else:
                    has_hard_failure = True
                    failed_criteria.append(
                        EvaluationReason(
                            code="required_employment_type_mismatch",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=False,
                            is_hard_constraint=True,
                            message=(
                                f"Employment type '{employment_type}' not in "
                                f"required types {prof_data['required_employment_types']}"
                            ),
                            details={
                                "job_employment_type": employment_type,
                                "required_types": prof_data["required_employment_types"],
                            },
                        )
                    )

        # 5. Graduation Year Constraint (Multi-year Cohort Support)
        prof_grad_year = prof_data["graduation_year"]
        require_grad_match = prof_data["require_graduation_year_match"]

        if prof_grad_year is not None:
            # Detect all eligible cohorts mentioned in title or description
            detected_cohorts = set(JobNormalizer.extract_graduation_years(title, description))
            if job_grad_year is not None:
                detected_cohorts.add(job_grad_year)

            if not detected_cohorts:
                # Posting omitted any graduation year or student cohort context
                unknown_criteria.append(
                    EvaluationReason(
                        code="graduation_year_unknown",
                        category=CriterionCategory.GRADUATION_YEAR,
                        is_passing=None,
                        is_hard_constraint=require_grad_match,
                        message="Job posting did not specify graduation year eligibility",
                    )
                )
                if require_grad_match:
                    has_critical_unknown = True
            elif prof_grad_year in detected_cohorts:
                matched_criteria.append(
                    EvaluationReason(
                        code="graduation_year_match",
                        category=CriterionCategory.GRADUATION_YEAR,
                        is_passing=True,
                        is_hard_constraint=require_grad_match,
                        message=(
                            f"Graduation year {prof_grad_year} is eligible "
                            f"(job accepts cohorts {sorted(detected_cohorts)})"
                        ),
                        details={
                            "eligible_cohorts": sorted(detected_cohorts),
                            "candidate_year": prof_grad_year,
                        },
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
                                f"Graduation year mismatch: job targets "
                                f"{sorted(detected_cohorts)}, profile specifies {prof_grad_year}"
                            ),
                            details={
                                "eligible_cohorts": sorted(detected_cohorts),
                                "candidate_year": prof_grad_year,
                            },
                        )
                    )
                else:
                    failed_criteria.append(
                        EvaluationReason(
                            code="graduation_year_differs",
                            category=CriterionCategory.GRADUATION_YEAR,
                            is_passing=False,
                            is_hard_constraint=False,
                            message=(
                                f"Graduation year {prof_grad_year} differs from "
                                f"job cohorts {sorted(detected_cohorts)}"
                            ),
                            details={
                                "eligible_cohorts": sorted(detected_cohorts),
                                "candidate_year": prof_grad_year,
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
            if location or is_remote_job:
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
            else:
                # Omitted location: exclude from denominator to avoid artificial penalty or reward
                score_breakdown["location"] = 0.0
                unknown_criteria.append(
                    EvaluationReason(
                        code="location_unknown",
                        category=CriterionCategory.LOCATION,
                        is_passing=None,
                        is_hard_constraint=False,
                        message="Job omitted location; excluded from soft score denominator",
                    )
                )

        # Dimension D: Workplace Arrangement (Remote / Hybrid / Onsite)
        pref_wp_types = [w.lower() for w in prof_data["preferred_workplace_types"]]
        if pref_wp_types:
            if job_type:
                total_active_weight += self.weights.workplace_type_weight
                if job_type.lower() in pref_wp_types:
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
                else:
                    score_breakdown["workplace_type"] = 0.0
                    failed_criteria.append(
                        EvaluationReason(
                            code="workplace_type_no_match",
                            category=CriterionCategory.WORKPLACE_TYPE,
                            is_passing=False,
                            is_hard_constraint=False,
                            message=f"Workplace '{job_type}' does not match preferences",
                            details={
                                "preferred_workplace_types": prof_data["preferred_workplace_types"]
                            },
                        )
                    )
            else:
                # Omitted workplace type: exclude from denominator (no arbitrary 50% reward)
                score_breakdown["workplace_type"] = 0.0
                unknown_criteria.append(
                    EvaluationReason(
                        code="workplace_type_unknown",
                        category=CriterionCategory.WORKPLACE_TYPE,
                        is_passing=None,
                        is_hard_constraint=False,
                        message="Job omitted workplace type; excluded from score denominator",
                    )
                )

        # Dimension E: Preferred Employment Types (Soft Scoring Only)
        pref_emp_types = [t.lower() for t in prof_data["preferred_employment_types"]]
        if pref_emp_types:
            if employment_type:
                total_active_weight += self.weights.employment_type_weight
                if employment_type.lower() in pref_emp_types:
                    emp_points = self.weights.employment_type_weight
                    earned_points += emp_points
                    score_breakdown["employment_type"] = round(emp_points, 2)
                    matched_criteria.append(
                        EvaluationReason(
                            code="preferred_employment_type_match",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=True,
                            is_hard_constraint=False,
                            message=f"Employment type '{employment_type}' matches preference",
                            details={"employment_type": employment_type},
                        )
                    )
                else:
                    score_breakdown["employment_type"] = 0.0
                    failed_criteria.append(
                        EvaluationReason(
                            code="preferred_employment_type_no_match",
                            category=CriterionCategory.EMPLOYMENT_TYPE,
                            is_passing=False,
                            is_hard_constraint=False,
                            message=(
                                f"Employment type '{employment_type}' differs from "
                                f"preferred {prof_data['preferred_employment_types']}"
                            ),
                            details={
                                "employment_type": employment_type,
                                "preferred_types": prof_data["preferred_employment_types"],
                            },
                        )
                    )
            else:
                # Omitted: exclude from denominator
                score_breakdown["employment_type"] = 0.0
                unknown_criteria.append(
                    EvaluationReason(
                        code="preferred_employment_type_unknown",
                        category=CriterionCategory.EMPLOYMENT_TYPE,
                        is_passing=None,
                        is_hard_constraint=False,
                        message="Job omitted employment type; excluded from score denominator",
                    )
                )

        # Calculate final normalized score (0.0 to 100.0)
        if total_active_weight > 0.0:
            final_score = round((earned_points / total_active_weight) * 100.0, 1)
        else:
            final_score = 100.0

        score_breakdown["total_score"] = final_score

        # =========================================================================
        # PHASE 3: OUTCOME DETERMINATION
        # Priority:
        # 1. Hard failure -> INELIGIBLE
        # 2. Below minimum score -> INELIGIBLE
        # 3. Critical unknown on mandatory requirement -> NEEDS_REVIEW
        # 4. Otherwise -> ELIGIBLE
        # =========================================================================

        min_score = prof_data["minimum_match_score"]
        status: EligibilityStatus

        if has_hard_failure:
            status = EligibilityStatus.INELIGIBLE
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
        elif has_critical_unknown:
            # Only elevate to review if the posting is otherwise relevant
            status = EligibilityStatus.NEEDS_REVIEW
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
