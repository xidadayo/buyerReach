from app.modules import ai_coordinator
from app.modules.ai_coordinator import generate_task_plan


def test_local_plan_is_reviewable_and_never_executes() -> None:
    result = generate_task_plan(
        "寻找美国市场的中高端手袋品牌采购负责人",
        {"enabled": False, "api_key": ""},
        [{"name": "apollo-company-search", "type": "company_search"}],
    )

    assert result["source"] == "local_rules"
    assert result["task"]["mode"] == "brand_discovery"
    assert result["task"]["brand_keywords"] == []
    assert result["task"]["countries"] == ["United States"]
    assert result["task"]["categories"]
    assert any("AI 未配置" in warning for warning in result["warnings"])


def test_local_plan_separates_country_from_product_categories() -> None:
    result = generate_task_plan(
        "寻找法国的箱包、钱包和皮带品牌",
        {"enabled": False, "api_key": ""},
        [{"name": "hunter-discover", "type": "company_search"}],
    )

    assert result["task"]["countries"] == ["France"]
    assert len(result["task"]["categories"]) == 3
    assert result["task"]["category_match_mode"] == "any"
    assert result["task"]["brand_keywords"] == []
    assert "法国" not in result["task"]["categories"]


def test_local_plan_recognizes_argentina() -> None:
    result = generate_task_plan(
        "寻找阿根廷箱包品牌采购手",
        {"enabled": False, "api_key": ""},
        [{"name": "apollo-company-search", "type": "company_search"}],
    )

    assert result["task"]["mode"] == "brand_discovery"
    assert result["task"]["countries"] == ["Argentina"]
    assert result["task"]["categories"]


def test_exact_plan_does_not_require_categories() -> None:
    result = generate_task_plan(
        "精确查找 Nike 品牌采购负责人",
        {"enabled": False, "api_key": ""},
        [{"name": "apollo-company-search", "type": "company_search"}],
    )

    assert result["task"]["mode"] == "exact_brand"
    assert result["task"]["categories"] == []
    assert result["task"]["official_domains"] == []
    assert any("官方官网域名" in warning for warning in result["warnings"])


def test_exact_plan_extracts_official_domain() -> None:
    result = generate_task_plan(
        "精确查找 MANGO（官网 https://www.mango.com）的采购负责人",
        {"enabled": False, "api_key": ""},
        [{"name": "apollo-company-search", "type": "company_search"}],
    )

    assert result["task"]["official_domains"] == ["mango.com"]
    assert not any("官方官网域名" in warning for warning in result["warnings"])


def test_local_plan_preserves_scoped_phrases_without_industry_dictionary() -> None:
    result = generate_task_plan(
        "寻找美国时尚女包，工业皮带，数字钱包公司",
        {"enabled": False, "api_key": ""},
        [{"name": "hunter", "type": "company_search"}],
    )
    assert result["task"]["categories"] == ["时尚女包", "工业皮带", "数字钱包"]
    assert result["search_intent"]["source"] == "local_rules"
    assert result["search_intent"]["ambiguities"][0]["requires_confirmation"] is True


def test_ai_intent_is_repaired_before_strict_validation(monkeypatch) -> None:
    monkeypatch.setattr(ai_coordinator, "_request_chat_completion", lambda *_args: {
        "task": {"name": "AI scope", "mode": "brand_discovery", "countries": ["United States"],
                 "categories": ["fallback"], "brand_keywords": [], "min_relevance": 0.5},
        "search_intent": {"target_concepts": [
            {"source_text": "女包", "normalized_label": "时尚女包", "confidence": 0.95},
            {"source_text": "皮带", "normalized_label": "时尚皮带", "confidence": 0.92}],
            "global_qualifiers": [{"type": "industry_context", "value": "时尚/配饰",
                                   "applies_to": ["时尚女包", "时尚皮带"], "confidence": 0.94}],
            "business_types": ["brand", "retailer"], "overall_confidence": 0.93},
    })
    result = generate_task_plan("寻找美国时尚女包、皮带商家",
        {"enabled": True, "api_key": "secret", "model_name": "test", "base_url": "https://example.test"}, [])
    assert result["source"] == "ai"
    assert result["fallback_reason"] is None
    assert result["task"]["categories"] == ["时尚女包", "时尚皮带"]
    assert result["task"]["min_relevance"] == 50
    assert result["search_intent"]["overall_confidence"] == 93
    concept_ids = {item["id"] for item in result["search_intent"]["target_concepts"]}
    assert set(result["search_intent"]["global_qualifiers"][0]["applies_to"]) == concept_ids
