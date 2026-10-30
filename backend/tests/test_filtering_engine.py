"""Comprehensive unit and integration tests for the deterministic job filtering engine."""

import copy

from app.filtering.engine import JobFilteringEngine
from app.filtering.models import (
    EligibilityStatus,
)
from app.models.job import Job
from app.models.preference import PreferenceProfile

# =============================================================================
# 1. Exact and Partial Title Matching
# =============================================================================


def test_title_matching_exact_and_partial() -> None:
    """Verify exact, phrase containment, and token overlap title matching."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Software Engineer", "Backend Developer"],
    }

    # 1. Exact match (case insensitive)
    job_exact = {"title": "software engineer"}
    res_exact = engine.evaluate(job_exact, profile)
    assert res_exact.status == EligibilityStatus.ELIGIBLE
    assert res_exact.score == 100.0  # Only role dimension active, 100% of weight
    assert any(r.code == "title_exact_match" for r in res_exact.matched_criteria)

    # 2. Phrase containment
    job_phrase = {"title": "Lead Software Engineer - Infrastructure"}
    res_phrase = engine.evaluate(job_phrase, profile)
    assert res_phrase.status == EligibilityStatus.ELIGIBLE
    assert res_phrase.score == 85.0
    assert any(r.code == "title_phrase_match" for r in res_phrase.matched_criteria)

    # 3. Token overlap (reordered or partial tokens)
    job_overlap = {"title": "Developer, Python Backend Systems"}
    res_overlap = engine.evaluate(job_overlap, profile)
    assert res_overlap.status == EligibilityStatus.ELIGIBLE
    assert res_overlap.score > 0.0
    assert any(
        r.code in ("title_phrase_match", "title_token_overlap_match")
        for r in res_overlap.matched_criteria
    )

    # 4. No match
    job_nomatch = {"title": "Product Designer"}
    res_nomatch = engine.evaluate(job_nomatch, profile)
    assert res_nomatch.score == 0.0
    assert any(r.code == "title_no_match" for r in res_nomatch.failed_criteria)


# =============================================================================
# 2. Case-Insensitive Matching
# =============================================================================


def test_case_insensitive_matching() -> None:
    """Verify case-insensitivity across roles, skills, and locations."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["backend developer"],
        "preferred_skills": ["PYTHON", "FaStApI"],
        "preferred_locations": ["SAN FRANCISCO, CA"],
    }

    job = {
        "title": "BACKEND DEVELOPER",
        "description": "Looking for python and fastapi expertise.",
        "location": "san francisco, ca",
    }

    res = engine.evaluate(job, profile)
    assert res.status == EligibilityStatus.ELIGIBLE
    assert res.score == 100.0
    assert any(r.code == "title_exact_match" for r in res.matched_criteria)
    assert any(r.code == "skills_match" for r in res.matched_criteria)
    assert any(r.code == "location_match" for r in res.matched_criteria)


# =============================================================================
# 3. Skills Matching and Duplicate Keywords
# =============================================================================


def test_skills_matching_and_deduplication() -> None:
    """Verify skill keyword matching with symbol handling and duplicate prevention."""
    engine = JobFilteringEngine()

    # User profile has duplicate entries in different casings
    profile = {
        "preferred_skills": ["Python", "python", "PYTHON", "C++", "Docker", "Kubernetes"],
    }

    # Job description mentions Python multiple times, C++ once, Docker once
    job = {
        "title": "Engineer",
        "description": (
            "We build high-performance C++ backend microservices. "
            "Python Python PYTHON scripting. Containerized with Docker."
        ),
    }

    res = engine.evaluate(job, profile)
    assert res.status == EligibilityStatus.ELIGIBLE

    # Matched skills: Python, C++, Docker (3 out of 4 unique: Python, C++, Docker, Kubernetes)
    # 3 / 4 = 75.0%
    assert res.score == 75.0
    reason = next(r for r in res.matched_criteria if r.code == "skills_match")
    matched = reason.details["matched_skills"]
    assert len(matched) == 3
    assert set(matched) == {"Python", "C++", "Docker"}


