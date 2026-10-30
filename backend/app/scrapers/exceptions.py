"""Scraper exception hierarchy for HawkOffer."""


class ScraperError(Exception):
    """Base exception for all career platform scraper failures."""

    pass


class NotFoundError(ScraperError):
    """Raised when the requested career site or board token is not found (HTTP 404)."""

    pass


class RateLimitError(ScraperError):
    """Raised when rate limits are exceeded (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class HttpError(ScraperError):
    """Raised on unexpected HTTP status codes (e.g. HTTP 5xx server errors)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScraperTimeoutError(ScraperError):
    """Raised when a request exceeds configured timeouts."""

    pass


class PayloadError(ScraperError):
    """Raised when response body cannot be parsed or lacks expected structure."""

    pass
