"""Lever Postings API scraper adapter."""

import html
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.exceptions import PayloadError

logger = logging.getLogger(__name__)


class LeverAdapter(BaseScraperAdapter):
    """Adapter for official public Lever Postings API."""

    name = "lever"
    API_TEMPLATE = "https://api.lever.co/v0/postings/{site}?mode=json"

    def extract_identifier(self, url_or_identifier: str) -> str:
        """Extract site slug from Lever career URL or return identifier."""
        cleaned = url_or_identifier.strip()
        if not cleaned:
            return ""

        if "://" in cleaned:
            parsed = urlparse(cleaned)
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            # Handle api.lever.co/v0/postings/{site}
            if (
                "api.lever.co" in parsed.netloc
                and len(parts) >= 3
                and parts[0] == "v0"
                and parts[1] == "postings"
            ):
                return parts[2]
            # Handle jobs.lever.co/{site}
            if len(parts) >= 1:
                return parts[0]
            return parsed.netloc

        return cleaned

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        """Fetch all public job postings for a Lever site identifier."""
        site = self.extract_identifier(identifier)
        if not site:
            raise PayloadError(f"Invalid Lever site identifier: '{identifier}'")

        url = self.API_TEMPLATE.format(site=site)
        response = await self._request(url)
        payload = self._parse_json(response)

        if not isinstance(payload, list):
            raise PayloadError(
                f"Expected JSON list from Lever API for '{site}', got {type(payload).__name__}"
            )

        postings: list[RawJobPosting] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            raw_id = item.get("id")
            raw_title = item.get("text")
            raw_url = item.get("hostedUrl") or item.get("applyUrl")

            external_id = str(raw_id).strip() if raw_id is not None else ""
            title = str(raw_title).strip() if raw_title is not None else ""
            app_url = str(raw_url).strip() if raw_url is not None else ""

            if not external_id or not title:
                logger.warning(
                    "Skipping Lever job with missing id or title: id=%s title=%s",
                    external_id,
                    title,
                )
                continue

            # Fallback URL if missing
            if not app_url:
                app_url = f"https://jobs.lever.co/{site}/{external_id}"

            # Extract categories
            categories = item.get("categories") or {}
            location = None
            department = None
            team = None
            employment_type = None

            if isinstance(categories, dict):
                location = categories.get("location")
                department = categories.get("department")
                team = categories.get("team")
                employment_type = categories.get("commitment")

            # Extract workplace type (e.g. 'remote', 'hybrid', 'on-site')
            raw_workplace = item.get("workplaceType")
            workplace_type = raw_workplace.strip() if isinstance(raw_workplace, str) else None

            # Extract description (prefer plain text if provided by Lever)
            description = item.get("descriptionPlain")
            if not description:
                raw_desc = item.get("description")
                if raw_desc:
                    description = html.unescape(str(raw_desc)).strip()

            # Additional notes from Lever (often contains requirements or qualifications)
            additional = item.get("additionalPlain") or item.get("additional")
            if additional and description:
                description = f"{description}\n\n{html.unescape(str(additional)).strip()}"
            elif additional and not description:
                description = html.unescape(str(additional)).strip()

            # Extract created timestamp (epoch ms)
            published_at = None
            created_at_ms = item.get("createdAt")
            if isinstance(created_at_ms, (int, float)) and created_at_ms > 0:
                try:
                    published_at = datetime.fromtimestamp(created_at_ms / 1000.0, tz=UTC)
                except (ValueError, OSError):
                    logger.debug("Failed parsing Lever epoch timestamp: %s", created_at_ms)

            posting = RawJobPosting(
                source_ats=self.name,
                external_id=external_id,
                title=title,
                application_url=app_url,
                location=str(location).strip() if location else None,
                department=str(department).strip() if department else None,
                team=str(team).strip() if team else None,
                description=description,
                employment_type=str(employment_type).strip() if employment_type else None,
                workplace_type=workplace_type,
                published_at=published_at,
                raw_data=item,
            )
            postings.append(posting)

        logger.info(
            "Scraper [%s] successfully extracted %d postings for '%s'",
            self.name,
            len(postings),
            site,
        )
        return postings