# =============================================================================
# 4. Location Matching and Normalization
# =============================================================================


def test_location_matching() -> None:
    """Verify city/state matching, remote matching, and non-matches."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_locations": ["New York, NY", "Remote"],
    }

    # 1. City match
    res1 = engine.evaluate({"title": "Dev", "location": "New York, NY"}, profile)
    assert any(r.code == "location_match" for r in res1.matched_criteria)
    assert res1.score == 100.0

    # 2. Remote match via location text
    res2 = engine.evaluate({"title": "Dev", "location": "Remote - US"}, profile)
    assert any(r.code == "location_match" for r in res2.matched_criteria)

    # 3. Remote match via job_type
    res3 = engine.evaluate({"title": "Dev", "location": None, "job_type": "remote"}, profile)
    assert any(r.code == "location_match" for r in res3.matched_criteria)

    # 4. Location mismatch
    res4 = engine.evaluate({"title": "Dev", "location": "London, UK"}, profile)
    assert any(r.code == "location_no_match" for r in res4.failed_criteria)
    assert res4.score == 0.0


# =============================================================================
# 5. Remote / Hybrid / On-Site Preferences
# =============================================================================


def test_workplace_type_preferences() -> None:
    """Verify workplace arrangement matching and neutral unstated handling."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_workplace_types": ["remote", "hybrid"],
    }

    # Match
    res_remote = engine.evaluate({"title": "Dev", "job_type": "remote"}, profile)
    assert res_remote.score == 100.0
    assert any(r.code == "workplace_type_match" for r in res_remote.matched_criteria)

    # Mismatch
    res_onsite = engine.evaluate({"title": "Dev", "job_type": "onsite"}, profile)
    assert res_onsite.score == 0.0
    assert any(r.code == "workplace_type_no_match" for r in res_onsite.failed_criteria)

    # Omitted (neutral partial credit)
    res_none = engine.evaluate({"title": "Dev", "job_type": None}, profile)
    assert res_none.score == 50.0  # 50% neutral partial weight
    assert any(r.code == "workplace_type_unknown" for r in res_none.unknown_criteria)


# =============================================================================
# 6. Internship versus Full-Time Filtering
# =============================================================================


def test_employment_type_hard_filtering() -> None:
    """Verify hard exclusion for conflicting employment types and neutral for omitted."""
    engine = JobFilteringEngine()

    # User strictly wants internships
    profile = {
        "preferred_employment_types": ["internship"],
    }

    # 1. Matches internship
    res_intern = engine.evaluate({"title": "SWE Intern", "employment_type": "internship"}, profile)
    assert res_intern.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "employment_type_match" for r in res_intern.matched_criteria)

    # 2. Conflicting full_time is marked INELIGIBLE
    res_ft = engine.evaluate({"title": "Senior SWE", "employment_type": "full_time"}, profile)
    assert res_ft.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "employment_type_mismatch" for r in res_ft.failed_criteria)

    # 3. Omitted employment type is marked NEEDS_REVIEW (not silently rejected!)
    res_omitted = engine.evaluate({"title": "Software Engineer", "employment_type": None}, profile)
    assert res_omitted.status == EligibilityStatus.NEEDS_REVIEW
    assert any(r.code == "employment_type_unknown" for r in res_omitted.unknown_criteria)


# =============================================================================
# 7. Graduation-Year Match, Mismatch, and Missing Information
# =============================================================================


