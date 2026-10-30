"""Comprehensive unit and integration tests for the deterministic job filtering engine."""

import copy

from app.filtering.engine import JobFilteringEngine, infer_remote
from app.filtering.models import (
    EligibilityStatus,
)
from app.filtering.scoring import (
    contains_keyword,
    match_locations,
)
from app.models.job import Job
from app.models.preference import PreferenceProfile

# =============================================================================
# 1. Exact and Partial Title Matching & Acronym Expansion (Finding 9)
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
    assert res_exact.score == 100.0
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


def test_tech_acronym_normalization() -> None:
    """Verify standard tech acronyms (SWE, SRE, ML, QA) match expanded titles."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": [
            "Software Engineer",
            "Site Reliability Engineer",
            "Machine Learning Engineer",
            "Quality Assurance",
        ],
    }

    # SWE Intern matching Software Engineer
    res_swe = engine.evaluate({"title": "SWE Intern"}, profile)
    assert res_swe.score >= 85.0
    assert any("title" in r.code and r.is_passing for r in res_swe.matched_criteria)

    # SRE matching Site Reliability Engineer
    res_sre = engine.evaluate({"title": "SRE"}, profile)
    assert res_sre.score == 100.0

    # ML Engineer matching Machine Learning Engineer
    res_ml = engine.evaluate({"title": "ML Engineer"}, profile)
    assert res_ml.score == 100.0

    # QA Analyst matching Quality Assurance
    res_qa = engine.evaluate({"title": "QA Analyst"}, profile)
    assert res_qa.score >= 85.0


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
# 3. Skills Matching and Single-Character Technology Fix (Finding 7)
# =============================================================================


def test_skills_matching_and_deduplication() -> None:
    """Verify skill keyword matching with symbol handling and duplicate prevention."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_skills": ["Python", "python", "PYTHON", "C++", "Docker", "Kubernetes"],
    }

    job = {
        "title": "Engineer",
        "description": (
            "We build high-performance C++ backend microservices. "
            "Python Python PYTHON scripting. Containerized with Docker."
        ),
    }

    res = engine.evaluate(job, profile)
    assert res.status == EligibilityStatus.ELIGIBLE
    assert res.score == 75.0
    reason = next(r for r in res.matched_criteria if r.code == "skills_match")
    matched = reason.details["matched_skills"]
    assert len(matched) == 3
    assert set(matched) == {"Python", "C++", "Docker"}


def test_single_character_and_symbolic_technology_matching() -> None:
    """Verify single-character languages (C, R) and symbols (C++, C#, .NET, Node.js)."""
    # 1. Objective-C must NOT match language C
    assert contains_keyword("Looking for an Objective-C developer", "C") is False
    assert contains_keyword("iOS app built in Objective-C and Swift", "C") is False

    # 2. Genuine C language matches
    assert contains_keyword("Must know C and C++.", "C") is True
    assert contains_keyword("Experience with C programming.", "C") is True
    assert contains_keyword("Core languages: (C, Assembly, Rust)", "C") is True

    # 3. Language R
    assert contains_keyword("Statistical analysis with R.", "R") is True
    assert contains_keyword("Looking for R&D engineers", "R") is False

    # 4. Complex symbols: C++, C#, .NET, Node.js, Vue.js, CI/CD, TS/SCI
    assert contains_keyword("Expertise with C++17 and C++20", "C++") is True
    assert contains_keyword("Backend service in C#", "C#") is True
    assert contains_keyword("Built on .NET Core", ".NET") is True
    assert contains_keyword("Node.js fullstack dev", "Node.js") is True
    assert contains_keyword("Vue.js frontend", "Vue.js") is True
    assert contains_keyword("CI/CD deployment pipelines", "CI/CD") is True
    assert contains_keyword("TS/SCI clearance required", "TS/SCI") is True


# =============================================================================
# 4. Location Matching Remediation (Finding 1)
# =============================================================================


