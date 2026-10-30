"""Deterministic relevance scoring algorithms and text matching utilities."""

import re
from dataclasses import dataclass

# Centralized US States mapping for location normalization
US_STATES: dict[str, str] = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
    "dc": "district of columbia",
}
STATE_NAME_TO_CODE: dict[str, str] = {name: code for code, name in US_STATES.items()}

# Common industry role acronyms to expand during title normalization
ROLE_ACRONYMS: dict[str, str] = {
    "swe": "software engineer",
    "sre": "site reliability engineer",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "qa": "quality assurance",
    "pm": "product manager",
    "em": "engineering manager",
}


@dataclass(frozen=True)
class ScoringWeights:
    """Configurable weights for soft relevance scoring dimensions (sum = 100.0)."""

    role_weight: float = 35.0
    skills_weight: float = 35.0
    location_weight: float = 15.0
    workplace_type_weight: float = 10.0
    employment_type_weight: float = 5.0


def contains_keyword(text: str, keyword: str) -> bool:
    """Check if keyword appears in text with strict word boundary or symbolic delimiter handling.

    For single-character keywords (e.g. 'C', 'R'), hyphens are explicitly excluded as delimiters
    to prevent false positives such as 'Objective-C' matching 'C'.
    """
    if not text or not keyword:
        return False

    clean_kw = keyword.strip()
    if not clean_kw:
        return False

    escaped = re.escape(clean_kw)

    # For single-character languages (like 'C' or 'R'), do NOT treat hyphen '-' as a boundary
    if len(clean_kw) == 1:
        pattern = rf"(?:^|[\s,.;:!?/()\[\]\"']){escaped}(?:$|[\s,.;:!?/()\[\]\"'])"
    elif clean_kw.endswith(("+", "#")):
        # For C++, C#, permit version numbers or standard delimiters: e.g. C++17, C++20, C#10
        pattern = rf"(?:^|[\s,.;:!?/()\[\]\"'-]){escaped}(?:$|[\s,.;:!?/()\[\]\"'-]|\d+)"
    else:
        # For general terms (.NET, Node.js, Python), allow standard delimiters
        pattern = rf"(?:^|[\s,.;:!?/()\[\]\"'-]){escaped}(?:$|[\s,.;:!?/()\[\]\"'-])"

    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def expand_role_acronyms(text: str) -> str:
    """Expand recognized tech role acronyms (e.g. SWE -> software engineer)."""
    tokens = re.findall(r"\b\w+\b", text.lower())
    expanded: list[str] = []
    for t in tokens:
        if t in ROLE_ACRONYMS:
            expanded.append(ROLE_ACRONYMS[t])
        else:
            expanded.append(t)
    return " ".join(expanded)


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

    expanded_title = expand_role_acronyms(clean_title)
    expanded_title_tokens = set(re.findall(r"\b\w+\b", expanded_title))

    best_ratio = 0.0
    best_role: str | None = None
    best_match_type = "none"

    for role in preferred_roles:
        clean_role = re.sub(r"\s+", " ", role.strip().lower())
        if not clean_role:
            continue

        expanded_role = expand_role_acronyms(clean_role)

        # 1. Exact match (case and excess space normalized, original or acronym-expanded)
        if clean_title == clean_role or expanded_title == expanded_role:
            return 1.0, role, "exact"

        # 2. Phrase containment
        if (clean_role in clean_title) or (expanded_role in expanded_title):
            ratio = 0.85
            if ratio > best_ratio:
                best_ratio = ratio
                best_role = role
                best_match_type = "phrase"
            continue

        # 3. Token overlap (Jaccard-like subset of role tokens in title)
        role_tokens = set(re.findall(r"\b\w+\b", clean_role))
        exp_role_tokens = set(re.findall(r"\b\w+\b", expanded_role))

        # Check standard tokens
        if role_tokens:
            matching = role_tokens.intersection(title_tokens)
            overlap_ratio = len(matching) / len(role_tokens)
            scaled = overlap_ratio * 0.75
            if scaled > best_ratio:
                best_ratio = scaled
                best_role = role
                best_match_type = "token_overlap"

        # Check expanded tokens
        if exp_role_tokens:
            matching_exp = exp_role_tokens.intersection(expanded_title_tokens)
            overlap_exp = len(matching_exp) / len(exp_role_tokens)
            scaled_exp = overlap_exp * 0.75
            if scaled_exp > best_ratio:
                best_ratio = scaled_exp
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
    """Check if job location aligns with user's preferred locations using component parsing.

    Avoids false positives where 2-letter state codes match arbitrary substrings in city names
    (e.g., 'CA' in 'Chicago, IL' or 'IN' in 'Austin, TX').
    """
    if not preferred_locations:
        return True, None

    # Check remote preference
    remote_preferred = any(
        "remote" in loc.strip().lower()
        or "virtual" in loc.strip().lower()
        or "wfh" in loc.strip().lower()
        for loc in preferred_locations
    )
    if is_remote_job and remote_preferred:
        matched_loc = next(
            loc
            for loc in preferred_locations
            if any(k in loc.lower() for k in ("remote", "virtual", "wfh"))
        )
        return True, matched_loc

    if not job_location:
        return False, None

    clean_job_loc = job_location.strip().lower()

    # If job explicitly mentions remote/virtual in location string
    if ("remote" in clean_job_loc or "virtual" in clean_job_loc) and remote_preferred:
        matched_loc = next(
            loc
            for loc in preferred_locations
            if any(k in loc.lower() for k in ("remote", "virtual", "wfh"))
        )
        return True, matched_loc

    # Parse job location into normalized components
    job_parts = [p.strip() for p in re.split(r"[,/|;()]+", clean_job_loc) if p.strip()]
    job_tokens = set(job_parts)
    for p in job_parts:
        if p in US_STATES:
            job_tokens.add(US_STATES[p])
        if p in STATE_NAME_TO_CODE:
            job_tokens.add(STATE_NAME_TO_CODE[p])

    for pref in preferred_locations:
        clean_pref = pref.strip().lower()
        if not clean_pref:
            continue

        pref_parts = [p.strip() for p in re.split(r"[,/|;()]+", clean_pref) if p.strip()]

        # 1. Single-component preference (e.g. 'CA', 'California', 'Chicago')
        if len(pref_parts) == 1:
            code = pref_parts[0]
            canonical = {code}
            if code in US_STATES:
                canonical.add(US_STATES[code])
            if code in STATE_NAME_TO_CODE:
                canonical.add(STATE_NAME_TO_CODE[code])

            # Check exact component match against job tokens
            if canonical.intersection(job_tokens):
                return True, pref

            # Check whole-word match within job parts (e.g. 'San Francisco' in Bay Area)
            for jp in job_parts:
                if re.search(rf"\b{re.escape(code)}\b", jp):
                    return True, pref

        # 2. Multi-component preference (e.g. 'New York, NY', 'San Francisco, CA')
        else:
            matched_parts = 0
            for pp in pref_parts:
                canonical_pp = {pp}
                if pp in US_STATES:
                    canonical_pp.add(US_STATES[pp])
                if pp in STATE_NAME_TO_CODE:
                    canonical_pp.add(STATE_NAME_TO_CODE[pp])

                has_token_match = bool(canonical_pp.intersection(job_tokens))
                has_word_match = any(re.search(rf"\b{re.escape(pp)}\b", jp) for jp in job_parts)
                if has_token_match or has_word_match:
                    matched_parts += 1

            # Matches if all components match or primary city matches a job component
            if matched_parts == len(pref_parts) or (
                matched_parts >= 1 and pref_parts[0] in job_tokens
            ):
                return True, pref

    return False, None
