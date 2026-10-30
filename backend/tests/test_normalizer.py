"""Tests for JobNormalizer rules, cleaning pipelines, and deterministic fingerprinting."""

from datetime import UTC, datetime

from app.scrapers.base import RawJobPosting
from app.services.job_normalizer import JobNormalizer, NormalizedJob


def test_clean_title() -> None:
    """Verify title cleaning removes excess whitespace, tabs, and wrapping punctuation."""
    assert (
        JobNormalizer.clean_title("  Senior   Software   Engineer  ") == "Senior Software Engineer"
    )
    assert JobNormalizer.clean_title(" - Product Manager | ") == "Product Manager"
    assert JobNormalizer.clean_title("") == ""


def test_clean_url() -> None:
    """Verify clean_url strips tracking query parameters and fragments deterministically."""
    raw = (
        "https://boards.greenhouse.io/stripe/jobs/12345"
        "?utm_source=linkedin&utm_medium=job_board&gh_src=abc1234&b=2&a=1#apply"
    )
    cleaned = JobNormalizer.clean_url(raw)
    assert "utm_source" not in cleaned
    assert "utm_medium" not in cleaned
    assert "gh_src" not in cleaned
    assert "#apply" not in cleaned
    # Legitimate non-tracking parameters are preserved and deterministically sorted
    assert "a=1&b=2" in cleaned
    assert cleaned.startswith("https://boards.greenhouse.io/stripe/jobs/12345")


def test_clean_description() -> None:
    """Verify HTML tags and excess blank lines are stripped from job descriptions."""
    html_desc = """
    <div>
        <h3>About the Role</h3>
        <p>We are looking for a <strong>Senior Engineer</strong> to join our team.<br/></p>
        <ul>
            <li>Build high-throughput Python backends.</li>
            <li>Maintain distributed systems.</li>
        </ul>
    </div>
    """
    clean_desc = JobNormalizer.clean_description(html_desc)
    assert clean_desc is not None
    assert "<" not in clean_desc
    assert ">" not in clean_desc
    assert "About the Role" in clean_desc
    assert "Senior Engineer" in clean_desc
    assert "Build high-throughput Python backends." in clean_desc

    # None and empty handling
    assert JobNormalizer.clean_description(None) is None
    assert JobNormalizer.clean_description("   ") is None


def test_normalize_workplace_type() -> None:
    """Verify workplace type canonicalization (remote, hybrid, onsite, None)."""
    assert JobNormalizer.normalize_workplace_type("remote") == "remote"
    assert (
        JobNormalizer.normalize_workplace_type(None, title="Remote Software Engineer") == "remote"
    )
    assert JobNormalizer.normalize_workplace_type(None, location="US (Virtual / WFH)") == "remote"

    assert JobNormalizer.normalize_workplace_type("hybrid") == "hybrid"
    assert JobNormalizer.normalize_workplace_type(None, title="Data Scientist (Hybrid)") == "hybrid"

    assert JobNormalizer.normalize_workplace_type("on-site") == "onsite"
    assert (
        JobNormalizer.normalize_workplace_type(None, location="San Francisco (In-Office)")
        == "onsite"
    )

    # None when no workplace type is stated (no fake guessing)
    assert (
        JobNormalizer.normalize_workplace_type(None, title="Accountant", location="Chicago, IL")
        is None
    )


def test_normalize_employment_type() -> None:
    """Verify employment type canonicalization (internship, full_time, contract, etc.)."""
    assert JobNormalizer.normalize_employment_type("Internship") == "internship"
    assert (
        JobNormalizer.normalize_employment_type(None, title="SWE Co-op Summer 2026") == "internship"
    )

    assert JobNormalizer.normalize_employment_type("Full-time") == "full_time"
    assert (
        JobNormalizer.normalize_employment_type(None, title="Permanent Senior Architect")
        == "full_time"
    )

    assert JobNormalizer.normalize_employment_type("Contractor") == "contract"
    assert JobNormalizer.normalize_employment_type("Part Time") == "part_time"

    assert JobNormalizer.normalize_employment_type(None, title="Engineer") is None


def test_extract_graduation_year() -> None:
    """Verify extraction of target graduation years from title and description."""
    assert (
        JobNormalizer.extract_graduation_year("Software Engineering Intern (Summer 2026)") == 2026
    )
    assert (
        JobNormalizer.extract_graduation_year(
            "Graduate Rotational Program",
            description="Targeted at the Class of 2025 graduating students.",
        )
        == 2025
    )
    assert JobNormalizer.extract_graduation_year("Senior Principal Engineer") is None


