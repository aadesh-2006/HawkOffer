"""Job normalization pipeline for standardizing raw scraped job postings."""

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from app.scrapers.base import RawJobPosting


class NormalizedJob(BaseModel):
    """Clean, standardized representation of a job posting ready for persistence."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    company_id: int
    title: str
    external_id: str | None = None
    department: str | None = None
    location: str | None = None
    description: str | None = None
    application_url: str
    employment_type: str | None = None
    job_type: str | None = None
    graduation_year: int | None = None
    posted_at: datetime | None = None
    fingerprint_hash: str
    content_hash: str
    source_ats: str = ""
    raw_metadata: dict[str, Any] = {}


class JobNormalizer:
    """Normalizes raw postings across all ATS platforms into standardized models."""

    TRACKING_PARAMS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "gh_src",
        "lever-origin",
        "lever-source",
        "source",
        "nl",
        "fbclid",
        "gclid",
    }

    @classmethod
    def clean_title(cls, title: str) -> str:
        """Strip excess whitespace and control characters from title."""
        if not title:
            return ""
        # Collapse whitespace and tabs
        cleaned = re.sub(r"\s+", " ", title).strip()
        # Remove trailing or leading dashes/pipes
        return cleaned.strip(" -|")

    @classmethod
    def clean_url(cls, raw_url: str) -> str:
        """Strip tracking parameters and fragments to produce a canonical URL."""
        if not raw_url:
            return ""

        parsed = urlparse(raw_url.strip())
        # Strip tracking query params
        query_params = parse_qsl(parsed.query, keep_blank_values=False)
        filtered_query = [(k, v) for k, v in query_params if k.lower() not in cls.TRACKING_PARAMS]
        # Deterministically sort query parameters
        filtered_query.sort(key=lambda x: (x[0], x[1]))

        # Rebuild URL without fragment and with filtered query
        clean = urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                "",
                urlencode(filtered_query),
                "",
            )
        )
        return clean

    @classmethod
    def clean_description(cls, raw_text: str | None) -> str | None:
        """Strip HTML tags and normalize formatting into readable plain text."""
        if not raw_text:
            return None

        text = raw_text.strip()
        if not text:
            return None

        # If text appears to contain HTML tags, strip them cleanly using BeautifulSoup
        if "<" in text and ">" in text:
            soup = BeautifulSoup(text, "html.parser")
            # Replace breaks and paragraphs with newlines
            for br in soup.find_all(["br", "p", "div", "li"]):
                br.append("\n")
            text = soup.get_text()

        # Collapse excess empty lines (max 2 consecutive newlines)
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned if cleaned else None

    @classmethod
    def normalize_workplace_type(
        cls,
        source_val: str | None,
        title: str = "",
        location: str = "",
    ) -> str | None:
        """Determine canonical workplace type ('remote', 'hybrid', 'onsite') or None."""
        combined = f"{source_val or ''} {title} {location}".lower()

        if "remote" in combined or "wfh" in combined or "virtual" in combined:
            return "remote"
        if "hybrid" in combined:
            return "hybrid"
        if "on-site" in combined or "onsite" in combined or "in-office" in combined:
            return "onsite"

        return None

    @classmethod
    def normalize_employment_type(cls, source_val: str | None, title: str = "") -> str | None:
        """Determine canonical employment type or None."""
        combined = f"{source_val or ''} {title}".lower()

        if any(w in combined for w in ["intern", "internship", "co-op", "coop"]):
            return "internship"
        if any(w in combined for w in ["contract", "contractor", "temporary", "temp"]):
            return "contract"
        if any(w in combined for w in ["part-time", "part time"]):
            return "part_time"
        if any(
            w in combined for w in ["full-time", "full time", "fulltime", "permanent", "regular"]
        ):
            return "full_time"

        return None

    @classmethod
    def extract_graduation_years(cls, title: str, description: str | None = None) -> list[int]:
        """Extract all explicitly eligible graduation cohort years (cohorts, ranges, sets).

        Avoids misinterpreting corporate founding dates or copyright years as student cohorts.
        """
        combined = f"{title} {description or ''}"
        years: set[int] = set()

        # 1. Range pattern: e.g. 2025-2027, 2025 to 2027
        for m in re.finditer(
            r"\b(202[4-9]|203[0-5])\s*(?:-|–|—|to)\s*(202[4-9]|203[0-5])\b",
            combined,
            re.IGNORECASE,
        ):
            y1, y2 = int(m.group(1)), int(m.group(2))
            if y1 <= y2 and y2 - y1 <= 5:
                years.update(range(y1, y2 + 1))

        # 2. Explicit cohort patterns (e.g. 'Class of 2025, 2026', 'graduating in 2026/2027')
        cohort_pattern = re.compile(
            r"(?:class\s+of|graduat\w+|cohort|internship\s+for|students?\s+in)\s+[^.;\n]{0,60}?\b(202[4-9]|203[0-5])\b",
            re.IGNORECASE,
        )
        for m in cohort_pattern.finditer(combined):
            years.add(int(m.group(1)))
            snippet = combined[m.end() : m.end() + 40]
            for ty in re.findall(
                r"(?:,|/|\bor\b|\band\b)\s*\b(202[4-9]|203[0-5])\b",
                snippet,
                re.IGNORECASE,
            ):
                years.add(int(ty))

        # 3. Direct slash pattern: e.g. 2025/2026
        for y1, y2 in re.findall(r"\b(202[4-9]|203[0-5])/(202[4-9]|203[0-5])\b", combined):
            years.add(int(y1))
            years.add(int(y2))

        # 4. Explicit year in job title (e.g. '2026 Summer Software Engineering Intern')
        for y in re.findall(r"\b(202[4-9]|203[0-5])\b", title):
            years.add(int(y))

        return sorted(years)

    @classmethod
    def extract_graduation_year(cls, title: str, description: str | None = None) -> int | None:
        """Extract primary anticipated graduation year if specified in role title or description."""
        years = cls.extract_graduation_years(title, description)
        if years:
            return years[0]

        # Fallback regex for legacy single year match
        combined = f"{title} {description or ''}"
        match = re.search(r"\b(202[4-9]|203[0-5])\b", combined)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def compute_fingerprint(
        cls,
        company_id: int,
        title: str,
        location: str | None,
        clean_url: str,
        external_id: str | None = None,
    ) -> str:
        """Generate a deterministic SHA-256 fingerprint hash for identity deduplication."""
        norm_title = re.sub(r"\W+", "", title.lower())
        norm_loc = re.sub(r"\W+", "", (location or "none").lower())
        norm_url = clean_url.strip().lower()

        if external_id and external_id.strip():
            norm_ext_id = external_id.strip().lower()
            raw_id = f"{company_id}:ext:{norm_ext_id}:{norm_title}:{norm_loc}:{norm_url}"
        else:
            raw_id = f"{company_id}:noext:{norm_title}:{norm_loc}:{norm_url}"

        return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()

    @classmethod
    def compute_content_hash(
        cls,
        description: str | None,
        title: str,
        location: str | None = None,
        department: str | None = None,
        employment_type: str | None = None,
        job_type: str | None = None,
        graduation_year: int | None = None,
        application_url: str | None = None,
        posted_at: datetime | None = None,
    ) -> str:
        """Generate a deterministic SHA-256 hash of all meaningful content and metadata."""
        posted_str = posted_at.isoformat() if posted_at else "none"
        components = [
            (description or "").strip(),
            title.strip(),
            (location or "").strip(),
            (department or "").strip(),
            (employment_type or "").strip(),
            (job_type or "").strip(),
            str(graduation_year) if graduation_year is not None else "none",
            (application_url or "").strip(),
            posted_str,
        ]
        content_repr = "|".join(components)
        return hashlib.sha256(content_repr.encode("utf-8")).hexdigest()

    @classmethod
    def normalize(cls, raw: RawJobPosting, company_id: int) -> NormalizedJob:
        """Execute full normalization pipeline on a RawJobPosting."""
        cleaned_title = cls.clean_title(raw.title)
        cleaned_url = cls.clean_url(raw.application_url)
        cleaned_desc = cls.clean_description(raw.description)

        location = raw.location.strip() if raw.location else None
        department = raw.department.strip() if raw.department else None
        if not department and raw.team:
            department = raw.team.strip()

        workplace_type = cls.normalize_workplace_type(
            raw.workplace_type, title=cleaned_title, location=location or ""
        )
        employment_type = cls.normalize_employment_type(raw.employment_type, title=cleaned_title)
        grad_year = cls.extract_graduation_year(cleaned_title, cleaned_desc)

        fingerprint = cls.compute_fingerprint(
            company_id=company_id,
            title=cleaned_title,
            location=location,
            clean_url=cleaned_url,
            external_id=raw.external_id,
        )
        content_hash = cls.compute_content_hash(
            description=cleaned_desc,
            title=cleaned_title,
            location=location,
            department=department,
            employment_type=employment_type,
            job_type=workplace_type,
            graduation_year=grad_year,
            application_url=cleaned_url,
            posted_at=raw.published_at,
        )

        return NormalizedJob(
            company_id=company_id,
            title=cleaned_title,
            external_id=raw.external_id,
            department=department,
            location=location,
            description=cleaned_desc,
            application_url=cleaned_url,
            employment_type=employment_type,
            job_type=workplace_type,
            graduation_year=grad_year,
            posted_at=raw.published_at,
            fingerprint_hash=fingerprint,
            content_hash=content_hash,
            source_ats=raw.source_ats,
            raw_metadata=raw.raw_data,
        )
