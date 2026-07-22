import json
import logging
import re
from uuid import NAMESPACE_URL, uuid5
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.modules.schemas import SearchTaskCreate
from app.modules.brand_discovery import COUNTRY_ALIAS_GROUPS
from app.pipeline.concepts import SearchIntent

logger = logging.getLogger(__name__)


def generate_task_plan(prompt: str, settings: dict, providers: list[dict]) -> dict:
    """Generate a reviewable search plan. This function never starts a task."""
    fallback = _local_plan(prompt, providers)
    if not settings.get("enabled") or not settings.get("api_key"):
        fallback["warnings"].append("AI 未配置或未启用，已使用本地规划规则。")
        fallback["ai_attempted"] = False
        fallback["fallback_reason"] = "ai_disabled"
        return fallback
    try:
        response = _request_chat_completion(prompt, settings, providers)
        plan = _normalise_plan(response, prompt, providers)
        plan["source"] = "ai"
        plan["ai_attempted"] = True
        plan["fallback_reason"] = None
        return plan
    except (
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        HTTPError,
        KeyError,
        IndexError,
    ) as exc:
        reason = f"{exc.__class__.__name__}: {str(exc)[:240]}"
        logger.warning("AI task plan degraded to local parser: %s", reason)
        fallback["warnings"].append(f"AI 解析失败，已使用本地兼容解析：{reason}")
        fallback["ai_attempted"] = True
        fallback["fallback_reason"] = reason
        return fallback


def _request_chat_completion(prompt: str, settings: dict, providers: list[dict]) -> dict:
    body = {
        "model": settings["model_name"],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a B2B buyer research coordinator. Return JSON only with keys task, search_intent, steps, warnings. "
                    "task must contain name, mode (brand_discovery or exact_brand), brand_keywords, countries, "
                    "official_domains, categories, category_match_mode, target_titles, contacts_limit_per_brand, require_website, min_relevance, "
                    "email_requirement, brand_limit. countries may contain geographic countries only; categories must "
                    "contain product or industry categories only, never countries. category_match_mode must be any so "
                    "each listed category is treated as an independent classification alternative. Preserve "
                    "each meaningful component of a compound target in categories. For brand_discovery, brand_keywords "
                    "and target_titles must be empty lists because this stage returns companies only; unknown brands "
                    "are discovered by countries plus categories. Do not plan contact or email retrieval for brand_discovery. "
                    "For exact_brand, brand_keywords contains the known target brand. Do not claim to execute work. "
                    "Keep inputs conservative. search_intent must use schema_version=2.0.0, original_prompt exactly as supplied, source=ai. "
                    "Every target_concept requires id, source_text, normalized_label, concept_type, required_contexts, excluded_contexts, "
                    "relation_scope (only exact/synonym/child/descendant), max_hierarchy_distance, minimum_evidence_level, "
                    "included_concepts, excluded_concepts, inherited_qualifiers, inferred, confidence. Every global_qualifier requires "
                    "type (country/industry_context/product_form/business_type), value, applies_to using target concept ids, source_text, "
                    "inferred and confidence. Also return target_industries, excluded_industries, business_types, category_match_mode, "
                    "ambiguities, overall_confidence and knowledge_sources as arrays/values, even when empty. "
                    "Propagate shared grammatical modifiers to coordinated concepts only when confident; preserve explicit local modifiers. "
                    "For a generic request for merchants, use retailer, wholesaler, distributor and importer. "
                    "For requests naming distributors, wholesalers, retailers, importers or buyers, preserve those commercial roles exactly; "
                    "do not replace them with brand. A procurement buyer is represented as business type buyer. "
                    "When the grammar and context clearly describe physical merchandise, add product_form=physical_goods to every applicable concept. "
                    "Bound ambiguous concepts with required/excluded contexts; for example a wallet in fashion accessories is a physical wallet and excludes digital services. "
                    "Coordination example: for '寻找美国时尚女包，皮带，钱包商家', fashion/accessories is a shared modifier and must apply to all three concepts; "
                    "the wallet is physical and excludes digital/software/financial contexts. Contrast: for '时尚女包，工业皮带，数字钱包公司', "
                    "keep fashion, industrial and digital as three local modifiers and never propagate one to another. "
                    "Confidence may use 0-100; do not use a 0-1 fraction. "
                    "Do not use an unbounded synonym expansion and do not assign a company relevance score."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"goal": prompt, "enabled_providers": providers}, ensure_ascii=False
                ),
            },
        ],
    }
    base_url = settings["base_url"].rstrip("/")
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = int(settings.get("request_timeout_seconds") or 60)
    with urlopen(request, timeout=timeout) as response:  # nosec B310: administrator configures the endpoint
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return json.loads(_json_object(content))


