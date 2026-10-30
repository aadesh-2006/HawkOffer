"""Greenhouse Job Board API scraper adapter."""

import html
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.scrapers.base import BaseScraperAdapter, RawJobPosting
from app.scrapers.exceptions import PayloadError

logger = logging.getLogger(__name__)


class GreenhouseAdapter(BaseScraperAdapter):
    """Adapter for official public Greenhouse Job Board API."""

    name = "greenhouse"
    API_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"

    def extract_identifier(self, url_or_identifier: str) -> str:
        """Extract board token from Greenhouse URL or return slug directly."""
        cleaned = url_or_identifier.strip()
        if not cleaned:
            return ""

        if "://" in cleaned:
            parsed = urlparse(cleaned)
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            # Handle boards.greenhouse.io/{board_token} or job-boards.greenhouse.io/{board_token}
            # Or boards-api.greenhouse.io/v1/boards/{board_token}/jobs
            if (
                "boards-api.greenhouse.io" in parsed.netloc
                and len(parts) >= 3
                and parts[0] == "v1"
                and parts[1] == "boards"
            ):
                return parts[2]
            if len(parts) >= 1:
                return parts[0]
            return parsed.netloc

        return cleaned

    async def fetch_jobs(self, identifier: str) -> list[RawJobPosting]:
        """Fetch all public job postings for a Greenhouse board token."""
        board_token = self.extract_identifier(identifier)
        if not board_token:
            raise PayloadError(f"Invalid Greenhouse board token: '{identifier}'")

        url = self.API_TEMPLATE.format(board_token=board_token)
        response = await self._request(url, params={"content": "true"})
        payload = self._parse_json(response)

        if not isinstance(payload, dict) or "jobs" not in payload:
            raise PayloadError(
                f"Expected JSON object with 'jobs' key from Greenhouse API for '{board_token}'"
            )

        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise PayloadError(f"'jobs' field is not a list in response for '{board_token}'")

        postings: list[RawJobPosting] = []
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue

            raw_id = item.get("id")
            raw_title = item.get("title")
            raw_app_url = item.get("absolute_url")

            external_id = str(raw_id).strip() if raw_id is not None else ""
            title = str(raw_title).strip() if raw_title is not None else ""
            app_url = str(raw_app_url).strip() if raw_app_url is not None else ""

            if not external_id or not title:
                logger.warning(
                    "Skipping Greenhouse job with missing id or title: id=%s title=%s",
                    external_id,
                    title,
                )
                continue

            # Fallback URL if absolute_url is absent
            if not app_url:
                app_url = f"https://boards.greenhouse.io/{board_token}/jobs/{external_id}"

            # Extract location
            location = None
            loc_data = item.get("location")
            if isinstance(loc_data, dict):
                location = loc_data.get("name")
            elif isinstance(loc_data, str):
                location = loc_data

            if not location:
                offices = item.get("offices")
                if isinstance(offices, list) and offices:
                    office_names = [
                        str(o["name"])
                        for o in offices
                        if isinstance(o, dict) and o.get("name") is not None
                    ]
                    if office_names:
                        location = ", ".join(office_names)

            # Extract department
            department = None
            departments = item.get("departments")
            if isinstance(departments, list) and departments:
                dept_names = [
                    str(d["name"])
                    for d in departments
                    if isinstance(d, dict) and d.get("name") is not None
                ]
                if dept_names:
                    department = ", ".join(dept_names)

            # Extract content / description
            raw_content = item.get("content")
            description = None
            if raw_content:
                # Unescape HTML entities if needed
                description = html.unescape(str(raw_content)).strip()

            # Extract publication/update timestamp
            published_at = None
            date_str = item.get("updated_at") or item.get("created_at")
            if date_str:
                try:
                    dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                    published_at = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    logger.debug("Failed parsing Greenhouse date string: %s", date_str)

            posting = RawJobPosting(
                source_ats=self.name,
                external_id=external_id,
                title=title,
                application_url=app_url,
                location=location.strip() if location else None,
                department=department.strip() if department else None,
                description=description,
                published_at=published_at,
                raw_data=item,
            )
            postings.append(posting)

        logger.info(
            "Scraper [%s] successfully extracted %d postings for '%s'",
            self.name,
            len(postings),
            board_token,
        )
        return postings
