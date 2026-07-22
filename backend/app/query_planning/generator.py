"""Query slice generator — AI and local-rules paths.

Produces vendor-neutral slices from a SearchIntent. AI is optional;
local_rules always produces a minimal viable plan.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.concepts import SearchIntent
from app.query_planning.normalization import slice_normalized_hash


def _concept_label(concept: Any) -> str:
    """Best human-readable label for a TargetConcept."""
    return (
        getattr(concept, "normalized_label", None)
        or getattr(concept, "source_text", "")
    )


def generate_slices_from_intent(
    intent: dict[str, Any],
    *,
    generator_type: str = "local_rules",
    generator_version: str | None = None,
    preset: str = "balanced",
) -> list[dict[str, Any]]:
    """Return a list of vendor-neutral Query Slice dicts ready to be persisted.

    ``intent`` must be valid ``SearchIntent`` model data.
    """
    parsed = SearchIntent.model_validate(intent)

    slices: list[dict[str, Any]] = []

    # Core slices — one per target concept
    for idx, concept in enumerate(parsed.target_concepts):
        qualifiers = [q for q in parsed.global_qualifiers if concept.id in q.applies_to]
        countries = sorted({
            q.value for q in qualifiers if q.type == "country"
        })
        biz_types = list(parsed.business_types)

        label = _concept_label(concept).title() or f"Query {idx + 1}"

        # Synonyms / local-language slices only for balanced and volume
        if preset in {"balanced", "volume"}:
            synonyms = list(dict.fromkeys(
                concept.included_concepts or []
            ))[:3]
            for syn in synonyms:
                slices.append(_make_slice(
                    idx=len(slices),
                    label=f"{label} — {syn}",
                    purpose="synonym",
                    concept_ids=[concept.id],
                    countries=countries,
                    target_concepts=[syn],
                    business_types=biz_types,
                    include_terms=[],
                    exclude_terms=concept.excluded_concepts or [],
                    match_mode=parsed.category_match_mode,
                    reason=f"Generated synonym for {label}",
                    origin="generated",
                ))

        # Local-language slice for countries with non-English primary language
        if preset in {"balanced", "volume"} and countries:
            local_lang_countries = [
                c for c in countries
                if c.lower() in {"it", "fr", "de", "es", "jp", "kr", "cn", "nl", "pl", "tr", "ru", "br", "pt"}
            ]
            if local_lang_countries:
                slices.append(_make_slice(
                    idx=len(slices),
                    label=f"{label} (local language)",
                    purpose="local_language",
                    concept_ids=[concept.id],
                    countries=local_lang_countries,
                    target_concepts=[concept.normalized_label],
                    business_types=biz_types,
                    include_terms=[],
                    exclude_terms=concept.excluded_concepts or [],
                    match_mode=parsed.category_match_mode,
                    reason=f"Local-language search for {label} in {', '.join(local_lang_countries)}",
                    origin="generated",
                ))

        # Business type slices
        if preset == "volume" and biz_types:
            for bt in biz_types:
                slices.append(_make_slice(
                    idx=len(slices),
                    label=f"{label} — {bt}",
                    purpose="business_type",
                    concept_ids=[concept.id],
                    countries=countries,
                    target_concepts=[concept.normalized_label],
                    business_types=[bt],
                    include_terms=[],
                    exclude_terms=concept.excluded_concepts or [],
                    match_mode="all",
                    reason=f"Business-type-scoped search for {label}",
                    origin="generated",
                ))

        # Adjacent / exploratory for volume preset
        if preset == "volume":
            slices.append(_make_slice(
                idx=len(slices),
                label=f"{label} (adjacent types)",
                purpose="adjacent",
                concept_ids=[concept.id],
                countries=countries,
                target_concepts=[concept.normalized_label],
                business_types=[],
                include_terms=[],
                exclude_terms=concept.excluded_concepts or [],
                match_mode="any",
                reason=f"Broad adjacent-type search for {label}",
                origin="generated",
            ))

    # Ensure at least one core slice per concept
    existing_concepts = {
        cid for sl in slices for cid in sl["target_concept_ids"]
    }
    for concept in parsed.target_concepts:
        if concept.id in existing_concepts:
            continue
        qualifiers = [q for q in parsed.global_qualifiers if concept.id in q.applies_to]
        countries = sorted({q.value for q in qualifiers if q.type == "country"})
        slices.append(_make_slice(
            idx=len(slices),
            label=_concept_label(concept).title(),
            purpose="core",
            concept_ids=[concept.id],
            countries=countries,
            target_concepts=[concept.normalized_label],
            business_types=list(parsed.business_types),
            include_terms=[],
            exclude_terms=concept.excluded_concepts or [],
            match_mode=parsed.category_match_mode,
            reason=f"Core search for {_concept_label(concept)}",
            origin="generated",
        ))

    # Cap at 20 slices (design constraint)
    return slices[:20]


def _make_slice(
    idx: int,
    label: str,
    purpose: str,
    concept_ids: list[str],
    countries: list[str],
    target_concepts: list[str],
    business_types: list[str],
    include_terms: list[str],
    exclude_terms: list[str],
    match_mode: str,
    reason: str,
    origin: str = "generated",
) -> dict[str, Any]:
    nhash = slice_normalized_hash(
        countries=countries,
        target_concepts=target_concepts,
        business_types=business_types,
        include_terms=include_terms,
        exclude_terms=exclude_terms,
        match_mode=match_mode,
        purpose=purpose,
    )
    return {
        "slice_key": f"slice-{idx:03d}",
        "label": label[:255],
        "purpose": purpose,
        "target_concept_ids": list(dict.fromkeys(concept_ids)),
        "countries": sorted(set(countries)),
        "target_concepts": list(dict.fromkeys(target_concepts)),
        "business_types": list(dict.fromkeys(business_types)),
        "include_terms": list(dict.fromkeys(include_terms)),
        "exclude_terms": list(dict.fromkeys(exclude_terms)),
        "match_mode": match_mode,
        "priority": idx,
        "enabled": True,
        "origin": origin,
        "reason": reason[:2000],
        "normalized_hash": nhash,
    }


def plan_summary(intent: dict[str, Any], slices: list[dict[str, Any]]) -> str:
    """Return a one-sentence user-language summary of the generated plan."""
    parsed = SearchIntent.model_validate(intent)
    concepts = [_concept_label(c) for c in parsed.target_concepts]
    n_concepts = len(concepts)
    n_slices = len(slices)
    n_countries = len({
        q.value for q in parsed.global_qualifiers if q.type == "country"
    })

    parts: list[str] = []
    if n_countries:
        parts.append(f"{n_countries} 个国家")
    if n_concepts:
        concept_preview = "、".join(concepts[:3])
        if n_concepts > 3:
            concept_preview += f" 等{n_concepts}个品类"
        parts.append(concept_preview)
    if parsed.business_types:
        parts.append("、".join(parsed.business_types[:3]))

    return f"将为{'、'.join(parts)}寻找相关商家，系统已生成 {n_slices} 个查询方向。"
