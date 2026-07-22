import pytest

from app.pipeline.concepts import CompanyProfile, Evidence, SearchIntent, TargetConcept, Qualifier
from app.pipeline.matching import evaluate_matches, plan_provider_queries


def intent(context: str, labels: list[str]) -> SearchIntent:
    concepts = [TargetConcept(source_text=x, normalized_label=x, required_contexts=[context], confidence=95) for x in labels]
    return SearchIntent(original_prompt="fixture", source="ai", target_concepts=concepts,
        global_qualifiers=[Qualifier(type="industry_context", value=context,
            applies_to=[x.id for x in concepts], source_text=context, confidence=95)],
        business_types=["supplier"], overall_confidence=95)


@pytest.mark.parametrize("context,labels", [
    ("fashion accessories", ["handbag", "belt", "physical wallet"]),
    ("industrial conveying", ["conveyor belt", "roller", "bearing"]),
    ("pet supplies", ["pet collar", "pet leash", "pet backpack"]),
])
def test_new_industries_use_the_same_query_planner(context, labels):
    value = intent(context, labels)
    result = plan_provider_queries(value, {"supports_keyword_tags": True})
    assert len(result) == 3
    assert all(context in item["query_text"] for item in result)


def test_provider_query_only_is_insufficient_data():
    value = intent("context", ["target"])
    profile = CompanyProfile(evidence=[])
    result = evaluate_matches(value.model_dump(), profile.model_dump(), [{
        "target_concept_id": value.target_concepts[0].id, "company_concept": "target",
        "relationship": "exact", "hierarchy_distance": 0, "context_compatible": True,
        "evidence_level": "provider_query", "confidence": 99, "evidence_refs": []}])
    assert result["target_relevance_score"] is None
    assert result["rating"] == "Pending"


def test_conflict_is_deterministically_penalized():
    value = intent("physical goods", ["wallet"])
    profile = CompanyProfile(products=["wallet"], evidence=[Evidence(field="products", value="wallet",
        url="https://example.test/product", source_type="official_product_page", confidence=95)])
    result = evaluate_matches(value.model_dump(), profile.model_dump(), [{
        "target_concept_id": value.target_concepts[0].id, "company_concept": "digital wallet",
        "relationship": "conflicting", "hierarchy_distance": 0, "context_compatible": False,
        "evidence_level": "official_product_page", "confidence": 95, "evidence_refs": ["0"]}])
    assert result["target_relevance_score"] == 0
    assert result["decision"] == "rejected"


def test_official_product_evidence_produces_a_bounded_percentage():
    value = intent("fashion accessories", ["handbag", "belt"])
    profile = CompanyProfile(
        industry="Retail - Apparel & Accessories", products=["designer handbags", "leather belts"],
        business_types=["retailer"], physical_goods=True, country_evidence={"headquarters": "US"},
        evidence=[Evidence(field="company_profile", value="designer handbags, leather belts",
            url="https://example.test", excerpt="Shop handbags and belts",
            source_type="official_website", confidence=96)])
    matches = [{
        "target_concept_id": concept.id, "company_concept": product,
        "relationship": "child", "hierarchy_distance": 1, "context_compatible": True,
        "evidence_level": "official_website", "confidence": 94, "evidence_refs": ["0"]}
        for concept, product in zip(value.target_concepts, profile.products, strict=True)]

    result = evaluate_matches(value.model_dump(), profile.model_dump(), matches)

    assert result["evaluation_status"] == "completed"
    assert result["target_relevance_score"] == 83
    assert result["rating"] == "B"
