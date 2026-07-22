"""Helpers for keyword-driven brand discovery."""

import re
from collections.abc import Iterable
from urllib.parse import urlparse


PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)
CATEGORY_ALIASES = {
    "bag": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "backpack",
        "luggage",
        "leather goods",
        "包",
        "手袋",
        "女包",
        "箱包",
        "背包",
        "钱包",
    ),
    "bags": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "backpack",
        "luggage",
        "leather goods",
        "包",
        "手袋",
        "女包",
        "箱包",
        "背包",
        "钱包",
    ),
    "handbag": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "leather goods",
        "包",
        "手袋",
        "女包",
        "箱包",
    ),
    "handbags": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "leather goods",
        "包",
        "手袋",
        "女包",
        "箱包",
    ),
    "包": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "backpack",
        "luggage",
        "leather goods",
        "箱包",
        "手袋",
        "女包",
        "背包",
        "钱包",
    ),
    "箱包": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "backpack",
        "luggage",
        "leather goods",
        "包",
        "手袋",
        "女包",
        "背包",
        "钱包",
    ),
    "手袋": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "leather goods",
        "包",
        "箱包",
        "女包",
    ),
    "女包": (
        "bag",
        "bags",
        "handbag",
        "handbags",
        "purse",
        "purses",
        "tote",
        "clutch",
        "satchel",
        "leather goods",
        "包",
        "箱包",
        "手袋",
    ),
    "背包": ("bag", "bags", "backpack", "backpacks", "rucksack", "luggage", "包", "箱包"),
    "钱包": ("wallet", "wallets", "purse", "purses", "small leather goods", "皮具", "包", "箱包"),
    "belt": ("belt", "belts", "waist belt", "leather belt", "皮带", "腰带"),
    "belts": ("belt", "belts", "waist belt", "leather belt", "皮带", "腰带"),
    "皮带": ("belt", "belts", "waist belt", "leather belt", "腰带"),
    "腰带": ("belt", "belts", "waist belt", "leather belt", "皮带"),
    "pet": ("pet", "pets", "pet supplies", "宠物", "宠物用品"),
    "apparel": ("apparel", "clothing", "fashion", "garment", "服装", "服饰"),
    "beauty": ("beauty", "cosmetics", "skincare", "makeup", "美容", "化妆品", "护肤"),
    "furniture": ("furniture", "home furnishing", "家居", "家具"),
}
EXCLUDED_INDUSTRY_TERMS = (
    "plastic packaging",
    "flexible packaging",
    "industrial packaging",
    "sterilization",
    "agricultural substrate",
    "marketing agency",
    "shipping supplies",
)
LEGAL_NAME_SUFFIXES = {
    "inc",
    "incorporated",
    "ltd",
    "limited",
    "llc",
    "llp",
    "plc",
    "co",
    "company",
    "corp",
    "corporation",
    "gmbh",
    "sa",
    "sarl",
    "bv",
    "ag",
}
BRAND_COUNTRY_FIELDS = (
    ("headquarters_country", "总部国家"),
    ("registered_country", "注册国家"),
    ("origin_country", "品牌起源国家"),
)
BRAND_COUNTRY_SCOPES = {"headquarters", "registered", "origin"}
COUNTRY_ALIAS_GROUPS = (
    ("us", "usa", "united states", "united states of america", "美国"),
    ("gb", "uk", "united kingdom", "great britain", "英国"),
    ("cn", "china", "people's republic of china", "中国", "中国大陆"),
    ("hk", "hong kong", "香港"),
    ("tw", "taiwan", "台湾", "中国台湾"),
    ("it", "italy", "意大利"),
    ("fr", "france", "法国"),
    ("de", "germany", "德国"),
    ("es", "spain", "西班牙"),
    ("pt", "portugal", "葡萄牙"),
    ("nl", "netherlands", "the netherlands", "荷兰"),
    ("be", "belgium", "比利时"),
    ("ch", "switzerland", "瑞士"),
    ("at", "austria", "奥地利"),
    ("ie", "ireland", "爱尔兰"),
    ("se", "sweden", "瑞典"),
    ("no", "norway", "挪威"),
    ("dk", "denmark", "丹麦"),
    ("fi", "finland", "芬兰"),
    ("pl", "poland", "波兰"),
    ("ca", "canada", "加拿大"),
    ("au", "australia", "澳大利亚", "澳洲"),
    ("nz", "new zealand", "新西兰"),
    ("jp", "japan", "日本"),
    ("kr", "south korea", "republic of korea", "korea", "韩国"),
    ("sg", "singapore", "新加坡"),
    ("in", "india", "印度"),
    ("br", "brazil", "巴西"),
    ("mx", "mexico", "墨西哥"),
    ("ae", "united arab emirates", "uae", "阿联酋"),
    ("sa", "saudi arabia", "沙特阿拉伯", "沙特"),
    ("tr", "turkey", "türkiye", "土耳其"),
    ("za", "south africa", "南非"),
    ("ar", "argentina", "阿根廷"),
)
COUNTRY_ALIASES = {
    re.sub(r"[^\w]+", "", alias.casefold()): group[0]
    for group in COUNTRY_ALIAS_GROUPS
    for alias in group
}