def test_graduation_year_strict_and_soft() -> None:
    """Verify strict graduation year matching vs missing info and soft differences."""
    engine = JobFilteringEngine()

    # Case A: Strict requirement (require_graduation_year_match=True)
    strict_profile = {
        "graduation_year": 2026,
        "require_graduation_year_match": True,
    }

    # 1. Matching grad year
    res_match = engine.evaluate({"title": "Intern", "graduation_year": 2026}, strict_profile)
    assert res_match.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "graduation_year_match" for r in res_match.matched_criteria)

    # 2. Confirmed mismatch -> INELIGIBLE
    res_mismatch = engine.evaluate({"title": "Intern", "graduation_year": 2025}, strict_profile)
    assert res_mismatch.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "graduation_year_mismatch" for r in res_mismatch.failed_criteria)

    # 3. Omitted grad year on job -> NEEDS_REVIEW (cannot verify requirement)
    res_unknown = engine.evaluate({"title": "Intern", "graduation_year": None}, strict_profile)
    assert res_unknown.status == EligibilityStatus.NEEDS_REVIEW
    assert any(r.code == "graduation_year_unknown" for r in res_unknown.unknown_criteria)

    # Case B: Soft preference (require_graduation_year_match=False)
    soft_profile = {
        "graduation_year": 2026,
        "require_graduation_year_match": False,
    }

    # Different grad year is not hard ineligible
    res_soft_diff = engine.evaluate({"title": "Intern", "graduation_year": 2027}, soft_profile)
    assert res_soft_diff.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "graduation_year_differs" for r in res_soft_diff.failed_criteria)


# =============================================================================
# 8. Exclusion Keywords
# =============================================================================


def test_exclusion_keywords() -> None:
    """Verify exclusion keywords in title or description cause hard ineligibility."""
    engine = JobFilteringEngine()

    profile = {
        "excluded_keywords": ["Senior", "Staff", "Director", "Clearance"],
    }

    # 1. Excluded keyword in title
    res_title = engine.evaluate({"title": "Senior Backend Engineer"}, profile)
    assert res_title.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "excluded_keyword_found" for r in res_title.failed_criteria)

    # 2. Excluded keyword in description
    res_desc = engine.evaluate(
        {
            "title": "Software Engineer",
            "description": "Active Secret Security Clearance required.",
        },
        profile,
    )
    assert res_desc.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "excluded_keyword_found" for r in res_desc.failed_criteria)

    # 3. Substring false positive guard (e.g. 'staff' must not match 'foodstaff' or 'distaff')
    res_false_pos = engine.evaluate(
        {"title": "Software Engineer", "description": "Free foodstaff in cafeteria."}, profile
    )
    assert res_false_pos.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "no_excluded_keywords" for r in res_false_pos.matched_criteria)


# =============================================================================
# 9. Empty or Partially Configured Preference Profiles
# =============================================================================


def test_empty_or_partial_profile() -> None:
    """Verify engine handles empty or single-dimension profiles gracefully."""
    engine = JobFilteringEngine()

    empty_profile: dict[str, list[str]] = {}
    job = {"title": "Software Engineer", "location": "Remote"}

    res = engine.evaluate(job, empty_profile)
    assert res.status == EligibilityStatus.ELIGIBLE
    assert res.score == 100.0  # Unconstrained profile defaults to baseline 100%

    # Profile with only skills
    skills_only_profile = {"preferred_skills": ["Python"]}
    job_with_skill = {"title": "Engineer", "description": "Python developer"}
    res_skills = engine.evaluate(job_with_skill, skills_only_profile)
    assert res_skills.status == EligibilityStatus.ELIGIBLE
    assert res_skills.score == 100.0  # 1/1 skills matched across active dimension


# =============================================================================
# 10. Missing Job Metadata
# =============================================================================


