"""Deterministic relevance scoring algorithms and text matching utilities."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringWeights:
    """Configurable weights for soft relevance scoring dimensions (sum = 100.0)."""

    role_weight: float = 35.0
    skills_weight: float = 35.0
    location_weight: float = 15.0
    workplace_type_weight: float = 10.0
    employment_type_weight: float = 5.0


def contains_keyword(text: str, keyword: str) -> bool:
    """Check if keyword appears in text with strict word boundary or symbolic delimiter handling."""
    if not text or not keyword:
        return False

    clean_kw = keyword.strip()
    if not clean_kw:
        return False

    # For symbols like C++, C#, .NET where standard \b fails on non-alphanumeric trailing chars
    escaped = re.escape(clean_kw)
    pattern = rf"(?:^|[\s,.;:!?/()\[\]\"'-]){escaped}(?:$|[\s,.;:!?/()\[\]\"'-])"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def compute_title_similarity(
    job_title: str, preferred_roles: list[str]
) -> tuple[float, str | None, str]:
    """Calculate role relevance score (0.0 to 1.0) and identify matched role and match type.

    Returns:
        (ratio, matched_role, match_type):
            ratio: float between 0.0 and 1.0
            matched_role: name of the best matching preferred role or None
            match_type: 'exact', 'phrase', 'token_overlap', or 'none'
    """
    if not job_title or not preferred_roles:
        return 0.0, None, "none"

    clean_title = re.sub(r"\s+", " ", job_title.strip().lower())
    title_tokens = set(re.findall(r"\b\w+\b", clean_title))

    best_ratio = 0.0
    best_role: str | None = None
    best_match_type = "none"

    for role in preferred_roles:
        clean_role = re.sub(r"\s+", " ", role.strip().lower())
        if not clean_role:
            continue

        role_tokens = set(re.findall(r"\b\w+\b", clean_role))
        if not role_tokens:
            continue

        # 1. Exact match (case and excess space normalized)
        if clean_title == clean_role:
            return 1.0, role, "exact"

        # 2. Phrase containment (e.g. "Software Engineer" inside "Senior Software Engineer, Core")
        if clean_role in clean_title:
            ratio = 0.85
            if ratio > best_ratio:
                best_ratio = ratio
                best_role = role
                best_match_type = "phrase"
            continue

        # 3. Token overlap (Jaccard-like subset of role tokens in title)
        matching_tokens = role_tokens.intersection(title_tokens)
        if matching_tokens:
            overlap_ratio = len(matching_tokens) / len(role_tokens)
            # Scale overlap: if all tokens of role appear in title (even if reordered), 0.75
            scaled_ratio = overlap_ratio * 0.75
            if scaled_ratio > best_ratio:
                best_ratio = scaled_ratio
                best_role = role
                best_match_type = "token_overlap"

    return best_ratio, best_role, best_match_type


def match_skills(text: str, preferred_skills: list[str]) -> tuple[list[str], list[str]]:
    """Identify matched and unmatched skills without double-counting identical skills."""
    if not preferred_skills:
        return [], []

    matched: list[str] = []
    unmatched: list[str] = []

    # Deduplicate while preserving case and order
    seen_skills: set[str] = set()
    deduped_skills: list[str] = []
    for skill in preferred_skills:
        normalized = skill.strip().lower()
        if normalized and normalized not in seen_skills:
            seen_skills.add(normalized)
            deduped_skills.append(skill.strip())

    for skill in deduped_skills:
        if contains_keyword(text, skill):
            matched.append(skill)
        else:
            unmatched.append(skill)

    return matched, unmatched


def match_locations(
    job_location: str | None,
    is_remote_job: bool,
    preferred_locations: list[str],
) -> tuple[bool, str | None]:
    """Check if job location aligns with user's preferred locations.

    Returns:
        (is_match, matched_preferred_location)
    """
    if not preferred_locations:
        return True, None

    # Check if user accepts remote and job is remote
    remote_preferred = any(
        "remote" in loc.strip().lower() or "virtual" in loc.strip().lower()
        for loc in preferred_locations
    )
    if is_remote_job and remote_preferred:
        matched_loc = next(
            loc
            for loc in preferred_locations
            if "remote" in loc.lower() or "virtual" in loc.lower()
        )
        return True, matched_loc

    if not job_location:
        return False, None

    clean_job_loc = job_location.strip().lower()

    for pref in preferred_locations:
        clean_pref = pref.strip().lower()
        if not clean_pref:
            continue

        # Handle "remote" match in job location text
        if clean_pref in ("remote", "virtual") and (
            "remote" in clean_job_loc or "virtual" in clean_job_loc
        ):
            return True, pref

        # City or Region substring match (e.g., "San Francisco" in "San Francisco, CA")
        if clean_pref in clean_job_loc or clean_job_loc in clean_pref:
            return True, pref

    return False, None