def build_discovery_query(filters: dict, template: str | None = None) -> str:
    """Build a provider query from task filters and an optional config template."""
    values = {
        "brand_keywords": _join(filters.get("brand_keywords")),
        "countries": _join(filters.get("countries")),
        "categories": _join(filters.get("categories")),
        "company_types": _join(filters.get("company_types")),
    }
    values["query"] = " ".join(value for value in values.values() if value).strip()
    query_template = (template or "{{brand_keywords}} {{categories}} {{countries}}").strip()
    return PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), ""), query_template).strip()


def score_brand_relevance(company: dict, filters: dict) -> tuple[int, list[str]]:
    """Score independent industry evidence rather than search constraints.

    Zero means "not evaluated" until enrichment records an evidence source and
    confidence. Provider query/category/country matches only decide admission.
    """
    source = str(company.get("industry_source") or "").strip()
    confidence = company.get("industry_confidence")
    if not source or confidence is None:
        return 0, []
    category_matched, category_term, category_is_direct = _match_target_category(company, filters)
    if not category_matched:
        return 0, ["独立行业证据与目标品类不匹配"]
    score = max(0, min(100, int(confidence or 0)))
    evidence_label = "官网行业证据" if source.startswith("official_website") else "公司行业丰富证据"
    match_label = "直接匹配" if category_is_direct else "语义匹配"
    return score, [f"{evidence_label}{match_label}“{category_term}”（置信度 {score}%）"]


