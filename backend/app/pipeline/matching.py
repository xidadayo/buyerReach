from __future__ import annotations

import hashlib
from typing import Any

from app.pipeline.concepts import CompanyProfile, ConceptMatch, SearchIntent
from app.pipeline.policy import RelevancePolicyV2


COMPANY_EVIDENCE = {"provider_company", "company_description", "official_website", "official_product_page"}
RELATION_POINTS = {"exact": 40, "synonym": 40, "child": 38, "descendant": 32,
                   "parent": 20, "related": 8, "conflicting": 0, "unknown": 0}


def plan_provider_queries(intent: SearchIntent, capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for concept in intent.target_concepts:
        qualifiers = [q for q in intent.global_qualifiers if concept.id in q.applies_to]
        countries = [q.value for q in qualifiers if q.type == "country"]
        contexts = [q.value for q in qualifiers if q.type == "industry_context"]
        terms = [*contexts, concept.normalized_label]
        query_text = " ".join(dict.fromkeys(term for term in terms if term)).strip()
        structured = {"countries": countries, "industry_contexts": contexts,
                      "business_types": intent.business_types, "concept": concept.normalized_label}
        applied = {
            "countries": countries,
            "keyword_tags": terms if capabilities.get("supports_keyword_tags") else [],
            "business_types": intent.business_types if capabilities.get("supports_business_type") else [],
            "industry_include": intent.target_industries if capabilities.get("supports_industry_include") else [],
        }
        digest = hashlib.sha256(f"{concept.id}:{query_text}".encode()).hexdigest()[:24]
        queries.append({"query_id": digest, "target_concept_ids": [concept.id],
                        "country": countries, "industry_context": contexts,
                        "business_types": intent.business_types, "query_text": query_text,
                        "structured_filters": structured, "applied_filters": applied})
    return queries


def evaluate_matches(intent_value: dict[str, Any], profile_value: dict[str, Any],
                     matches_value: list[dict[str, Any]]) -> dict[str, Any]:
    intent = SearchIntent.model_validate(intent_value)
    profile = CompanyProfile.model_validate(profile_value)
    matches = [ConceptMatch.model_validate(item) for item in matches_value]
    evidenced = [item for item in matches if item.evidence_level in COMPANY_EVIDENCE]
    if not profile.evidence or not evidenced:
        return RelevancePolicyV2().evaluate({"evaluation_status": "insufficient_data",
            "decision": "pending", "target_relevance_score": None, "intent_match_confidence": None,
            "matched_concepts": [], "conflicting_concepts": [], "dimension_scores": {},
            "penalties": [], "reason_codes": ["no_company_level_evidence"],
            "evidence_schema_version": "2.0.0"})
    allowed: dict[str, ConceptMatch] = {}
    conflicts: list[dict[str, Any]] = []
    targets = {item.id: item for item in intent.target_concepts}
    for match in evidenced:
        target = targets.get(match.target_concept_id)
        if target is None:
            continue
        if match.relationship == "conflicting" or not match.context_compatible:
            conflicts.append(match.model_dump())
            continue
        if match.relationship not in target.relation_scope or match.hierarchy_distance > target.max_hierarchy_distance:
            continue
        prior = allowed.get(target.id)
        if prior is None or RELATION_POINTS[match.relationship] > RELATION_POINTS[prior.relationship]:
            allowed[target.id] = match
    best_product = max((RELATION_POINTS[item.relationship] for item in allowed.values()), default=0)
    coverage = len(allowed) / len(intent.target_concepts)
    dimensions = {"product_fit": best_product, "industry_fit": 20 if any(item.context_compatible for item in allowed.values()) else 0,
                  "business_type_fit": 15 if not intent.business_types or set(intent.business_types) & set(profile.business_types) else 0,
                  "country_fit": 10 if profile.country_evidence else 0,
                  "evidence_quality": 10 if any(item.evidence_level.startswith("official_") for item in allowed.values()) else 6,
                  "category_coverage": round(5 * coverage)}
    penalties = [{"code": "concept_conflict", "points": -40 * len(conflicts)}] if conflicts else []
    return RelevancePolicyV2().evaluate({"evaluation_status": "completed", "decision": "pending",
        "target_relevance_score": None,
        "intent_match_confidence": round(sum(item.confidence for item in allowed.values()) / len(allowed)) if allowed else 0,
        "matched_concepts": [item.model_dump() for item in allowed.values()], "conflicting_concepts": conflicts,
        "dimension_scores": dimensions, "penalties": penalties,
        "reason_codes": ["bounded_concept_scope"], "evidence_schema_version": "2.0.0"})