def test_location_false_positives_eliminated() -> None:
    """Verify state abbreviations do not match substrings in unrelated city names."""
    # 1. Substring collisions confirmed fixed:
    assert match_locations("Chicago, IL", False, ["CA"])[0] is False
    assert match_locations("Austin, TX", False, ["IN"])[0] is False
    assert match_locations("Orlando, FL", False, ["OR"])[0] is False
    assert match_locations("Dallas, TX", False, ["AL"])[0] is False
    assert match_locations("Miami, FL", False, ["MI"])[0] is False

    # 2. York (UK) vs New York (NY) confirmed fixed:
    assert match_locations("York, UK", False, ["New York, NY"])[0] is False
    assert match_locations("York", False, ["New York"])[0] is False

    # 3. Legitimate positive matches work accurately:
    assert match_locations("Los Angeles, CA", False, ["CA"])[0] is True
    assert match_locations("San Francisco, CA", False, ["California"])[0] is True
    assert match_locations("San Francisco, CA", False, ["San Francisco, CA"])[0] is True
    assert match_locations("San Francisco, CA", False, ["San Francisco"])[0] is True
    assert match_locations("New York, NY", False, ["New York, NY"])[0] is True
    assert match_locations("New York, NY", False, ["NY"])[0] is True
    assert match_locations("Chicago, IL", False, ["Chicago, IL"])[0] is True
    assert match_locations("Remote - US", False, ["Remote"])[0] is True


# =============================================================================
# 5. Remote Inference Without False Positives (Finding 8)
# =============================================================================


def test_remote_inference_accuracy() -> None:
    """Verify domain titles (remote sensing) do not trigger false remote inference."""
    # 1. Non-remote domain titles
    assert infer_remote(None, "Reston, VA", "Remote Sensing Specialist") is False
    assert infer_remote(None, "San Diego, CA", "Remote Vehicle Pilot") is False
    assert infer_remote(None, "Houston, TX", "Remote Operations Control Tech") is False

    # 2. Legitimate remote titles and metadata
    assert infer_remote("remote", "Austin, TX", "Software Engineer") is True
    assert infer_remote(None, "Remote - US", "Backend Developer") is True
    assert infer_remote(None, None, "Remote Software Engineer") is True
    assert infer_remote(None, "New York, NY", "Senior SRE (Work From Home)") is True


# =============================================================================
# 6. Scoped Exclusion Keywords (Finding 2)
# =============================================================================


def test_scoped_exclusion_keywords() -> None:
    """Verify 'Senior' in internship description does not reject the internship."""
    engine = JobFilteringEngine()

    # Case A: Legacy profile with excluded_keywords = ["Senior"]
    # Should automatically scope seniority terms to title, NOT rejecting description mentions!
    legacy_profile = {
        "preferred_roles": ["Software Engineering Intern"],
        "excluded_keywords": ["Senior"],
    }

    intern_job = {
        "title": "Software Engineering Intern",
        "description": "You will be paired with a Senior Engineer who will mentor you.",
    }
    res_intern = engine.evaluate(intern_job, legacy_profile)
    assert res_intern.status == EligibilityStatus.ELIGIBLE
    assert not any(r.code == "excluded_title_keyword_found" for r in res_intern.failed_criteria)

    # Job title with Senior is rejected
    senior_job = {
        "title": "Senior Software Engineer",
        "description": "Looking for experienced backend developers.",
    }
    res_senior = engine.evaluate(senior_job, legacy_profile)
    assert res_senior.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "excluded_title_keyword_found" for r in res_senior.failed_criteria)

    # Case B: Explicit excluded_content_keywords
    content_ex_profile = {
        "excluded_content_keywords": ["Top Secret Clearance"],
    }
    clearance_job = {
        "title": "Junior Developer",
        "description": "Must hold an active Top Secret Clearance.",
    }
    res_clearance = engine.evaluate(clearance_job, content_ex_profile)
    assert res_clearance.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "excluded_content_keyword_found" for r in res_clearance.failed_criteria)


# =============================================================================
# 7. Minimum Relevance Score Before NEEDS_REVIEW (Finding 3)
# =============================================================================


