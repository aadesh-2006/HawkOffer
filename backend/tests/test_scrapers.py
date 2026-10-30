"""Tests for Greenhouse and Lever scraper adapters, registry, and HTTP resilience."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.scrapers.exceptions import (
    NotFoundError,
    PayloadError,
    RateLimitError,
    ScraperError,
    ScraperTimeoutError,
)
from app.scrapers.greenhouse import GreenhouseAdapter
from app.scrapers.lever import LeverAdapter
from app.scrapers.registry import ScraperRegistry

# --- Greenhouse Adapter Tests ---


def test_greenhouse_extract_identifier() -> None:
    """Verify Greenhouse identifier extraction from various URL patterns and slugs."""
    adapter = GreenhouseAdapter()
    assert adapter.extract_identifier("stripe") == "stripe"
    assert adapter.extract_identifier("  airbnb  ") == "airbnb"
    assert adapter.extract_identifier("https://boards.greenhouse.io/stripe") == "stripe"
    assert adapter.extract_identifier("https://boards.greenhouse.io/stripe/") == "stripe"
    assert adapter.extract_identifier("https://job-boards.greenhouse.io/figma/jobs/123") == "figma"
    assert (
        adapter.extract_identifier("https://boards-api.greenhouse.io/v1/boards/datadog/jobs")
        == "datadog"
    )
    assert adapter.extract_identifier("") == ""


@pytest.mark.asyncio
async def test_greenhouse_fetch_jobs_success() -> None:
    """Verify successful parsing of Greenhouse API payload with all fields."""
    sample_payload = {
        "jobs": [
            {
                "id": 101,
                "title": "Senior Backend Engineer",
                "absolute_url": "https://boards.greenhouse.io/stripe/jobs/101",
                "location": {"name": "San Francisco, CA"},
                "departments": [{"id": 1, "name": "Engineering"}],
                "offices": [{"id": 2, "name": "San Francisco HQ"}],
                "content": "&lt;p&gt;Build distributed banking APIs.&lt;/p&gt;",
                "updated_at": "2026-10-25T14:30:00Z",
            },
            {
                "id": 102,
                "title": "Product Designer",
                "absolute_url": "https://boards.greenhouse.io/stripe/jobs/102",
                "location": None,
                "offices": [{"name": "New York"}],
                "departments": [],
                "content": "<p>Design intuitive UI.</p>",
                "updated_at": None,
            },
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(
        status_code=200,
        content=json.dumps(sample_payload).encode("utf-8"),
        request=httpx.Request("GET", "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"),
    )
    mock_client.get.return_value = mock_response

    adapter = GreenhouseAdapter(client=mock_client)
    postings = await adapter.fetch_jobs("stripe")

    assert len(postings) == 2

    job1 = postings[0]
    assert job1.external_id == "101"
    assert job1.title == "Senior Backend Engineer"
    assert job1.location == "San Francisco, CA"
    assert job1.department == "Engineering"
    assert job1.description == "<p>Build distributed banking APIs.</p>"
    assert job1.published_at is not None
    assert job1.published_at.year == 2026

    job2 = postings[1]
    assert job2.external_id == "102"
    assert job2.location == "New York"
    assert job2.department is None
    assert job2.published_at is None


@pytest.mark.asyncio
async def test_greenhouse_empty_and_malformed_payload() -> None:
    """Verify empty job lists and malformed JSON payloads."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    adapter = GreenhouseAdapter(client=mock_client)

    # Empty list
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=b'{"jobs": []}',
        request=httpx.Request("GET", "https://api.test"),
    )
    assert await adapter.fetch_jobs("test") == []

    # Malformed JSON (missing 'jobs' key)
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=b'{"status": "error"}',
        request=httpx.Request("GET", "https://api.test"),
    )
    with pytest.raises(PayloadError, match="Expected JSON object with 'jobs' key"):
        await adapter.fetch_jobs("test")

    # Non-JSON content
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=b"<html>Internal Error</html>",
        request=httpx.Request("GET", "https://api.test"),
    )
    with pytest.raises(PayloadError, match="Malformed JSON response"):
        await adapter.fetch_jobs("test")