def _category_match_points(company: dict, filters: dict, category_is_direct: bool) -> int:
    """Weight actual company evidence above Provider query-level matching."""
    categories = _category_values(filters)
    match_mode = str(filters.get("category_match_mode") or "any").casefold()
    all_evidence_text = " ".join(
        str(company.get(field) or "")
        for field in (
            "brand_name",
            "name",
            "legal_name",
            "industry",
            "industry_details",
            "category",
            "source_title",
            "source_excerpt",
        )
    ).casefold()
    asks_for_belts = any(
        any(
            term in {"belt", "belts", "waist belt", "leather belt", "皮带", "腰带"}
            for term in _category_search_terms(category)
        )
        for category in categories
    )
    belt_non_fashion = any(
        term in all_evidence_text
        for term in (
            "conveyor",
            "power transmission",
            "timing belt",
            "drive belt",
            "industrial supplies",
            "industrial machinery",
            "automotive safety",
            "seat belt",
            "safety belt",
            "treadmill",
            "martial art",
            "black belt school",
            "karate",
            "wire belt",
        )
    )
    belt_fashion = any(
        term in all_evidence_text
        for term in (
            "leather goods",
            "leather belt",
            "fashion",
            "apparel",
            "accessories",
            "accessory",
            "clothing",
            "waist belt",
            "皮具",
            "皮带",
            "服饰",
            "配饰",
        )
    )
    if asks_for_belts and belt_non_fashion and not belt_fashion:
        return 0
    if category_is_direct:
        # Enriched classifications are probabilistic evidence. Preserve the
        # classifier confidence instead of awarding every completed result the
        # same 70 points.
        if company.get("industry_source") and company.get("industry_confidence") is not None:
            confidence = max(0, min(100, int(company.get("industry_confidence") or 0)))
            return round(45 + confidence * 0.25)
        return 70
    evidence_fields = (
        ("industry", 70),
        ("industry_details", 65),
        ("source_excerpt", 55),
        ("source_title", 50),
    )
    for field, points in evidence_fields:
        value = company.get(field)
        if isinstance(value, dict):
            text = " ".join(str(item) for item in value.values())
        else:
            text = str(value or "")
        text = _remove_ambiguous_category_phrases(text.casefold())
        matches = [
            any(_contains_term(text, term) for term in _category_search_terms(category))
            for category in categories
        ]
        if matches and (all(matches) if match_mode == "all" else any(matches)):
            return points
    identity = " ".join(
        str(company.get(field) or "") for field in ("brand_name", "name", "legal_name", "domain")
    )
    identity = _remove_ambiguous_category_phrases(identity.casefold())
    identity_matches = [
        any(_contains_term(identity, term) for term in _category_search_terms(category))
        for category in categories
    ]
    if identity_matches and (
        all(identity_matches) if match_mode == "all" else any(identity_matches)
    ):
        # A word in a company name/domain is only a discovery clue.  Treating it
        # as strong category proof made "wire belt", "safety belt" and martial
        # arts companies score exactly like fashion-accessory brands.
        industry_text = " ".join(
            str(company.get(field) or "")
            for field in (
                "industry",
                "industry_details",
                "category",
                "source_title",
                "source_excerpt",
            )
        ).casefold()
        if any(term in industry_text for term in ("leather goods", "leather product", "皮具")):
            return 45
        if any(
            term in industry_text
            for term in (
                "apparel",
                "fashion",
                "accessories",
                "accessory",
                "clothing",
                "department store",
                "fashion retail",
                "服装",
                "服饰",
                "配饰",
            )
        ):
            return 40
        if company.get("semantic_category_provider_confirmed"):
            return 35
        return 15
    # A Provider keyword filter applies to the query, not necessarily each company.
    # It is still useful as medium-strength discovery evidence when the adapter
    # confirms the filter was actually sent.  Candidates remain pending until
    # website/industry enrichment supplies company-level proof.
    if company.get("semantic_category_provider_confirmed"):
        return 35
    return 15


def _remove_ambiguous_category_phrases(text: str) -> str:
    for phrase in (
        "purse seine",
        "filter bag",
        "filter bags",
        "bag filter",
        "bag filters",
        "air bag",
        "air bags",
        "airbag",
        "airbags",
        "sand bag",
        "sand bags",
        "body bag",
        "body bags",
        "tea bag",
        "tea bags",
    ):
        text = text.replace(phrase, " ")
    return text


def filter_discovery_companies(companies: Iterable[dict], filters: dict) -> list[dict]:
    """Keep usable Provider results; relevance is advisory in the review list."""
    configured_threshold = filters.get("min_relevance")
    threshold = 45 if configured_threshold is None else int(configured_threshold)
    require_website = bool(filters.get("require_website", True))
    require_category_match = bool(_category_values(filters))
    require_country_match = bool(_country_values(filters.get("countries")))
    company_types = {
        str(value).strip().casefold()
        for value in filters.get("company_types", [])
        if str(value).strip()
    }
    commercial_advisory_relevance = bool(company_types - {"brand", "品牌"})
    accepted: list[dict] = []
    for company in companies:
        category_matched, _, category_is_direct = _match_target_category(company, filters)
        country_matched, country, country_evidence = _match_target_country(company, filters)
        score, reasons = score_brand_relevance(company, filters)
        website = company.get("website") or company.get("url") or company.get("domain")
        excluded_industry = _matches_excluded_industry(company)
        score_is_evaluated = (
            bool(company.get("industry_source")) and company.get("industry_confidence") is not None
        )
        category_evidence_is_insufficient = (
            category_matched and _category_match_points(company, filters, category_is_direct) < 35
        )
        provider_scoped_relevance = bool(
            company.get("semantic_match") and company.get("semantic_category_match")
        )
        advisory_relevance = commercial_advisory_relevance or provider_scoped_relevance
        strict_relevance_failure = not advisory_relevance and (
            (score_is_evaluated and score < threshold)
            or (require_category_match and not category_matched)
            or category_evidence_is_insufficient
        )
        if (
            strict_relevance_failure
            or (require_country_match and not country_matched)
            or (require_website and not website)
            or excluded_industry
        ):
            continue
        accepted.append(
            {
                **company,
                "country": country or company.get("country"),
                "country_evidence": country_evidence,
                "relevance_score": score,
                "relevance_reasons": reasons,
            }
        )
    return sorted(accepted, key=lambda item: int(item["relevance_score"]), reverse=True)


