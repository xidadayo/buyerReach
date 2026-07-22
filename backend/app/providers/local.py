import re


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "unknown"


def title_priority(title: str) -> int:
    title_l = title.lower()
    if any(word in title_l for word in ["buyer", "buying", "sourcing", "procurement"]):
        return 100
    if any(word in title_l for word in ["product", "merchandising", "category", "production"]):
        return 80
    if any(word in title_l for word in ["founder", "owner", "director"]):
        return 70
    if any(word in title_l for word in ["intern", "student", "recruiter", "former"]):
        return 0
    return 50