@pytest.mark.asyncio
async def test_greenhouse_explicit_null_fields_skipped() -> None:
    """Verify that jobs with explicit null id or title are skipped rather than parsed as 'None'."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    adapter = GreenhouseAdapter(client=mock_client)

    payload = {
        "jobs": [
            {"id": None, "title": "Engineer with null id", "absolute_url": "https://url"},
            {"id": 201, "title": None, "absolute_url": "https://url"},
            {"id": "", "title": "Empty id", "absolute_url": "https://url"},
            {"id": 202, "title": "Valid Engineer", "absolute_url": "https://url"},
        ]
    }
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=json.dumps(payload).encode("utf-8"),
        request=httpx.Request("GET", "https://api.test"),
    )

    postings = await adapter.fetch_jobs("test")
    assert len(postings) == 1
    assert postings[0].external_id == "202"
    assert postings[0].title == "Valid Engineer"


# --- Lever Adapter Tests ---


def test_lever_extract_identifier() -> None:
    """Verify Lever site identifier extraction from URLs and slugs."""
    adapter = LeverAdapter()
    assert adapter.extract_identifier("netflix") == "netflix"
    assert adapter.extract_identifier("  atlassian ") == "atlassian"
    assert adapter.extract_identifier("https://jobs.lever.co/netflix") == "netflix"
    assert adapter.extract_identifier("https://jobs.lever.co/netflix/") == "netflix"
    assert adapter.extract_identifier("https://api.lever.co/v0/postings/palantir") == "palantir"
    assert adapter.extract_identifier("") == ""


@pytest.mark.asyncio
async def test_lever_fetch_jobs_success() -> None:
    """Verify successful parsing of Lever API payload with categories and workplace types."""
    sample_payload = [
        {
            "id": "lever-uuid-1",
            "text": "Machine Learning Intern",
            "hostedUrl": "https://jobs.lever.co/netflix/lever-uuid-1",
            "applyUrl": "https://jobs.lever.co/netflix/lever-uuid-1/apply",
            "categories": {
                "commitment": "Internship",
                "department": "AI Research",
                "team": "Recommendation Algorithms",
                "location": "Los Gatos, CA",
            },
            "workplaceType": "hybrid",
            "descriptionPlain": "Build recommendation models.",
            "additionalPlain": "Must be graduating in 2026.",
            "createdAt": 1730000000000,
        },
        {
            "id": "lever-uuid-2",
            "text": "Site Reliability Engineer",
            "hostedUrl": "https://jobs.lever.co/netflix/lever-uuid-2",
            "categories": {},
            "workplaceType": None,
            "description": "<p>Maintain infra.</p>",
            "createdAt": None,
        },
    ]

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=json.dumps(sample_payload).encode("utf-8"),
        request=httpx.Request("GET", "https://api.lever.co/v0/postings/netflix"),
    )

    adapter = LeverAdapter(client=mock_client)
    postings = await adapter.fetch_jobs("netflix")

    assert len(postings) == 2

    job1 = postings[0]
    assert job1.external_id == "lever-uuid-1"
    assert job1.title == "Machine Learning Intern"
    assert job1.location == "Los Gatos, CA"
    assert job1.department == "AI Research"
    assert job1.team == "Recommendation Algorithms"
    assert job1.employment_type == "Internship"
    assert job1.workplace_type == "hybrid"
    assert "Build recommendation models." in (job1.description or "")
    assert "Must be graduating in 2026." in (job1.description or "")
    assert job1.published_at is not None

    job2 = postings[1]
    assert job2.external_id == "lever-uuid-2"
    assert job2.location is None
    assert job2.published_at is None


@pytest.mark.asyncio
async def test_lever_empty_and_malformed_payload() -> None:
    """Verify Lever handles empty arrays and non-list payloads."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    adapter = LeverAdapter(client=mock_client)

    # Empty array
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=b"[]",
        request=httpx.Request("GET", "https://api.test"),
    )
    assert await adapter.fetch_jobs("test") == []

    # Non-list JSON
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=b'{"error": "not found"}',
        request=httpx.Request("GET", "https://api.test"),
    )
    with pytest.raises(PayloadError, match="Expected JSON list from Lever API"):
        await adapter.fetch_jobs("test")