def brand_country(company: dict) -> tuple[str, str]:
    """Return an evidenced brand-origin country, never a sales/operating market."""
    for field, label in BRAND_COUNTRY_FIELDS:
        value = str(company.get(field) or "").strip()
        if value:
            return value, label
    scope = str(company.get("country_scope") or "").strip().casefold()
    country = str(company.get("country") or "").strip()
    if country and scope in BRAND_COUNTRY_SCOPES:
        labels = {"headquarters": "总部国家", "registered": "注册国家", "origin": "品牌起源国家"}
        return country, labels[scope]
    return "", ""


def _match_target_country(company: dict, filters: dict) -> tuple[bool, str, str]:
    targets = {_normalize_country(value) for value in _country_values(filters.get("countries"))}
    targets.discard("")
    country, evidence = brand_country(company)
    if not targets:
        return True, country, evidence
    return _normalize_country(country) in targets, country, evidence


def filter_exact_brand_companies(companies: Iterable[dict], filters: dict) -> list[dict]:
    """Keep only results whose brand name and official domain both match."""
    target_names = {
        _normalize_brand_name(value) for value in _joinable_values(filters.get("brand_keywords"))
    }
    target_domains = {
        _normalize_domain(value) for value in _joinable_values(filters.get("official_domains"))
    }
    target_names.discard("")
    target_domains.discard("")
    if not target_names or not target_domains:
        return []
    accepted: list[dict] = []
    for company in companies:
        candidate_names = (
            company.get("brand_name"),
            company.get("name"),
            company.get("legal_name"),
        )
        candidate_domain = _normalize_domain(
            company.get("domain") or company.get("website") or company.get("url")
        )
        name_matches = any(
            _normalize_brand_name(value) in target_names for value in candidate_names if value
        )
        domain_matches = any(_domains_match(candidate_domain, target) for target in target_domains)
        if name_matches and domain_matches:
            accepted.append(company)
    return accepted


def _join(value: object) -> str:
    return " ".join(_joinable_values(value))


def _joinable_values(value: object) -> list[str]:
    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _terms(value: object) -> list[str]:
    return (
        [match.group(0).lower() for item in value for match in TOKEN_RE.finditer(str(item).lower())]
        if isinstance(value, list)
        else []
    )


def _category_values(filters: dict) -> list[str]:
    return (
        [str(item).strip().lower() for item in filters.get("categories", []) if str(item).strip()]
        if isinstance(filters.get("categories"), list)
        else []
    )


def _country_values(value: object) -> list[str]:
    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _normalize_country(value: object) -> str:
    normalized = re.sub(r"[^\w]+", "", str(value or "").strip().casefold())
    return COUNTRY_ALIASES.get(normalized, normalized)


