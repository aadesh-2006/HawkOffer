"""Base scraper adapter interface and common raw job model."""

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.scrapers.exceptions import (
    HttpError,
    NotFoundError,
    PayloadError,
    RateLimitError,
    ScraperTimeoutError,
)

logger = logging.getLogger(__name__)


class RawJobPosting(BaseModel):
    """Normalized carrier of raw data scraped from an Applicant Tracking System."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_ats: str
    external_id: str | None = None
    title: str
    application_url: str
    location: str | None = None
    department: str | None = None
    team: str | None = None
    description: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    published_at: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class BaseScraperAdapter(ABC):
    """Abstract base class for all career platform scraper adapters."""

    name: str = "base"
    default_timeout: float = 15.0
    default_max_retries: int = 3
    default_backoff_factor: float = 0.5

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    ) -> None:
        self._external_client = client
        self.timeout = timeout or self.default_timeout
        self.max_retries = max_retries if max_retries is not None else self.default_max_retries
        self.backoff_factor = (
            backoff_factor if backoff_factor is not None else self.default_backoff_factor
        )

    @abstractmethod
    def extract_identifier(self, url_or_identifier: str) -> str:
        """Extract ATS slug/board token from full URL or return clean identifier."""
        pass

    @abstractmethod
    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        """Fetch available job postings for the given identifier."""
        pass

    async def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute an asynchronous HTTP GET request with retries and exponential backoff."""
        req_headers = {
            "User-Agent": "HawkOffer-CareerMonitor/1.0 (Open-Source Career Assistant)",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        close_client = False
        client = self._external_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            close_client = True

        try:
            for attempt in range(self.max_retries + 1):
                try:
                    logger.debug(
                        "Scraper [%s] requesting URL %s (attempt %d/%d)",
                        self.name,
                        url,
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    response = await client.get(url, params=params, headers=req_headers)

                    # Successful response
                    if response.status_code == 200:
                        return response

                    # Handle 404 (Not Found) immediately without retries
                    if response.status_code == 404:
                        logger.warning(
                            "Scraper [%s] resource not found (HTTP 404) at %s", self.name, url
                        )
                        raise NotFoundError(f"Target career board not found: {url}")

                    # Handle 429 (Rate Limited)
                    if response.status_code == 429:
                        retry_after_hdr = response.headers.get("Retry-After")
                        jitter = random.uniform(0.1, 0.5)
                        retry_delay = (self.backoff_factor * (2**attempt)) + jitter
                        if retry_after_hdr:
                            try:
                                retry_delay = float(retry_after_hdr.strip()) + jitter
                            except ValueError:
                                try:
                                    target_dt = parsedate_to_datetime(retry_after_hdr.strip())
                                    diff = (target_dt - datetime.now(UTC)).total_seconds()
                                    retry_delay = max(0.0, diff) + jitter
                                except Exception:
                                    pass

                        # Cap individual delay at 60s
                        retry_delay = min(retry_delay, 60.0)

                        if attempt < self.max_retries:
                            logger.warning(
                                "Scraper [%s] rate limited (429). Retrying in %.2fs",
                                self.name,
                                retry_delay,
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        raise RateLimitError(
                            f"Rate limit exceeded for {url}", retry_after=retry_delay
                        )

                    # Handle 5xx Server Errors (Retryable)
                    if response.status_code >= 500:
                        if attempt < self.max_retries:
                            delay = min(
                                (self.backoff_factor * (2**attempt)) + random.uniform(0.1, 0.5),
                                60.0,
                            )
                            logger.warning(
                                "Scraper [%s] received HTTP %d. Retrying in %.2fs",
                                self.name,
                                response.status_code,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise HttpError(
                            f"Server error HTTP {response.status_code} from {url}",
                            status_code=response.status_code,
                        )

                    # Other client errors (4xx) - do not retry permanent client errors
                    preview = response.text[:150]
                    raise HttpError(
                        f"HTTP request failed with status {response.status_code}: {preview}",
                        status_code=response.status_code,
                    )

                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        delay = min(
                            (self.backoff_factor * (2**attempt)) + random.uniform(0.1, 0.5),
                            60.0,
                        )
                        logger.warning(
                            "Scraper [%s] timeout on %s. Retrying in %.2fs: %s",
                            self.name,
                            url,
                            delay,
                            exc,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise ScraperTimeoutError(f"Request timed out for {url}") from exc

                except (httpx.ConnectError, httpx.NetworkError) as exc:
                    if attempt < self.max_retries:
                        delay = min(
                            (self.backoff_factor * (2**attempt)) + random.uniform(0.1, 0.5),
                            60.0,
                        )
                        logger.warning(
                            "Scraper [%s] network error on %s. Retrying in %.2fs: %s",
                            self.name,
                            url,
                            delay,
                            exc,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise HttpError(
                        f"Network connection failed for {url}: {exc}", status_code=0
                    ) from exc

            raise HttpError(f"Max retries exceeded for {url}", status_code=0)

        finally:
            if close_client:
                await client.aclose()

    def _parse_json(self, response: httpx.Response) -> Any:
        """Safely parse JSON response body or raise PayloadError."""
        try:
            return response.json()
        except Exception as exc:
            raise PayloadError(f"Malformed JSON response from {response.url}: {exc}") from exc