def _normalise_plan(value: dict, prompt: str, providers: list[dict]) -> dict:
    task_value = value.get("task") if isinstance(value.get("task"), dict) else value
    default = _local_plan(prompt, providers)["task"]
    try:
        merged_task = {
            **default,
            **{key: task_value[key] for key in default if key in task_value},
        }
        task = SearchTaskCreate.model_validate(
            _normalise_task_value(merged_task, prompt)
        ).model_dump()
        source = "ai"
    except Exception as exc:
        raise ValueError(f"AI task schema invalid: {exc}") from exc
    steps = [str(item).strip() for item in value.get("steps", []) if str(item).strip()][:6]
    result = {
        "task": task,
        "steps": steps or _steps(task),
        "warnings": [str(item).strip() for item in value.get("warnings", []) if str(item).strip()][
            :5
        ],
        "source": source,
    }
    if not isinstance(value.get("search_intent"), dict):
        raise ValueError("AI response omitted search_intent")
    intent = _normalise_search_intent(value["search_intent"], prompt)
    result["search_intent"] = intent.model_dump(mode="json")
    if task["mode"] == "brand_discovery":
        task["categories"] = [item.normalized_label for item in intent.target_concepts]
        task["company_types"] = intent.business_types
        task["target_titles"] = []
        scoped_countries = [
            item.value for item in intent.global_qualifiers if item.type == "country"
        ]
        if scoped_countries:
            task["countries"] = list(dict.fromkeys(scoped_countries))
    return result


def _normalise_task_value(value: dict, prompt: str) -> dict:
    result = dict(value)
    result["name"] = str(result.get("name") or f"AI：{prompt[:70]}")[:255]
    result["mode"] = (
        result.get("mode")
        if result.get("mode") in {"brand_discovery", "exact_brand"}
        else "brand_discovery"
    )
    for key in (
        "brand_keywords",
        "official_domains",
        "countries",
        "categories",
        "company_types",
        "target_titles",
    ):
        result[key] = _strings(result.get(key))
    try:
        relevance = float(result.get("min_relevance", 45))
    except (TypeError, ValueError):
        relevance = 45
    if 0 <= relevance <= 1:
        relevance *= 100
    result["min_relevance"] = max(0, min(100, round(relevance)))
    result["brand_limit"] = _bounded_int(result.get("brand_limit"), 100, 1, 5000)
    result["contacts_limit_per_brand"] = _bounded_int(
        result.get("contacts_limit_per_brand"), 5, 1, 50
    )
    result["category_match_mode"] = "all" if result.get("category_match_mode") == "all" else "any"
    result["require_website"] = bool(result.get("require_website", True))
    result["email_requirement"] = (
        result.get("email_requirement")
        if isinstance(result.get("email_requirement"), str) and result.get("email_requirement")
        else "valid_and_risky"
    )
    try:
        result["budget_limit"] = (
            max(0.0, float(result["budget_limit"]))
            if result.get("budget_limit") is not None
            and not isinstance(result.get("budget_limit"), bool)
            else None
        )
    except (TypeError, ValueError):
        result["budget_limit"] = None
    result["discovery_candidate_id"] = None
    result["original_prompt"] = prompt
    result["search_intent"] = None
    return result


