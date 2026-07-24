"""Fuzzy duplicate detection using difflib (stdlib, no extra deps)."""

import difflib
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.models import Brand, Contact, EmailAddress


def find_fuzzy_brands(db: Session, threshold: float = 0.85, limit: int = 5000, organization_id=None) -> list[dict]:
    """Find pairs of brands with similar names."""
    brands = db.query(Brand.id, Brand.name, Brand.normalized_name).filter(
        Brand.deleted_at.is_(None), Brand.organization_id == organization_id
    ).order_by(Brand.normalized_name).limit(limit).all()

    return _fuzzy_pairs(
        [(b.id, b.name, b.normalized_name) for b in brands],
        threshold,
    )


def find_fuzzy_contacts(db: Session, threshold: float = 0.85, limit: int = 5000, organization_id=None) -> list[dict]:
    """Find pairs of contacts with similar names."""
    contacts = db.query(Contact.id, Contact.full_name).filter(
        Contact.deleted_at.is_(None), Contact.organization_id == organization_id
    ).order_by(Contact.full_name).limit(limit).all()

    return _fuzzy_pairs(
        [(c.id, c.full_name, c.full_name.lower()) for c in contacts],
        threshold,
    )


def find_fuzzy_emails(db: Session, threshold: float = 0.90, limit: int = 5000, organization_id=None) -> list[dict]:
    """Find pairs of emails with similar local parts on the same domain."""
    from sqlalchemy import func

    # Group emails by domain, then compare local parts within each domain
    domains = db.query(EmailAddress.domain, func.count(EmailAddress.id)).filter(
        EmailAddress.deleted_at.is_(None), EmailAddress.organization_id == organization_id
    ).group_by(EmailAddress.domain).having(func.count(EmailAddress.id) > 1).limit(100).all()

    results: list[dict] = []
    for domain, _ in domains:
        emails = db.query(EmailAddress.id, EmailAddress.normalized_address).filter(
            EmailAddress.deleted_at.is_(None),
            EmailAddress.organization_id == organization_id,
            EmailAddress.domain == domain,
        ).limit(100).all()

        pairs = _fuzzy_pairs(
            [(e.id, e.normalized_address, e.normalized_address.split("@")[0]) for e in emails],
            threshold,
        )
        results.extend(pairs)

    return results


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _fuzzy_pairs(
    records: list[tuple[UUID, str, str]],
    threshold: float,
) -> list[dict]:
    """Compare all pairs of records using token_sort_ratio, return those above threshold."""
    results: list[dict] = []
    n = len(records)
    if n < 2:
        return results

    # Compare each record only to subsequent ones (avoid duplicate pairs)
    for i in range(n):
        id_a, name_a, key_a = records[i]
        for j in range(i + 1, n):
            id_b, name_b, key_b = records[j]
            ratio = difflib.SequenceMatcher(None, key_a, key_b).ratio()
            if ratio >= threshold:
                results.append({
                    "id_a": str(id_a),
                    "id_b": str(id_b),
                    "name_a": name_a,
                    "name_b": name_b,
                    "similarity": round(ratio * 100, 1),
                })

    # Sort by similarity descending
    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:200]  # Cap at 200