@pytest.mark.asyncio
async def test_lever_explicit_null_fields_skipped() -> None:
    """Verify that Lever postings with explicit null id or text are skipped."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    adapter = LeverAdapter(client=mock_client)

    payload = [
        {"id": None, "text": "Role with null id", "hostedUrl": "https://url"},
        {"id": "lev-1", "text": None, "hostedUrl": "https://url"},
        {"id": "lev-2", "text": "Valid Role", "hostedUrl": "https://url"},
    ]
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        content=json.dumps(payload).encode("utf-8"),
        request=httpx.Request("GET", "https://api.test"),
    )

    postings = await adapter.fetch_jobs("test")
    assert len(postings) == 1
    assert postings[0].external_id == "lev-2"
    assert postings[0].title == "Valid Role"


# --- HTTP Reliability, Error Handling and Retry Tests ---


@pytest.mark.asyncio
async def test_http_404_not_found() -> None:
    """Verify HTTP 404 raises NotFoundError immediately without retrying."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = httpx.Response(
        status_code=404,
        content=b"Not Found",
        request=httpx.Request("GET", "https://api.test/missing"),
    )

    adapter = GreenhouseAdapter(client=mock_client, max_retries=3)
    with pytest.raises(NotFoundError, match="Target career board not found"):
        await adapter.fetch_jobs("missing_slug")

    # Verify only 1 request was attempted (no wasteful retries for 404)
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_http_429_rate_limit_and_retry() -> None:
    """Verify HTTP 429 retries with exponential backoff and eventually raises RateLimitError."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = httpx.Response(
        status_code=429,
        headers={"Retry-After": "0.01"},
        content=b"Too Many Requests",
        request=httpx.Request("GET", "https://api.test"),
    )

    adapter = LeverAdapter(client=mock_client, max_retries=2, backoff_factor=0.01)
    with pytest.raises(RateLimitError, match="Rate limit exceeded"):
        await adapter.fetch_jobs("ratelimited")

    # Original attempt + 2 retries = 3 attempts total
    assert mock_client.get.call_count == 3


@pytest.mark.asyncio
async def test_http_429_http_date_retry_after() -> None:
    """Verify HTTP 429 parses RFC 7231 HTTP-date Retry-After headers without crashing."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = httpx.Response(
        status_code=429,
        headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        content=b"Too Many Requests",
        request=httpx.Request("GET", "https://api.test"),
    )

    adapter = GreenhouseAdapter(client=mock_client, max_retries=1, backoff_factor=0.01)
    with pytest.raises(RateLimitError, match="Rate limit exceeded"):
        await adapter.fetch_jobs("board")

    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_http_500_transient_recovery() -> None:
    """Verify transient 5xx server errors retry and recover when subsequent call succeeds."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # Fail first call with 503, succeed second call with 200
    mock_client.get.side_effect = [
        httpx.Response(
            status_code=503,
            content=b"Service Unavailable",
            request=httpx.Request("GET", "https://api.test"),
        ),
        httpx.Response(
            status_code=200,
            content=b'{"jobs": [{"id": 1, "title": "Dev", "absolute_url": "https://url"}]}',
            request=httpx.Request("GET", "https://api.test"),
        ),
    ]

    adapter = GreenhouseAdapter(client=mock_client, max_retries=2, backoff_factor=0.01)
    postings = await adapter.fetch_jobs("recovered")

    assert len(postings) == 1
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_http_timeout_exhaustion() -> None:
    """Verify request timeouts retry up to max_retries and raise TimeoutError."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.TimeoutException("Read timed out")

    adapter = LeverAdapter(client=mock_client, max_retries=2, backoff_factor=0.01)
    with pytest.raises(ScraperTimeoutError, match="Request timed out"):
        await adapter.fetch_jobs("slowsite")

    assert mock_client.get.call_count == 3


# --- Scraper Registry Tests ---


def test_registry_registration_and_lookup() -> None:
    """Verify registry manages adapters and platform detection."""
    reg = ScraperRegistry()
    assert "greenhouse" in reg.list_supported()
    assert "lever" in reg.list_supported()
    assert reg.is_supported("greenhouse") is True
    assert reg.is_supported("lever") is True
    assert reg.is_supported("unknown_ats") is False

    # Get adapter instances
    gh = reg.get("Greenhouse")
    assert isinstance(gh, GreenhouseAdapter)
    lev = reg.get("LEVER")
    assert isinstance(lev, LeverAdapter)

    # Unknown adapter raises ScraperError
    with pytest.raises(ScraperError, match="Unsupported platform 'workday'"):
        reg.get("workday")


def test_registry_platform_detection() -> None:
    """Verify registry auto-detects ATS platform from URLs."""
    reg = ScraperRegistry()
    assert reg.detect_platform("https://boards.greenhouse.io/stripe") == "greenhouse"
    assert reg.detect_platform("https://job-boards.greenhouse.io/airbnb/jobs") == "greenhouse"
    assert reg.detect_platform("https://jobs.lever.co/netflix") == "lever"
    assert reg.detect_platform("https://api.lever.co/v0/postings/palantir") == "lever"
    assert reg.detect_platform("https://careers.google.com") is None
    assert reg.detect_platform("") is None
