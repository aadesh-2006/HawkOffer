"""Registry and factory for career platform scraper adapters."""

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.scrapers.base import BaseScraperAdapter
from app.scrapers.exceptions import ScraperError
from app.scrapers.greenhouse import GreenhouseAdapter
from app.scrapers.lever import LeverAdapter

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """Registry maintaining available ATS scraper adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[BaseScraperAdapter] | Callable[..., BaseScraperAdapter]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in adapters."""
        self.register(GreenhouseAdapter.name, GreenhouseAdapter)
        self.register(LeverAdapter.name, LeverAdapter)

    def register(
        self,
        platform: str,
        adapter_cls: type[BaseScraperAdapter] | Callable[..., BaseScraperAdapter],
    ) -> None:
        """Register a new adapter class under a platform name."""
        key = platform.strip().lower()
        self._adapters[key] = adapter_cls
        logger.debug("Registered scraper adapter for platform '%s'", key)

    def get(self, platform: str, **kwargs: Any) -> BaseScraperAdapter:
        """Instantiate and return the scraper adapter for the given platform."""
        key = platform.strip().lower()
        adapter_cls = self._adapters.get(key)
        if not adapter_cls:
            raise ScraperError(
                f"Unsupported platform '{platform}'. Supported platforms: {self.list_supported()}"
            )
        return adapter_cls(**kwargs)

    def is_supported(self, platform: str) -> bool:
        """Check if a platform name is registered."""
        return platform.strip().lower() in self._adapters

    def list_supported(self) -> list[str]:
        """Return list of supported platform names."""
        return sorted(self._adapters.keys())

    def detect_platform(self, url: str) -> str | None:
        """Detect ATS platform name based on URL domain and path patterns."""
        if not url:
            return None
        parsed = urlparse(url.lower())
        netloc = parsed.netloc

        if "greenhouse.io" in netloc:
            return "greenhouse"
        if "lever.co" in netloc:
            return "lever"

        return None


# Global registry singleton
registry = ScraperRegistry()