def test_fingerprint_determinism_and_stability() -> None:
    """Verify that fingerprint is completely deterministic and ignores volatile fields."""
    fp1 = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Backend Engineer",
        location="San Francisco, CA",
        clean_url="https://company.com/jobs/1",
    )
    fp2 = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Backend Engineer",
        location="San Francisco, CA",
        clean_url="https://company.com/jobs/1",
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 length

    # Minor title case or spacing differences normalize to same fingerprint
    fp_case_space = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="  backend   engineer  ",
        location="San Francisco, CA",
        clean_url="https://company.com/jobs/1",
    )
    assert fp1 == fp_case_space

    # Changing URL or location produces a different fingerprint
    fp_different_url = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Backend Engineer",
        location="San Francisco, CA",
        clean_url="https://company.com/jobs/2",
    )
    assert fp1 != fp_different_url

    # Different external_id with otherwise identical title, location, and URL do NOT collide
    fp_ext1 = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Software Engineer",
        location="Remote",
        clean_url="https://company.com/jobs/apply",
        external_id="1001",
    )
    fp_ext2 = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Software Engineer",
        location="Remote",
        clean_url="https://company.com/jobs/apply",
        external_id="1002",
    )
    assert fp_ext1 != fp_ext2

    # Same external_id across different companies produces different fingerprints
    fp_comp1 = JobNormalizer.compute_fingerprint(
        company_id=1,
        title="Software Engineer",
        location="Remote",
        clean_url="https://company.com/jobs/apply",
        external_id="1001",
    )
    fp_comp2 = JobNormalizer.compute_fingerprint(
        company_id=2,
        title="Software Engineer",
        location="Remote",
        clean_url="https://company.com/jobs/apply",
        external_id="1001",
    )
    assert fp_comp1 != fp_comp2


def test_content_hash_metadata_sensitivity() -> None:
    """Verify that content_hash captures changes in description and metadata."""
    desc = "Develop backend systems using Python."
    title = "Backend Engineer"
    base_hash = JobNormalizer.compute_content_hash(
        description=desc,
        title=title,
        location="San Francisco, CA",
        department="Core Platform",
        employment_type="full_time",
        job_type="remote",
        graduation_year=2026,
        application_url="https://acme.com/jobs/1",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )

    # Identical content and metadata yields identical hash
    same_hash = JobNormalizer.compute_content_hash(
        description=desc,
        title=title,
        location="San Francisco, CA",
        department="Core Platform",
        employment_type="full_time",
        job_type="remote",
        graduation_year=2026,
        application_url="https://acme.com/jobs/1",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert base_hash == same_hash

    # Description change changes hash
    changed_desc = JobNormalizer.compute_content_hash(
        description="Develop backend systems using Rust and Python.",
        title=title,
        location="San Francisco, CA",
        department="Core Platform",
        employment_type="full_time",
        job_type="remote",
        graduation_year=2026,
        application_url="https://acme.com/jobs/1",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert base_hash != changed_desc

    # Metadata change (department) changes hash even with identical description
    changed_dept = JobNormalizer.compute_content_hash(
        description=desc,
        title=title,
        location="San Francisco, CA",
        department="Infrastructure & Cloud",
        employment_type="full_time",
        job_type="remote",
        graduation_year=2026,
        application_url="https://acme.com/jobs/1",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert base_hash != changed_dept

    # Metadata change (employment_type) changes hash
    changed_emp = JobNormalizer.compute_content_hash(
        description=desc,
        title=title,
        location="San Francisco, CA",
        department="Core Platform",
        employment_type="contract",
        job_type="remote",
        graduation_year=2026,
        application_url="https://acme.com/jobs/1",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert base_hash != changed_emp


def test_full_normalization_pipeline() -> None:
    """Verify end-to-end normalization from RawJobPosting to NormalizedJob."""
    raw = RawJobPosting(
        source_ats="greenhouse",
        external_id="gh-456",
        title="2026 Software Engineer Intern",
        application_url="https://boards.greenhouse.io/corp/jobs/456?gh_src=linkedin",
        location="New York, NY",
        department="Core Infrastructure",
        team=None,
        description="<p>Join us for Summer 2026 to build APIs.</p>",
        employment_type="Internship",
        workplace_type="hybrid",
        published_at=datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
    )

    norm: NormalizedJob = JobNormalizer.normalize(raw, company_id=10)

    assert norm.company_id == 10
    assert norm.external_id == "gh-456"
    assert norm.title == "2026 Software Engineer Intern"
    assert norm.location == "New York, NY"
    assert norm.department == "Core Infrastructure"
    assert norm.employment_type == "internship"
    assert norm.job_type == "hybrid"
    assert norm.graduation_year == 2026
    assert norm.posted_at is not None
    assert "Join us for Summer 2026" in (norm.description or "")
    assert "<p>" not in (norm.description or "")
    assert "gh_src" not in norm.application_url
    assert len(norm.fingerprint_hash) == 64
    assert len(norm.content_hash) == 64