def test_missing_job_metadata_resilience() -> None:
    """Verify evaluation does not raise exceptions when job has empty or None fields."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Software Engineer"],
        "preferred_skills": ["Python"],
        "preferred_locations": ["San Francisco"],
        "preferred_employment_types": ["full_time"],
        "preferred_workplace_types": ["remote"],
    }

    sparse_job = {
        "title": "Software Engineer",
        "description": None,
        "location": None,
        "department": None,
        "employment_type": None,
        "job_type": None,
        "graduation_year": None,
    }

    res = engine.evaluate(sparse_job, profile)
    # Because employment_type was required by profile but None on job, outcome is NEEDS_REVIEW
    assert res.status == EligibilityStatus.NEEDS_REVIEW
    assert len(res.unknown_criteria) >= 2


# =============================================================================
# 11. Outcomes: Eligible, Ineligible, and Needs Review
# =============================================================================


def test_outcome_classifications() -> None:
    """Verify the 3 distinct outcomes (ELIGIBLE, INELIGIBLE, NEEDS_REVIEW)."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Backend Engineer"],
        "required_roles": ["Backend Engineer"],
        "preferred_skills": ["Python", "FastAPI"],
        "graduation_year": 2026,
        "require_graduation_year_match": True,
        "minimum_match_score": 70.0,
    }

    # 1. ELIGIBLE: Meets hard criteria and exceeds minimum score
    eligible_job = {
        "title": "Backend Engineer",
        "description": "Python and FastAPI developer",
        "graduation_year": 2026,
    }
    res1 = engine.evaluate(eligible_job, profile)
    assert res1.status == EligibilityStatus.ELIGIBLE
    assert res1.is_eligible is True

    # 2. INELIGIBLE: Fails required role
    ineligible_job = {
        "title": "Frontend Engineer",
        "description": "Python and FastAPI developer",
        "graduation_year": 2026,
    }
    res2 = engine.evaluate(ineligible_job, profile)
    assert res2.status == EligibilityStatus.INELIGIBLE
    assert res2.is_ineligible is True

    # 3. NEEDS_REVIEW: Role matches, but graduation year is omitted
    review_job = {
        "title": "Backend Engineer",
        "description": "Python and FastAPI developer",
        "graduation_year": None,
    }
    res3 = engine.evaluate(review_job, profile)
    assert res3.status == EligibilityStatus.NEEDS_REVIEW
    assert res3.needs_review is True


# =============================================================================
# 12. Score Boundaries and Determinism
# =============================================================================