def _normalise_search_intent(value: dict, prompt: str) -> SearchIntent:
    raw_concepts = (
        value.get("target_concepts") if isinstance(value.get("target_concepts"), list) else []
    )
    concepts: list[dict] = []
    aliases: dict[str, str] = {}
    for index, raw in enumerate(raw_concepts):
        item = raw if isinstance(raw, dict) else {"normalized_label": str(raw)}
        label = str(item.get("normalized_label") or item.get("source_text") or "").strip()
        if not label:
            continue
        source_text = str(item.get("source_text") or label).strip()
        concept_id = str(item.get("id") or uuid5(NAMESPACE_URL, f"{prompt}:{index}:{label}"))
        aliases[label.casefold()] = concept_id
        aliases[source_text.casefold()] = concept_id
        aliases[concept_id] = concept_id
        scope = [
            x
            for x in item.get("relation_scope", [])
            if x in {"exact", "synonym", "child", "descendant"}
        ]
        concepts.append(
            {
                "id": concept_id,
                "source_text": source_text,
                "normalized_label": label,
                "concept_type": item.get("concept_type")
                if item.get("concept_type") in {"product", "service", "industry", "business_type"}
                else "product",
                "required_contexts": _strings(item.get("required_contexts")),
                "excluded_contexts": _strings(item.get("excluded_contexts")),
                "relation_scope": scope or ["exact", "synonym", "child", "descendant"],
                "max_hierarchy_distance": _bounded_int(
                    item.get("max_hierarchy_distance"), 2, 0, 10
                ),
                "minimum_evidence_level": (
                    str(item.get("minimum_evidence_level"))
                    if item.get("minimum_evidence_level")
                    in {
                        "company_product",
                        "provider_company",
                        "company_description",
                        "official_website",
                        "official_product_page",
                    }
                    else "company_product"
                ),
                "included_concepts": _strings(item.get("included_concepts")),
                "excluded_concepts": _strings(item.get("excluded_concepts")),
                "inherited_qualifiers": _strings(item.get("inherited_qualifiers")),
                "inferred": bool(item.get("inferred", False)),
                "confidence": _confidence(item.get("confidence"), 70),
            }
        )
    if not concepts:
        raise ValueError("AI response contains no target concepts")
    concept_ids = [item["id"] for item in concepts]
    qualifiers: list[dict] = []
    for raw in value.get("global_qualifiers", []):
        if not isinstance(raw, dict) or raw.get("type") not in {
            "country",
            "industry_context",
            "product_form",
            "business_type",
        }:
            continue
        applies = [aliases.get(str(x).casefold(), str(x)) for x in raw.get("applies_to", [])]
        applies = [x for x in applies if x in concept_ids] or concept_ids
        qualifier_value = str(raw.get("value") or "").strip()
        if qualifier_value:
            qualifiers.append(
                {
                    "type": raw["type"],
                    "value": qualifier_value,
                    "applies_to": list(dict.fromkeys(applies)),
                    "source_text": str(raw.get("source_text") or qualifier_value),
                    "inferred": bool(raw.get("inferred", False)),
                    "confidence": _confidence(raw.get("confidence"), 70),
                }
            )
    ambiguities = [item for item in value.get("ambiguities", []) if isinstance(item, dict)]
    sources = [
        item if isinstance(item, dict) else {"type": str(item)}
        for item in value.get("knowledge_sources", [])
    ]
    return SearchIntent.model_validate(
        {
            "schema_version": "2.0.0",
            "original_prompt": prompt,
            "source": "ai",
            "global_qualifiers": qualifiers,
            "target_concepts": concepts,
            "target_industries": _strings(value.get("target_industries")),
            "excluded_industries": _strings(value.get("excluded_industries")),
            "business_types": _strings(value.get("business_types")),
            "category_match_mode": "all" if value.get("category_match_mode") == "all" else "any",
            "ambiguities": ambiguities,
            "overall_confidence": _confidence(value.get("overall_confidence"), 70),
            "knowledge_sources": sources
            or [{"type": "ai_task_generation", "version": "intent-2.0.2"}],
        }
    )


def _strings(value: object) -> list[str]:
    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _confidence(value: object, default: int) -> int:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    if 0 <= confidence <= 1:
        confidence *= 100
    return max(0, min(100, round(confidence)))


def _local_plan(prompt: str, providers: list[dict]) -> dict:
    normalized = re.sub(r"\s+", " ", prompt).strip()
    exact = any(token in normalized.lower() for token in ("精确", "指定品牌", "exact brand"))
    mode = "exact_brand" if exact else "brand_discovery"
    countries = _mentioned_countries(normalized)
    official_domains = _mentioned_domains(normalized) if mode == "exact_brand" else []
    local_categories = _local_target_phrases(normalized, countries)
    task = SearchTaskCreate(
        name=f"AI：{normalized[:70]}",
        mode=mode,
        brand_keywords=[normalized] if mode == "exact_brand" else [],
        official_domains=official_domains,
        countries=countries,
        categories=[] if mode == "exact_brand" else (local_categories or [normalized]),
        target_titles=[] if mode == "brand_discovery" else ["Head of Buying", "Sourcing Manager"],
        contacts_limit_per_brand=5,
        require_website=True,
        min_relevance=45,
        email_requirement="valid_and_risky",
        brand_limit=100,
    ).model_dump()
    warnings = []
    provider_types = {item["type"] for item in providers}
    if "company_search" not in provider_types:
        warnings.append("未启用企业/品牌搜索 Provider，确认前请先在系统配置中启用。")
    if mode == "exact_brand" and not official_domains:
        warnings.append(
            "精准品牌必须补充已确认的官方官网域名，否则任务不会启动，以避免命中同名公司。"
        )
    if mode == "brand_discovery":
        warnings.append(
            "本地解析会保留原始目标短语，不自动猜测修饰语传播范围；请确认业务语境后再创建。"
        )
    intent = None if mode == "exact_brand" else _local_search_intent(normalized, task)
    return {
        "task": task,
        "search_intent": intent.model_dump(mode="json") if intent else None,
        "steps": _steps(task),
        "warnings": warnings,
        "source": "local_rules",
        "ai_attempted": False,
        "fallback_reason": None,
    }