def test_minimum_score_before_needs_review() -> None:
    """Verify score threshold evaluates before unknown criteria produces NEEDS_REVIEW."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Backend Engineer"],
        "graduation_year": 2026,
        "require_graduation_year_match": True,
        "minimum_match_score": 80.0,
    }

    # 1. Dishwasher with missing grad year and min score 80 -> INELIGIBLE (not NEEDS_REVIEW)
    dishwasher_job = {
        "title": "Commercial Dishwasher",
        "graduation_year": None,
    }
    res_dw = engine.evaluate(dishwasher_job, profile)
    assert res_dw.status == EligibilityStatus.INELIGIBLE
    assert res_dw.score == 0.0
    assert any(r.code == "minimum_score_unmet" for r in res_dw.failed_criteria)

    # 2. Relevant internship with missing mandatory grad year -> NEEDS_REVIEW
    relevant_job = {
        "title": "Backend Engineer",
        "description": "Building microservices",
        "graduation_year": None,
    }
    res_rel = engine.evaluate(relevant_job, profile)
    assert res_rel.status == EligibilityStatus.NEEDS_REVIEW
    assert res_rel.score >= 80.0
    assert any(r.code == "graduation_year_unknown" for r in res_rel.unknown_criteria)

    # 3. Relevant job with matching grad year -> ELIGIBLE
    matching_job = {
        "title": "Backend Engineer",
        "description": "Class of 2026 only.",
        "graduation_year": 2026,
    }
    res_match = engine.evaluate(matching_job, profile)
    assert res_match.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "graduation_year_match" for r in res_match.matched_criteria)

    # 4. Relevant job with confirmed grad year mismatch -> INELIGIBLE
    mismatch_job = {
        "title": "Backend Engineer",
        "description": "Class of 2024 only.",
        "graduation_year": 2024,
    }
    res_mismatch = engine.evaluate(mismatch_job, profile)
    assert res_mismatch.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "graduation_year_mismatch" for r in res_mismatch.failed_criteria)


# =============================================================================
# 8. Separate Preferred and Required Employment Types (Finding 4)
# =============================================================================


def test_preferred_vs_required_employment_types() -> None:
    """Verify preferred employment is soft preference while required is hard constraint."""
    engine = JobFilteringEngine()

    # Case A: Preferred internship (Soft Preference)
    soft_profile = {
        "preferred_roles": ["Software Engineer"],
        "preferred_employment_types": ["internship"],
    }

    # Full-time job is NOT automatically ineligible
    res_soft_ft = engine.evaluate(
        {"title": "Software Engineer", "employment_type": "full_time"}, soft_profile
    )
    assert res_soft_ft.status == EligibilityStatus.ELIGIBLE
    # Missing employment type does NOT force review
    res_soft_none = engine.evaluate(
        {"title": "Software Engineer", "employment_type": None}, soft_profile
    )
    assert res_soft_none.status == EligibilityStatus.ELIGIBLE

    # Case B: Required internship (Hard Constraint)
    hard_profile = {
        "preferred_roles": ["Software Engineer"],
        "required_employment_types": ["internship"],
    }

    # Full-time job is hard INELIGIBLE
    res_hard_ft = engine.evaluate(
        {"title": "Software Engineer", "employment_type": "full_time"}, hard_profile
    )
    assert res_hard_ft.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "required_employment_type_mismatch" for r in res_hard_ft.failed_criteria)

    # Missing employment type is NEEDS_REVIEW
    res_hard_none = engine.evaluate(
        {"title": "Software Engineer", "employment_type": None}, hard_profile
    )
    assert res_hard_none.status == EligibilityStatus.NEEDS_REVIEW
    assert any(r.code == "required_employment_type_unknown" for r in res_hard_none.unknown_criteria)


# =============================================================================
# 9. Multiple Eligible Graduation Years / Cohorts (Finding 5)
# =============================================================================


def test_multiple_eligible_graduation_years() -> None:
    """Verify job postings targeting cohorts (e.g. 2025-2027) accept candidate year."""
    engine = JobFilteringEngine()

    profile_2026 = {
        "graduation_year": 2026,
        "require_graduation_year_match": True,
    }

    # 1. Multiple explicitly eligible years: "Class of 2025, 2026, or 2027"
    job_cohort_list = {
        "title": "Software Engineering Intern",
        "description": "Open to Class of 2025, 2026, or 2027.",
    }
    res1 = engine.evaluate(job_cohort_list, profile_2026)
    assert res1.status == EligibilityStatus.ELIGIBLE
    assert any(r.code == "graduation_year_match" for r in res1.matched_criteria)

    # 2. Range: "2025-2027"
    job_range = {
        "title": "2025-2027 Tech Intern",
        "description": "Graduating between 2025 and 2027.",
    }
    res2 = engine.evaluate(job_range, profile_2026)
    assert res2.status == EligibilityStatus.ELIGIBLE

    # 3. Candidate outside range (2024 graduate evaluated against 2025-2027 cohort)
    profile_2024 = {
        "graduation_year": 2024,
        "require_graduation_year_match": True,
    }
    res3 = engine.evaluate(job_range, profile_2024)
    assert res3.status == EligibilityStatus.INELIGIBLE
    assert any(r.code == "graduation_year_mismatch" for r in res3.failed_criteria)

    # 4. Unrelated years (corporate history, copyright) do not create false cohorts
    unrelated_job = {
        "title": "Software Engineer",
        "description": "Company was established in 2024. All rights reserved 2025.",
    }
    res_unrelated = engine.evaluate(unrelated_job, profile_2026)
    # No graduation cohort specified -> unknown (NEEDS_REVIEW if required, not false mismatch)
    assert res_unrelated.status == EligibilityStatus.NEEDS_REVIEW
    assert any(r.code == "graduation_year_unknown" for r in res_unrelated.unknown_criteria)


# =============================================================================
# 10. Missing Metadata Scoring Normalization (Finding 6)
# =============================================================================


def test_missing_metadata_scoring_normalization() -> None:
    """Verify missing metadata is excluded from denominator without arbitrary 50% reward."""
    engine = JobFilteringEngine()

    profile = {
        "preferred_roles": ["Software Engineer"],  # 35
        "preferred_skills": ["Python"],  # 35
        "preferred_workplace_types": ["remote"],  # 10
        "preferred_employment_types": ["full_time"],  # 5
    }

    # Job A: Matches role and skill, workplace_type is omitted
    # Active weight: 35 (role) + 35 (skills) = 70. Earned: 35 + 35 = 70. Final score = 100.0
    job_missing_wp = {
        "title": "Software Engineer",
        "description": "Python developer",
        "job_type": None,
        "employment_type": None,
    }
    res_missing = engine.evaluate(job_missing_wp, profile)
    assert res_missing.score == 100.0
    assert any(r.code == "workplace_type_unknown" for r in res_missing.unknown_criteria)

    # Job B: Matches role and skill, but explicitly ONSITE (conflict with remote preference)
    # Active weight: 35 + 35 + 10 = 80. Earned: 35 + 35 + 0 = 70. Final score = 70 / 80 = 87.5
    job_onsite = {
        "title": "Software Engineer",
        "description": "Python developer",
        "job_type": "onsite",
        "employment_type": None,
    }
    res_onsite = engine.evaluate(job_onsite, profile)
    assert res_onsite.score == 87.5
    assert any(r.code == "workplace_type_no_match" for r in res_onsite.failed_criteria)

    # Job C: Matching remote workplace type
    # Active weight: 35 + 35 + 10 = 80. Earned: 35 + 35 + 10 = 80. Final score = 100.0
    job_remote = {
        "title": "Software Engineer",
        "description": "Python developer",
        "job_type": "remote",
        "employment_type": None,
    }
    res_remote = engine.evaluate(job_remote, profile)
    assert res_remote.score == 100.0
    assert any(r.code == "workplace_type_match" for r in res_remote.matched_criteria)


# =============================================================================
# 11. Immutability, Profile Isolation, and DB Model Compatibility
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


def test_orm_compatibility_and_batch() -> None:
    """Verify engine evaluates real SQLAlchemy Job and PreferenceProfile objects."""
    engine = JobFilteringEngine()

    job = Job(
        id=10,
        company_id=1,
        title="Site Reliability Engineer",
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
        preferred_roles=["Site Reliability Engineer"],
        preferred_skills=["Kubernetes", "Linux", "Python"],
        preferred_locations=["Remote"],
        required_employment_types=["full_time"],
        preferred_workplace_types=["remote"],
        excluded_title_keywords=["Manager"],
        graduation_year=2024,
        require_graduation_year_match=True,
        minimum_match_score=80.0,
    )

    result = engine.evaluate(job, profile)

    assert result.job_id == 10
    assert result.profile_id == 5
    assert result.status == EligibilityStatus.ELIGIBLE
    assert result.score >= 80.0
    assert any(r.code == "required_employment_type_match" for r in result.matched_criteria)

    batch_res = engine.evaluate_batch([job], profile)
    assert len(batch_res) == 1
    assert batch_res[0].status == EligibilityStatus.ELIGIBLE
