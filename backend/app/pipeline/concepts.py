from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Qualifier(StrictModel):
    type: Literal["country", "industry_context", "product_form", "business_type"]
    value: str
    applies_to: list[str] = Field(default_factory=list)
    source_text: str
    inferred: bool = False
    confidence: int = Field(ge=0, le=100)


class TargetConcept(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_text: str
    normalized_label: str
    concept_type: Literal["product", "service", "industry", "business_type"] = "product"
    required_contexts: list[str] = Field(default_factory=list)
    excluded_contexts: list[str] = Field(default_factory=list)
    relation_scope: list[Literal["exact", "synonym", "child", "descendant"]] = Field(
        default_factory=lambda: ["exact", "synonym", "child", "descendant"]
    )
    max_hierarchy_distance: int = Field(default=2, ge=0, le=10)
    minimum_evidence_level: str = "company_product"
    included_concepts: list[str] = Field(default_factory=list)
    excluded_concepts: list[str] = Field(default_factory=list)
    inherited_qualifiers: list[str] = Field(default_factory=list)
    inferred: bool = False
    confidence: int = Field(ge=0, le=100)


class SearchIntent(StrictModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    original_prompt: str
    source: Literal["ai", "local_rules", "manual"]
    global_qualifiers: list[Qualifier] = Field(default_factory=list)
    target_concepts: list[TargetConcept]
    target_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    business_types: list[str] = Field(default_factory=list)
    category_match_mode: Literal["any", "all"] = "any"
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    overall_confidence: int = Field(ge=0, le=100)
    knowledge_sources: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> "SearchIntent":
        ids = {item.id for item in self.target_concepts}
        if not ids:
            raise ValueError("At least one target concept is required")
        for qualifier in self.global_qualifiers:
            if not set(qualifier.applies_to).issubset(ids):
                raise ValueError("Qualifier applies_to contains an unknown concept")
        return self


class Evidence(StrictModel):
    field: str
    value: str
    url: str | None = None
    excerpt: str | None = None
    source_type: str
    confidence: int = Field(ge=0, le=100)


class CompanyProfile(StrictModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    industry: str | None = None
    industry_confidence: int | None = Field(default=None, ge=0, le=100)
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    business_types: list[str] = Field(default_factory=list)
    physical_goods: bool | None = None
    market_contexts: list[str] = Field(default_factory=list)
    country_evidence: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class ConceptMatch(StrictModel):
    target_concept_id: str
    company_concept: str
    relationship: Literal["exact", "synonym", "child", "descendant", "parent", "related", "conflicting", "unknown"]
    hierarchy_distance: int = Field(ge=0)
    context_compatible: bool
    evidence_level: Literal["provider_query", "provider_company", "company_description", "official_website", "official_product_page"]
    confidence: int = Field(ge=0, le=100)
    evidence_refs: list[str] = Field(default_factory=list)


def intent_from_legacy(prompt: str, categories: list[str], countries: list[str],
                       company_types: list[str], match_mode: str = "any") -> SearchIntent:
    """Compatibility adapter only; it does no industry-specific interpretation."""
    concepts = [TargetConcept(source_text=value, normalized_label=value.casefold(), confidence=60,
                              inferred=False) for value in categories if value.strip()]
    qualifiers = [Qualifier(type="country", value=value, applies_to=[c.id for c in concepts],
                            source_text=value, confidence=100) for value in countries if value.strip()]
    return SearchIntent(original_prompt=prompt, source="local_rules", global_qualifiers=qualifiers,
                        target_concepts=concepts, business_types=company_types,
                        category_match_mode="all" if match_mode == "all" else "any",
                        ambiguities=[{"code": "ai_unavailable", "requires_confirmation": True}],
                        overall_confidence=60,
                        knowledge_sources=[{"type": "task_context", "version": "2.0.0"}])