def _local_target_phrases(prompt: str, countries: list[str]) -> list[str]:
    text = prompt
    for alias in _country_aliases():
        text = re.sub(re.escape(alias), " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^(?:请|帮我|请帮我)?\s*(?:寻找|查找|搜索|获取|开发|find|search for|get)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:商家|企业|公司|供应商|品牌|零售商|批发商|分销商|vendors?|companies|suppliers?|brands?)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    phrases = []
    for item in re.split(r"[,，、;；\n]+|(?<=\S)(?:和|及|与)(?=\S)", text):
        cleaned = re.sub(r"^(?:的|面向|关于)\s*", "", item.strip(" ，,、;；"))
        if cleaned:
            phrases.append(cleaned)
    return list(dict.fromkeys(phrases))


def _local_search_intent(prompt: str, task: dict) -> SearchIntent:
    from app.pipeline.concepts import intent_from_legacy

    intent = intent_from_legacy(
        prompt,
        task["categories"],
        task["countries"],
        task.get("company_types", []),
        task.get("category_match_mode", "any"),
    )
    intent.ambiguities = [
        {
            "code": "modifier_scope_unresolved",
            "requires_confirmation": True,
            "message": "本地解析无法可靠判断修饰语是共享还是局部，请确认目标概念摘要。",
        }
    ]
    intent.knowledge_sources = [{"type": "local_syntax_preservation", "version": "2.0.2"}]
    return intent


def _country_aliases() -> list[str]:
    # Geography is generic parsing data, not an industry-specific scoring rule.
    return list({alias for group in COUNTRY_ALIAS_GROUPS for alias in group})


def _steps(task: dict) -> list[str]:
    steps = ["搜索目标企业或品牌", "返回候选公司列表"]
    if task["mode"] == "brand_discovery":
        return steps
    else:
        steps.extend(["解析品牌官网公开联系方式", "搜索目标职位联系人并验证邮箱"])
    return steps


def _mentioned_countries(prompt: str) -> list[str]:
    aliases = {
        # 北美
        "美国": "United States",
        "usa": "United States",
        "united states": "United States",
        "加拿大": "Canada",
        "canada": "Canada",
        "墨西哥": "Mexico",
        "mexico": "Mexico",
        # 欧洲
        "英国": "United Kingdom",
        "uk": "United Kingdom",
        "united kingdom": "United Kingdom",
        "德国": "Germany",
        "germany": "Germany",
        "法国": "France",
        "france": "France",
        "意大利": "Italy",
        "italy": "Italy",
        "西班牙": "Spain",
        "spain": "Spain",
        "荷兰": "Netherlands",
        "netherlands": "Netherlands",
        "比利时": "Belgium",
        "belgium": "Belgium",
        "瑞士": "Switzerland",
        "switzerland": "Switzerland",
        "瑞典": "Sweden",
        "sweden": "Sweden",
        "波兰": "Poland",
        "poland": "Poland",
        "葡萄牙": "Portugal",
        "portugal": "Portugal",
        "俄罗斯": "Russia",
        "russia": "Russia",
        # 亚太
        "日本": "Japan",
        "japan": "Japan",
        "韩国": "South Korea",
        "korea": "South Korea",
        "south korea": "South Korea",
        "澳大利亚": "Australia",
        "australia": "Australia",
        "新西兰": "New Zealand",
        "new zealand": "New Zealand",
        "印度": "India",
        "india": "India",
        "越南": "Vietnam",
        "vietnam": "Vietnam",
        "泰国": "Thailand",
        "thailand": "Thailand",
        "印尼": "Indonesia",
        "indonesia": "Indonesia",
        "菲律宾": "Philippines",
        "philippines": "Philippines",
        "马来西亚": "Malaysia",
        "malaysia": "Malaysia",
        "新加坡": "Singapore",
        "singapore": "Singapore",
        # 中东
        "阿联酋": "United Arab Emirates",
        "uae": "United Arab Emirates",
        "united arab emirates": "United Arab Emirates",
        "沙特": "Saudi Arabia",
        "saudi arabia": "Saudi Arabia",
        "saudi": "Saudi Arabia",
        "土耳其": "Turkey",
        "turkey": "Turkey",
        # 南美
        "巴西": "Brazil",
        "brazil": "Brazil",
        "阿根廷": "Argentina",
        "argentina": "Argentina",
        "智利": "Chile",
        "chile": "Chile",
        "哥伦比亚": "Colombia",
        "colombia": "Colombia",
        # 非洲
        "南非": "South Africa",
        "south africa": "South Africa",
    }
    text = prompt.lower()
    return [country for alias, country in aliases.items() if alias in text]


def _mentioned_domains(prompt: str) -> list[str]:
    matches = re.findall(
        r"(?i)(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+)",
        prompt,
    )
    return list(dict.fromkeys(match.lower().removeprefix("www.") for match in matches))


def _json_object(value: str) -> str:
    match = re.search(r"\{.*\}", value, re.DOTALL)
    if not match:
        raise ValueError("AI response did not contain a JSON object")
    return match.group(0)
