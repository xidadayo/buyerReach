"""Deterministic normalization and stable hashing for query slices.

All slices pass through this before being saved or compared; the output is used
for the duplicate-detection unique constraint ``normalized_hash``.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

# ---------------------------------------------------------------------------
# Country → canonical ISO-3166 alpha-2 (subset sufficient for BuyerReach)
# ---------------------------------------------------------------------------
_COUNTRY_NORMALIZE: dict[str, str] = {
    # canonical self-mappings
    "it": "it", "fr": "fr", "de": "de", "es": "es", "gb": "gb",
    "uk": "gb", "us": "us", "ca": "ca", "au": "au", "jp": "jp",
    "kr": "kr", "cn": "cn", "nl": "nl", "be": "be", "ch": "ch",
    "se": "se", "no": "no", "dk": "dk", "fi": "fi", "pl": "pl",
    "at": "at", "pt": "pt", "ie": "ie", "nz": "nz", "sg": "sg",
    "hk": "hk", "tw": "tw", "mx": "mx", "br": "br", "in": "in",
    "tr": "tr", "ru": "ru", "za": "za", "ae": "ae", "il": "il",
    # common aliases
    "united kingdom": "gb", "united states": "us", "germany": "de",
    "france": "fr", "italy": "it", "spain": "es", "japan": "jp",
    "south korea": "kr", "china": "cn", "netherlands": "nl",
    "belgium": "be", "switzerland": "ch", "sweden": "se",
    "norway": "no", "denmark": "dk", "finland": "fi", "poland": "pl",
    "austria": "at", "portugal": "pt", "ireland": "ie",
    "new zealand": "nz", "singapore": "sg", "hong kong": "hk",
    "taiwan": "tw", "mexico": "mx", "brazil": "br", "india": "in",
    "turkey": "tr", "russia": "ru", "south africa": "za",
    "uae": "ae", "israel": "il",
}


def normalize_country(value: str) -> str | None:
    """Return canonical ISO-3166 alpha-2 code or None."""
    cleaned = value.strip().casefold()
    if not cleaned:
        return None
    if len(cleaned) == 2 and cleaned.isascii():
        return _COUNTRY_NORMALIZE.get(cleaned, cleaned)
    return _COUNTRY_NORMALIZE.get(cleaned)


def normalize_text(value: str) -> str:
    """Unicode NFKC, trim, casefold."""
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def normalize_list(values: list[str]) -> list[str]:
    """Deduplicate sorted casefolded list, dropping empties."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        n = normalize_text(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    out.sort()
    return out


def slice_normalized_hash(
    countries: list[str],
    target_concepts: list[str],
    business_types: list[str],
    include_terms: list[str],
    exclude_terms: list[str],
    match_mode: str,
    purpose: str,
) -> str:
    """Produce a stable SHA-256 hex digest for duplicate detection.

    The hash input is a deterministically ordered JSON object so that two
    semantically identical slices always produce the same digest regardless
    of the *order* the caller passed arrays in.
    """
    payload: dict[str, Any] = {
        "countries": normalize_list(countries),
        "target_concepts": normalize_list(target_concepts),
        "business_types": normalize_list(business_types),
        "include_terms": normalize_list(include_terms),
        "exclude_terms": normalize_list(exclude_terms),
        "match_mode": normalize_text(match_mode),
        "purpose": normalize_text(purpose),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def input_hash(payload: dict[str, Any]) -> str:
    """Stable hash for a SliceRun input (used in idempotency key)."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