def test_score_boundaries_and_determinism() -> None:
    """Verify score is bounded in [0.0, 100.0] and strictly deterministic."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Backend Engineer"],
        "preferred_skills": ["Python", "Docker", "SQL"],
        "preferred_locations": ["Austin, TX"],
    }

    job = {
        "title": "Backend Engineer",
        "description": "Python and Docker skills needed.",
        "location": "Austin, TX",
    }

    # Run evaluation 5 times; results must be identical
    results = [engine.evaluate(job, profile) for _ in range(5)]
    first_score = results[0].score

    assert 0.0 <= first_score <= 100.0
    for r in results:
        assert r.score == first_score
        assert [c.code for c in r.matched_criteria] == [c.code for c in results[0].matched_criteria]


# =============================================================================
# 13. Multiple Preference Profile Isolation
# =============================================================================


def test_multiple_profile_isolation() -> None:
    """Verify evaluating against Profile A does not leak into Profile B."""
    engine = JobFilteringEngine()

    profile_swe = {
        "id": 1,
        "name": "SWE Profile",
        "preferred_roles": ["Software Engineer"],
        "preferred_skills": ["Python"],
    }

    profile_pm = {
        "id": 2,
        "name": "PM Profile",
        "preferred_roles": ["Product Manager"],
        "preferred_skills": ["Roadmapping", "Agile"],
    }

    swe_job = {"title": "Software Engineer", "description": "Python"}

    res_swe = engine.evaluate(swe_job, profile_swe)
    res_pm = engine.evaluate(swe_job, profile_pm)

    assert res_swe.profile_id == 1
    assert res_swe.status == EligibilityStatus.ELIGIBLE
    assert res_swe.score == 100.0

    assert res_pm.profile_id == 2
    assert res_pm.status == EligibilityStatus.ELIGIBLE
    assert res_pm.score == 0.0


# =============================================================================
# 14. No Mutation of Source Objects
# =============================================================================


def test_source_objects_not_mutated() -> None:
    """Verify evaluation does not alter the input Job or PreferenceProfile."""
    engine = JobFilteringEngine()

    original_job = {
        "id": 42,
        "title": "Platform Engineer",
        "description": "Terraform and Go",
        "preferred_skills": ["SHOULD_NOT_BE_MODIFIED"],
    }
    original_profile = {
        "id": 7,
        "preferred_roles": ["Platform Engineer"],
        "preferred_skills": ["Go", "Terraform"],
    }

    job_copy = copy.deepcopy(original_job)
    profile_copy = copy.deepcopy(original_profile)

    engine.evaluate(original_job, original_profile)

    assert original_job == job_copy
    assert original_profile == profile_copy


# =============================================================================
# 15. Invalid or Unexpected Input Values
# =============================================================================


def test_unexpected_and_invalid_inputs() -> None:
    """Verify robust handling of empty strings, none values, and mismatched types."""
    engine = JobFilteringEngine()

    # Empty strings, nulls, negative numbers in dict
    weird_job = {
        "id": "not_an_int",
        "title": "",
        "description": None,
        "location": "",
        "graduation_year": "invalid_year",
    }
    weird_profile = {
        "id": None,
        "preferred_roles": ["", None, "Engineer"],
        "preferred_skills": [],
        "minimum_match_score": -10.0,
    }

    res = engine.evaluate(weird_job, weird_profile)
    assert res is not None
    assert 0.0 <= res.score <= 100.0


# =============================================================================
# 16. Integration with SQLAlchemy ORM and Pydantic Models
# =============================================================================


def test_orm_and_pydantic_model_compatibility() -> None:
    """Verify engine evaluates real SQLAlchemy Job and PreferenceProfile objects."""
    engine = JobFilteringEngine()

    job = Job(
        id=10,
        company_id=1,
        title="Senior Site Reliability Engineer",
        description="Kubernetes, Linux, Python troubleshooting.",
        location="Remote, US",
        employment_type="full_time",
        job_type="remote",
        graduation_year=2024,
        application_url="https://acme.com/jobs/10",
        fingerprint_hash="a" * 64,
        content_hash="b" * 64,
    )

    profile = PreferenceProfile(
        id=5,
        name="SRE Hunter",
        preferred_roles=["Site Reliability Engineer", "DevOps"],
        preferred_skills=["Kubernetes", "Linux", "Python"],
        preferred_locations=["Remote"],
        preferred_employment_types=["full_time"],
        preferred_workplace_types=["remote"],
        excluded_keywords=["Manager"],
        graduation_year=2024,
        require_graduation_year_match=True,
        minimum_match_score=80.0,
    )

    result = engine.evaluate(job, profile)

    assert result.job_id == 10
    assert result.profile_id == 5
    assert result.status == EligibilityStatus.ELIGIBLE
    assert result.score >= 80.0
    assert any(r.code == "no_excluded_keywords" for r in result.matched_criteria)
    assert any(r.code == "graduation_year_match" for r in result.matched_criteria)
    assert any(r.code == "employment_type_match" for r in result.matched_criteria)
    assert any(r.code == "workplace_type_match" for r in result.matched_criteria)
    assert any(r.code == "skills_match" for r in result.matched_criteria)


def test_batch_evaluation() -> None:
    """Verify batch evaluation processes multiple jobs efficiently."""
    engine = JobFilteringEngine()

    profile = {"preferred_roles": ["Engineer"]}
    jobs = [
        {"id": 1, "title": "Engineer"},
        {"id": 2, "title": "Designer"},
        {"id": 3, "title": "Accountant"},
    ]

    results = engine.evaluate_batch(jobs, profile)
    assert len(results) == 3
    assert results[0].score == 100.0
    assert results[1].score == 0.0
    assert results[2].score == 0.0
