"""Infer email addresses from contact name + domain using common corporate patterns."""

import re

# Pattern: (template, confidence)
# Confidence is a rough prior based on how common each pattern is.
PATTERNS: list[tuple[str, int]] = [
    ("{first}.{last}", 75),
    ("{first_initial}{last}", 65),
    ("{first}{last_initial}", 50),
    ("{first}_{last}", 55),
    ("{first_initial}.{last}", 60),
    ("{first}", 30),
    ("{last}", 25),
    ("{first}{last}", 40),
    ("{first_initial}_{last}", 45),
    ("{last}{first_initial}", 35),
]


def infer_emails(
    first_name: str,
    last_name: str,
    domain: str,
    min_confidence: int = 30,
) -> list[dict]:
    """Generate email candidates for a contact at a given domain.

    Returns a list of dicts with keys: address, pattern, confidence.
    """
    first = _sanitize(first_name)
    last = _sanitize(last_name)
    domain = domain.strip().lower()

    if not first or not last or not domain:
        return []

    first_initial = first[0] if first else ""
    last_initial = last[0] if last else ""

    results: list[dict] = []
    for template, confidence in PATTERNS:
        if confidence < min_confidence:
            continue
        local = template.format(
            first=first,
            last=last,
            first_initial=first_initial,
            last_initial=last_initial,
        )
        results.append({
            "address": f"{local}@{domain}",
            "pattern": template,
            "confidence": confidence,
        })

    return results


def _sanitize(value: str) -> str:
    """Normalize a name part for email generation: lowercase, strip special chars."""
    value = value.strip().lower()
    # Replace accented characters
    value = re.sub(r"[àáâãäå]", "a", value)
    value = re.sub(r"[èéêë]", "e", value)
    value = re.sub(r"[ìíîï]", "i", value)
    value = re.sub(r"[òóôõö]", "o", value)
    value = re.sub(r"[ùúûü]", "u", value)
    value = re.sub(r"[ñ]", "n", value)
    # Remove everything except a-z and dots and dashes
    value = re.sub(r"[^a-z.\-]", "", value)
    # Collapse multiple dots/dashes
    value = re.sub(r"\.{2,}", ".", value)
    value = re.sub(r"-{2,}", "-", value)
    # Strip leading/trailing dots and dashes
    return value.strip(".-")