def _match_target_category(company: dict, filters: dict) -> tuple[bool, str, bool]:
    categories = _category_values(filters)
    if not categories:
        return False, "", False
    direct_text = _remove_ambiguous_category_phrases(
        " ".join(
            str(company.get(field) or "") for field in ("category", "industry", "industry_details")
        ).lower()
    )
    context_text = _remove_ambiguous_category_phrases(
        " ".join(
            str(company.get(field) or "")
            for field in (
                "category",
                "industry",
                "industry_details",
                "source_title",
                "source_excerpt",
            )
        ).lower()
    )
    matches: list[tuple[bool, str, bool]] = []
    for category in categories:
        category_match: tuple[bool, str, bool] = (False, "", False)
        for term in _category_search_terms(category):
            if _contains_term(direct_text, term):
                category_match = (True, term, True)
                break
            if _contains_term(context_text, term):
                category_match = (True, term, False)
                break
        matches.append(category_match)
    match_mode = str(filters.get("category_match_mode") or "any").casefold()
    if matches and (
        all(item[0] for item in matches)
        if match_mode == "all"
        else any(item[0] for item in matches)
    ):
        matched_terms = " + ".join(item[1] for item in matches if item[0])
        return True, matched_terms, all(item[2] for item in matches if item[0])
    if _candidate_has_lexical_target(company, categories, match_mode):
        return True, categories[0], False
    if company.get("semantic_category_match"):
        # A small semantic result set is not company-level evidence. Only trust
        # an explicit Provider filter or lexical evidence from the candidate.
        explicit_industry = " ".join(
            str(company.get(field) or "") for field in ("category", "industry", "industry_details")
        ).casefold()
        broad_consumer_support = any(
            term in explicit_industry
            for term in (
                "fashion",
                "apparel",
                "accessories",
                "accessory",
                "leather goods",
                "clothing",
                "retail",
            )
        )
        provider_filter_is_usable = (
            bool(company.get("semantic_category_provider_confirmed"))
            and company.get("company_category_evidence") == "hunter_applied_industry_filter"
            and (not explicit_industry.strip() or broad_consumer_support)
        )
        if provider_filter_is_usable or _candidate_has_lexical_target(
            company, categories, match_mode
        ):
            return True, categories[0], False
    return False, "", False


def _candidate_has_lexical_target(
    company: dict, categories: list[str], match_mode: str = "any"
) -> bool:
    """Require company-level category evidence when a semantic result set is broad."""
    text = " ".join(
        str(company.get(field) or "")
        for field in ("brand_name", "name", "legal_name", "website", "domain")
    ).casefold()
    compact_text = re.sub(r"[^\w]+", "", text)
    category_matches: list[bool] = []
    for category in categories:
        matched = False
        for term in _category_search_terms(category):
            normalized_term = re.sub(r"[^\w]+", "", term.casefold())
            if len(normalized_term) >= 3 and normalized_term in compact_text:
                matched = True
                break
        category_matches.append(matched)
    return (
        (all(category_matches) if match_mode == "all" else any(category_matches))
        if category_matches
        else False
    )


def _matches_excluded_industry(company: dict) -> bool:
    text = " ".join(
        str(company.get(field) or "")
        for field in (
            "brand_name",
            "name",
            "legal_name",
            "category",
            "source_title",
            "source_excerpt",
        )
    ).casefold()
    return any(term in text for term in EXCLUDED_INDUSTRY_TERMS)


def _category_search_terms(category: str) -> list[str]:
    terms = [category]
    for token in _terms([category]):
        terms.extend(CATEGORY_ALIASES.get(token, ()))
    return list(dict.fromkeys(term for term in terms if term))


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if term.isascii() and any(character.isalnum() for character in term):
        return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text) is not None
    return term in text


def _normalize_brand_name(value: object) -> str:
    normalized = re.sub(r"[^\w]+", " ", str(value).lower()).strip()
    tokens = normalized.split()
    while tokens and tokens[-1] in LEGAL_NAME_SUFFIXES:
        tokens.pop()
    return (
        " ".join(tokens)
        .removesuffix("有限责任公司")
        .removesuffix("股份有限公司")
        .removesuffix("有限公司")
        .strip()
    )


def _normalize_domain(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"//{text}")
    hostname = (parsed.hostname or "").strip(".")
    return hostname.removeprefix("www.")


def _domains_match(candidate: str, target: str) -> bool:
    if not candidate or not target:
        return False
    return (
        candidate == target or candidate.endswith(f".{target}") or target.endswith(f".{candidate}")
    )
