import json
import re
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.modules.models import ProviderConfig
from app.providers.base import ProviderResult
from app.providers.errors import format_http_error


CONFIGURABLE_CATALOG_ADAPTERS = {
    "snov",
    "prospeo",
    "pdl",
    "dropcontact",
    "lusha",
    "neverbounce",
    "emailable",
    "wappalyzer",
    "builtwith",
    "crunchbase",
}

CATALOG_SUPPORTED_TYPES = {
    "snov": {
        "company_search",
        "contact_search",
        "email_finder",
        "brand_email_search",
        "email_verifier",
    },
    "prospeo": {"company_search", "contact_search", "email_finder"},
    "pdl": {"company_search", "contact_search"},
    "dropcontact": {"email_finder", "brand_email_search", "email_verifier"},
    "lusha": {"company_search", "contact_search"},
    "neverbounce": {"email_verifier"},
    "emailable": {"email_verifier"},
    "wappalyzer": {"company_search"},
    "builtwith": {"company_search"},
    "crunchbase": {"company_search"},
}

PROVIDER_CATEGORY_TERMS = {
    "箱包": ("luggage", "bags", "handbags", "travel bags"),
    "包": ("bags", "handbags", "luggage"),
    "女包": ("women's handbags", "handbags", "purses"),
    "手袋": ("handbags", "purses", "tote bags"),
    "男包": ("men's bags", "briefcases", "messenger bags", "leather goods"),
    "背包": ("backpacks", "bags", "luggage", "travel goods"),
    "旅行箱": ("luggage", "suitcases", "travel goods"),
    "行李箱": ("luggage", "suitcases", "travel goods"),
    "钱包": ("wallets", "purses", "small leather goods", "fashion accessories"),
    "皮带": ("belts", "leather belts", "fashion accessories", "leather goods"),
    "腰带": ("belts", "waist belts", "fashion accessories"),
    "皮具": ("leather goods", "bags", "wallets", "belts"),
    "服装": ("apparel", "clothing", "fashion"),
    "服饰": ("apparel", "clothing", "fashion accessories"),
    "宠物用品": ("pet supplies", "pet products"),
    "化妆品": ("cosmetics", "beauty products", "makeup"),
    "护肤": ("skincare", "beauty products", "cosmetics"),
    "家具": ("furniture", "home furnishings"),
    "家居": ("home furnishings", "home goods", "furniture"),
}

PROVIDER_ENGLISH_CATEGORY_TERMS = {
    "luggage": ("luggage", "suitcases", "travel bags"),
    "handbag": ("handbags", "purses", "tote bags"),
    "backpack": ("backpacks", "bags"),
    "wallet": ("wallets", "small leather goods"),
    "belt": ("belts", "leather belts"),
    "bag": ("bags", "handbags", "travel bags"),
}

PROVIDER_COUNTRY_NAMES = {
    "us": "United States",
    "usa": "United States",
    "美国": "United States",
    "gb": "United Kingdom",
    "uk": "United Kingdom",
    "英国": "United Kingdom",
    "cn": "China",
    "中国": "China",
    "中国大陆": "China",
    "hk": "Hong Kong",
    "香港": "Hong Kong",
    "tw": "Taiwan",
    "台湾": "Taiwan",
    "it": "Italy",
    "意大利": "Italy",
    "fr": "France",
    "法国": "France",
    "de": "Germany",
    "德国": "Germany",
    "es": "Spain",
    "西班牙": "Spain",
    "pt": "Portugal",
    "葡萄牙": "Portugal",
    "nl": "Netherlands",
    "荷兰": "Netherlands",
    "ca": "Canada",
    "加拿大": "Canada",
    "au": "Australia",
    "澳大利亚": "Australia",
    "jp": "Japan",
    "日本": "Japan",
    "kr": "South Korea",
    "韩国": "South Korea",
    "sg": "Singapore",
    "新加坡": "Singapore",
    "in": "India",
    "印度": "India",
    "br": "Brazil",
    "巴西": "Brazil",
    "mx": "Mexico",
    "墨西哥": "Mexico",
    "ae": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "阿联酋": "United Arab Emirates",
    "sa": "Saudi Arabia",
    "沙特": "Saudi Arabia",
    "沙特阿拉伯": "Saudi Arabia",
    "tr": "Turkey",
    "土耳其": "Turkey",
    "za": "South Africa",
    "南非": "South Africa",
    "ar": "Argentina",
    "阿根廷": "Argentina",
}

PROVIDER_COUNTRY_CODES = {
    "united states": "US", "united kingdom": "GB", "china": "CN",
    "hong kong": "HK", "taiwan": "TW", "italy": "IT", "france": "FR",
    "germany": "DE", "spain": "ES", "portugal": "PT", "netherlands": "NL",
    "canada": "CA", "australia": "AU", "japan": "JP", "south korea": "KR",
    "singapore": "SG", "india": "IN", "brazil": "BR", "mexico": "MX",
    "united arab emirates": "AE", "saudi arabia": "SA", "turkey": "TR",
    "south africa": "ZA", "argentina": "AR",
}

PROVIDER_BUSINESS_ROLES = {
    "distributor": "distributor",
    "分销商": "distributor",
    "wholesaler": "wholesaler",
    "批发商": "wholesaler",
    "retailer": "retailer",
    "零售商": "retailer",
    "importer": "importer",
    "进口商": "importer",
    "buyer": "buyer",
    "采购商": "buyer",
    "brand": "brand",
    "品牌": "brand",
}

def execute_vendor_provider(
    provider: ProviderConfig, payload: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    adapter = str(config.get("adapter") or "").lower()
    if adapter == "apollo":
        return _execute_apollo(provider, payload, config)
    if adapter == "hunter":
        return _execute_hunter(provider, payload, config)
    if adapter == "zerobounce":
        return _execute_zerobounce(provider, payload, config)
    if adapter == "aftership_local":
        return _execute_aftership_local(provider, payload, config)
    if adapter in CONFIGURABLE_CATALOG_ADAPTERS:
        return _execute_configured_catalog_provider(provider, payload, config)
    return ProviderResult(
        False,
        provider.provider,
        error_code="unsupported_adapter",
        error_message=f"Unsupported adapter: {adapter}",
    )


def test_catalog_provider_connection(
    provider: ProviderConfig, config: dict[str, Any]
) -> ProviderResult | None:
    """Use an optional, configuration-defined free endpoint for connection tests."""
    adapter = str(config.get("adapter") or "").lower()
    if adapter not in CONFIGURABLE_CATALOG_ADAPTERS:
        return None
    endpoint_url = str(config.get("connection_test_endpoint_url") or "").strip()
    if not endpoint_url:
        return None
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    headers = {
        str(key): str(value) for key, value in (config.get("connection_test_headers") or {}).items()
    }
    headers.setdefault("Accept", "application/json")
    api_key_header = str(config.get("api_key_header") or "").strip()
    api_key_query_param = str(config.get("api_key_query_param") or "").strip()
    if api_key_query_param:
        endpoint_url = _with_query(endpoint_url, {api_key_query_param: api_key})
    elif api_key_header:
        prefix = str(config.get("api_key_prefix") or "").strip()
        headers[api_key_header] = f"{prefix} {api_key}".strip() if prefix else api_key
    else:
        return ProviderResult(
            False,
            provider.provider,
            error_code="missing_api_key_auth",
            error_message="Catalog Provider API key authentication must be configured in System Settings",
        )
    method = str(config.get("connection_test_method") or "POST").strip().upper()
    if method not in {"GET", "POST"}:
        return ProviderResult(
            False,
            provider.provider,
            error_code="invalid_connection_test_method",
            error_message="Connection test method must be GET or POST",
        )
    if method == "POST":
        headers.setdefault("Content-Type", "application/json")
    body = (
        config.get("connection_test_body")
        if isinstance(config.get("connection_test_body"), dict)
        else {}
    )
    raw = _request_json(provider, endpoint_url, method, headers, body if method == "POST" else None)
    if isinstance(raw, ProviderResult):
        return raw
    return ProviderResult(True, provider.provider, data={}, raw=raw)


def check_vendor_provider_quota(
    provider: ProviderConfig, config: dict[str, Any]
) -> ProviderResult | None:
    """Query the configured vendor quota endpoint before consuming a credit."""
    adapter = str(config.get("adapter") or "").strip().lower()
    if adapter == "aftership_local":
        return _check_aftership_local_ready(provider, config)
    if adapter not in {"apollo", "hunter", "zerobounce", *CONFIGURABLE_CATALOG_ADAPTERS}:
        return None

    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    quota_endpoint_url = _quota_endpoint_url_result(provider, config)
    if isinstance(quota_endpoint_url, ProviderResult):
        return quota_endpoint_url

    headers = {str(key): str(value) for key, value in (config.get("quota_headers") or {}).items()}
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "BuyerReach/1.0")
    api_key_query_param = str(config.get("quota_api_key_query_param") or "").strip()
    if api_key_query_param:
        quota_endpoint_url = _with_query(quota_endpoint_url, {api_key_query_param: api_key})
    else:
        api_key_header = str(config.get("quota_api_key_header") or "").strip()
        if adapter in CONFIGURABLE_CATALOG_ADAPTERS and not api_key_header:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_quota_auth",
                error_message="Catalog Provider quota authentication must be configured in System Settings",
            )
        if not api_key_header:
            api_key_header = "X-API-KEY" if adapter == "hunter" else "x-api-key"
        api_key_prefix = str(config.get("quota_api_key_prefix") or "").strip()
        headers[api_key_header] = (
            f"{api_key_prefix} {api_key}".strip() if api_key_prefix else api_key
        )

    method = str(config.get("quota_method") or "GET").strip().upper()
    if method == "POST":
        headers.setdefault("Content-Type", "application/json")
        body = (
            config.get("quota_request_body")
            if isinstance(config.get("quota_request_body"), dict)
            else {}
        )
    else:
        body = None
    raw = _request_json(provider, quota_endpoint_url, method, headers, body)
    if isinstance(raw, ProviderResult):
        return raw

    remaining_path = str(config.get("quota_remaining_path") or "")
    remaining = _quota_response_value(raw, remaining_path, config)
    if remaining in (None, ""):
        total = _quota_response_value(raw, str(config.get("quota_available_path") or ""), config)
        used = _quota_response_value(raw, str(config.get("quota_used_path") or ""), config)
        try:
            remaining = float(total) - float(used)
        except (TypeError, ValueError):
            pass
    try:
        remaining_value = float(remaining)
    except (TypeError, ValueError):
        return ProviderResult(
            False,
            provider.provider,
            raw=raw,
            error_code="quota_check_invalid_response",
            error_message="Quota endpoint did not return a numeric remaining quota at the configured path",
        )

    reset_at = _quota_response_value(raw, str(config.get("quota_reset_at_path") or ""), config)
    if remaining_value > 0:
        return ProviderResult(
            True,
            provider.provider,
            data={"remaining": remaining_value, "reset_at": reset_at},
            raw=raw,
        )
    reset_message = f"; provider reports reset at {reset_at}" if reset_at not in (None, "") else ""
    return ProviderResult(
        False,
        provider.provider,
        data={"remaining": remaining_value, "reset_at": reset_at},
        raw=raw,
        error_code="quota_exhausted",
        error_message=f"Provider reports no remaining quota{reset_message}",
    )


def _check_aftership_local_ready(
    provider: ProviderConfig, config: dict[str, Any]
) -> ProviderResult:
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = str(config.get("quota_endpoint_url") or "").strip()
    if not endpoint_url:
        return ProviderResult(
            False,
            provider.provider,
            error_code="missing_ready_endpoint",
            error_message="本地邮箱验证服务就绪地址缺失",
        )
    raw = _request_json(
        provider,
        endpoint_url,
        "GET",
        {"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        None,
    )
    if isinstance(raw, ProviderResult):
        message = raw.error_message or "本地邮箱验证服务不可用"
        if raw.error_code == "http_401":
            message = "本地邮箱验证服务 Token 无效"
        return ProviderResult(
            False, provider.provider, error_code=raw.error_code, error_message=message, raw=raw.raw
        )
    if str(raw.get("status") or "") != "ready":
        return ProviderResult(
            False,
            provider.provider,
            error_code="service_not_ready",
            error_message="本地邮箱验证服务尚未就绪",
            raw=raw,
        )
    return ProviderResult(True, provider.provider, data={"ready": True}, raw=raw)


def _execute_apollo(
    provider: ProviderConfig, payload: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = _endpoint_url_result(provider, config)
    if isinstance(endpoint_url, ProviderResult):
        return endpoint_url
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    if provider.type == "company_search":
        keywords = _strings(payload.get("brand_keywords"))
        source_categories = _strings(payload.get("categories"))
        categories = _provider_search_categories(source_categories)
        if payload.get("test"):
            keywords = _strings(config.get("test_query")) or keywords
        official_domains = [
            domain
            for value in _strings(payload.get("official_domains"))
            if (domain := _domain(value))
        ]
        if source_categories and not categories:
            return ProviderResult(
                False,
                provider.provider,
                error_code="unsupported_search_language",
                error_message="Apollo could not translate the target category into English search terms",
            )
        if not keywords and not categories and not official_domains:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_search_keyword",
                error_message="Apollo requires a brand name, official domain, or target category",
            )
        body: dict[str, Any] = {
            "page": max(int(payload.get("discovery_page") or 1), 1),
            "per_page": min(
                max(
                    int(
                        payload.get("discovery_limit")
                        or payload.get("brand_limit")
                        or config.get("limit")
                        or 25
                    ),
                    1,
                ),
                100,
            ),
        }
        if official_domains:
            body["q_organization_domains_list"] = official_domains
        if (
            str(payload.get("mode") or "") == "brand_discovery"
            and categories
            and not official_domains
        ):
            # Company discovery is scoped only by market and product industry.
            # Buyer/procurement is a person role and belongs to contact search.
            body["q_organization_keyword_tags"] = categories[:12]
        elif keywords:
            body["q_organization_name"] = keywords[0]
        countries = _provider_search_countries(_strings(payload.get("countries")))
        if countries and not official_domains:
            body["organization_locations"] = countries
        raw = _request_json(provider, endpoint_url, "POST", headers, body)
        if isinstance(raw, ProviderResult):
            return raw
        return ProviderResult(
            True,
            provider.provider,
            data={
                "companies": [
                    _apollo_company(item, payload)
                    for item in _first_list(raw, "organizations", "accounts", "data")
                ]
            },
            raw=raw,
        )
    if provider.type == "contact_search":
        if str(payload.get("operation") or "") == "bulk_enrich":
            enrichment_endpoint = _configured_endpoint_url_result(
                provider,
                config,
                "bulk_enrichment_endpoint_url",
                "Apollo bulk enrichment",
            )
            if isinstance(enrichment_endpoint, ProviderResult):
                return enrichment_endpoint
            contacts = [item for item in payload.get("contacts", []) if isinstance(item, dict)]
            if not contacts:
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="missing_contacts",
                    error_message="Apollo bulk enrichment requires contacts",
                )
            company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
            domain = _domain(str(company.get("domain") or company.get("website") or ""))
            details = []
            for item in contacts[
                : min(max(int(config.get("bulk_enrichment_batch_size") or 10), 1), 10)
            ]:
                person_id = str(
                    item.get("provider_person_id") or item.get("person_id") or ""
                ).strip()
                if person_id:
                    details.append({"id": person_id})
                    continue
                first_name = str(item.get("first_name") or "").strip()
                last_name = str(item.get("last_name") or "").strip()
                if first_name and last_name:
                    details.append(
                        {
                            "first_name": first_name,
                            "last_name": last_name,
                            **({"domain": domain} if domain else {}),
                        }
                    )
            if not details:
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="missing_contact_identity",
                    error_message="Apollo bulk enrichment requires a person ID or full name",
                )
            query = {
                key: value
                for key, value in {
                    "reveal_personal_emails": config.get("bulk_enrichment_reveal_personal_emails"),
                    "reveal_phone_number": config.get("bulk_enrichment_reveal_phone_number"),
                }.items()
                if isinstance(value, bool)
            }
            raw = _request_json(
                provider,
                _with_query(enrichment_endpoint, query),
                "POST",
                headers,
                {"details": details},
            )
            if isinstance(raw, ProviderResult):
                return raw
            return ProviderResult(
                True,
                provider.provider,
                data={
                    "contacts": [
                        _apollo_contact(item)
                        for item in _first_list(raw, "matches", "people", "contacts", "data")
                    ]
                },
                raw=raw,
            )
        company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
        domain = _domain(str(company.get("domain") or company.get("website") or ""))
        if not domain:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_company_domain",
                error_message="Apollo contact search requires a company domain",
            )
        body = {
            "page": 1,
            "per_page": min(max(int(payload.get("limit") or config.get("limit") or 5), 1), 100),
            "q_organization_domains_list": [domain],
        }
        titles = _strings(payload.get("titles"))
        if titles:
            body["person_titles"] = titles
        raw = _request_json(provider, endpoint_url, "POST", headers, body)
        if isinstance(raw, ProviderResult):
            return raw
        return ProviderResult(
            True,
            provider.provider,
            data={
                "contacts": [
                    _apollo_contact(item) for item in _first_list(raw, "people", "contacts", "data")
                ]
            },
            raw=raw,
        )
    return _unsupported_type(provider, "Apollo", {"company_search", "contact_search"})


def _execute_hunter(
    provider: ProviderConfig, payload: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = _endpoint_url_result(provider, config)
    if isinstance(endpoint_url, ProviderResult):
        return endpoint_url
    if provider.type == "company_search":
        if str(payload.get("operation") or "") == "company_enrichment":
            enrichment_endpoint = _configured_endpoint_url_result(
                provider,
                config,
                "company_enrichment_endpoint_url",
                "Hunter Company Enrichment",
            )
            if isinstance(enrichment_endpoint, ProviderResult):
                return enrichment_endpoint
            domain = _domain(str(payload.get("domain") or payload.get("website") or ""))
            if not domain:
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="missing_company_domain",
                    error_message="Hunter Company Enrichment requires a domain",
                )
            raw = _request_json(
                provider,
                _with_query(enrichment_endpoint, {"domain": domain}),
                "GET",
                {
                    "Accept": "application/json",
                    "User-Agent": "BuyerReach/1.0",
                    "X-API-KEY": api_key,
                },
                None,
            )
            if isinstance(raw, ProviderResult):
                return raw
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            return ProviderResult(
                True, provider.provider, data={"company": data}, raw=raw, cost=0.2
            )
        if (
            str(payload.get("operation") or "") == "domain_finder"
            or str(payload.get("mode") or "") == "exact_brand"
        ):
            domain_finder_endpoint = _configured_endpoint_url_result(
                provider,
                config,
                "domain_finder_endpoint_url",
                "Hunter Domain Finder",
            )
            if isinstance(domain_finder_endpoint, ProviderResult):
                return domain_finder_endpoint
            company_name = str(
                payload.get("company_name")
                or payload.get("brand_name")
                or payload.get("company")
                or next(iter(_strings(payload.get("brand_keywords"))), "")
            ).strip()
            if not company_name:
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="missing_company_name",
                    error_message="Hunter Domain Finder requires a company name",
                )
            raw = _request_json(
                provider,
                _with_query(domain_finder_endpoint, {"company": company_name}),
                "GET",
                {
                    "Accept": "application/json",
                    "User-Agent": "BuyerReach/1.0",
                    "X-API-KEY": api_key,
                },
                None,
            )
            if isinstance(raw, ProviderResult):
                return raw
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            domains = _first_list(data, "domains") if isinstance(data, dict) else []
            companies = [
                {
                    "brand_name": company_name,
                    "domain": domain,
                    "website": _website(domain),
                }
                for item in domains
                if (
                    domain := _domain(
                        str(item.get("domain") or item.get("value") or item.get("website") or "")
                    )
                )
            ]
            return ProviderResult(True, provider.provider, data={"companies": companies}, raw=raw)
        categories = _provider_search_categories(_strings(payload.get("categories")))
        countries = _provider_search_country_codes(_strings(payload.get("countries")))
        if _strings(payload.get("categories")) and not categories:
            return ProviderResult(
                False,
                provider.provider,
                error_code="unsupported_search_language",
                error_message="Hunter could not translate the target category into English search terms",
            )
        if not categories and not countries:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_discovery_query",
                error_message="Hunter Discover requires countries, categories, or industry keywords",
            )
        discovery_limit = min(max(int(payload.get("discovery_limit") or 100), 1), 100)
        discovery_offset = max(int(payload.get("discovery_offset") or 0), 0)
        discovery_filters: dict[str, Any] = {
            "limit": discovery_limit,
            "offset": discovery_offset,
        }
        if countries:
            discovery_filters["headquarters_location"] = {
                "include": [{"country": country} for country in countries]
            }
        if categories:
            discovery_filters["keywords"] = {"include": categories, "match": "any"}
        query_summary = _hunter_discover_query(payload)
        raw = _request_json(
            provider,
            endpoint_url,
            "POST",
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "BuyerReach/1.0",
                "X-API-KEY": api_key,
            },
            discovery_filters,
        )
        if isinstance(raw, ProviderResult):
            return raw
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        applied_filters = meta.get("filters") if isinstance(meta.get("filters"), dict) else {}
        raw_companies = _first_list(raw, "data")
        try:
            semantic_result_count = max(
                int(meta.get("results") or len(raw_companies)), len(raw_companies)
            )
        except (TypeError, ValueError):
            semantic_result_count = len(raw_companies)
        try:
            semantic_trust_limit = min(max(int(config.get("semantic_trust_limit") or 50), 1), 100)
        except (TypeError, ValueError):
            semantic_trust_limit = 50
        companies = [
            _hunter_discover_company(
                item,
                payload,
                query_summary,
                applied_filters,
                semantic_result_count,
                semantic_trust_limit,
            )
            for item in raw_companies
        ]
        return ProviderResult(True, provider.provider, data={"companies": companies}, raw=raw)
    if provider.type == "contact_search":
        company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
        domain = _domain(str(company.get("domain") or company.get("website") or ""))
        if not domain:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_company_domain",
                error_message="Hunter Domain Search requires a company domain",
            )
        limit = min(max(int(payload.get("limit") or config.get("limit") or 20), 1), 100)
        raw = _request_json(
            provider,
            _with_query(endpoint_url, {"domain": domain, "limit": limit}),
            "GET",
            {"Accept": "application/json", "User-Agent": "BuyerReach/1.0", "X-API-KEY": api_key},
            None,
        )
        if isinstance(raw, ProviderResult):
            return raw
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        contacts = []
        for item in _first_list(data, "emails"):
            email = _hunter_domain_email(item)
            if email is None:
                continue
            contacts.append(
                {
                    "provider_person_id": email["address"],
                    "first_name": email.get("first_name"),
                    "last_name": email.get("last_name") or "",
                    "title": email.get("title"),
                    "email": email["address"],
                    "emails": [email["address"]],
                }
            )
        return ProviderResult(True, provider.provider, data={"contacts": contacts}, raw=raw)
    if provider.type in {"email_finder", "brand_email_search"}:
        contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
        domain = _domain(str(payload.get("domain") or ""))
        first_name = str(contact.get("first_name") or "").strip()
        last_name = str(contact.get("last_name") or "").strip()
        if not domain:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_company_domain",
                error_message="Hunter email discovery requires a company domain",
            )

        headers = {
            "Accept": "application/json",
            "User-Agent": "BuyerReach/1.0",
            "X-API-KEY": api_key,
        }
        domain_search = provider.type == "brand_email_search"
        if domain_search:
            email_count_endpoint = str(config.get("email_count_endpoint_url") or "").strip()
            if email_count_endpoint:
                email_count_endpoint_result = _configured_endpoint_url_result(
                    provider,
                    config,
                    "email_count_endpoint_url",
                    "Hunter Email Count",
                )
                if isinstance(email_count_endpoint_result, ProviderResult):
                    return email_count_endpoint_result
                count_raw = _request_json(
                    provider,
                    _with_query(email_count_endpoint_result, {"domain": domain}),
                    "GET",
                    headers,
                    None,
                )
                if isinstance(count_raw, ProviderResult):
                    return count_raw
                count_data = (
                    count_raw.get("data") if isinstance(count_raw.get("data"), dict) else {}
                )
                try:
                    total = int(count_data.get("total"))
                except (TypeError, ValueError):
                    return ProviderResult(
                        False,
                        provider.provider,
                        raw=count_raw,
                        error_code="email_count_invalid_response",
                        error_message="Hunter Email Count did not return data.total",
                    )
                if total <= 0:
                    return ProviderResult(
                        True, provider.provider, data={"emails": []}, raw={"email_count": count_raw}
                    )
            limit = min(
                max(int(payload.get("limit") or config.get("domain_search_limit") or 10), 1), 100
            )
            raw = _request_json(
                provider,
                _with_query(endpoint_url, {"domain": domain, "limit": limit}),
                "GET",
                headers,
                None,
            )
            if isinstance(raw, ProviderResult):
                return raw
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            emails = [
                normalized
                for item in _first_list(data, "emails")
                if (normalized := _hunter_domain_email(item)) is not None
            ]
            return ProviderResult(True, provider.provider, data={"emails": emails}, raw=raw)

        if not first_name or not last_name:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_contact_name",
                error_message="Hunter Email Finder requires both first and last name",
            )
        query = {"first_name": first_name, "last_name": last_name, "domain": domain}
        raw = _request_json(provider, _with_query(endpoint_url, query), "GET", headers, None)
        if isinstance(raw, ProviderResult):
            return raw
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        address = data.get("email")
        emails = (
            [{"address": address, "type": "personal", "confidence": data.get("score") or 0}]
            if address
            else []
        )
        return ProviderResult(True, provider.provider, data={"emails": emails}, raw=raw)
    if provider.type == "email_verifier":
        address = _email(payload)
        if not address:
            return ProviderResult(
                False,
                provider.provider,
                error_code="missing_email",
                error_message="Hunter Email Verifier requires an email address",
            )
        raw = _request_json(
            provider,
            _with_query(endpoint_url, {"email": address}),
            "GET",
            {"Accept": "application/json", "User-Agent": "BuyerReach/1.0", "X-API-KEY": api_key},
            None,
        )
        if isinstance(raw, ProviderResult):
            return raw
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        status = str(data.get("status") or "unknown")
        return ProviderResult(
            True,
            provider.provider,
            data={
                "result": _hunter_status(status),
                "score": _status_score(status),
                "is_catch_all": status.lower() == "accept_all" or bool(data.get("accept_all")),
                "is_disposable": bool(data.get("disposable")),
                "domain_deliverable": bool(data.get("mx_records", True)),
                "smtp_check": bool(data.get("smtp_check", status.lower() == "valid")),
            },
            raw=raw,
        )
    return _unsupported_type(
        provider,
        "Hunter",
        {
            "company_search",
            "contact_search",
            "brand_email_search",
            "email_finder",
            "email_verifier",
        },
    )


def _execute_zerobounce(
    provider: ProviderConfig, payload: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    if provider.type != "email_verifier":
        return _unsupported_type(provider, "ZeroBounce", {"email_verifier"})
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = _endpoint_url_result(provider, config)
    if isinstance(endpoint_url, ProviderResult):
        return endpoint_url
    address = _email(payload)
    if not address:
        return ProviderResult(
            False,
            provider.provider,
            error_code="missing_email",
            error_message="ZeroBounce requires an email address",
        )
    raw = _request_json(
        provider,
        _with_query(endpoint_url, {"email": address, "api_key": api_key}),
        "GET",
        {"Accept": "application/json", "User-Agent": "BuyerReach/1.0"},
        None,
    )
    if isinstance(raw, ProviderResult):
        return raw
    status = str(raw.get("status") or "unknown") if isinstance(raw, dict) else "unknown"
    lowered = status.lower()
    return ProviderResult(
        True,
        provider.provider,
        data={
            "result": _zerobounce_status(status),
            "score": _status_score(status),
            "is_catch_all": lowered in {"catch-all", "catch_all"},
            "is_disposable": lowered == "disposable",
            "domain_deliverable": bool(raw.get("mx_found", lowered not in {"invalid", "unknown"})),
            "mailbox_exists": lowered == "valid",
        },
        raw=raw,
    )


def _execute_aftership_local(
    provider: ProviderConfig, payload: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    if provider.type != "email_verifier":
        return _unsupported_type(provider, "AfterShip Local", {"email_verifier"})
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = _endpoint_url_result(provider, config)
    if isinstance(endpoint_url, ProviderResult):
        return endpoint_url
    address = _email(payload)
    if not address:
        return ProviderResult(
            False,
            provider.provider,
            error_code="missing_email",
            error_message="本地邮箱验证需要邮箱地址",
        )
    raw = _request_json(
        provider,
        endpoint_url,
        "POST",
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        {"email": address, "smtp": True},
    )
    if isinstance(raw, ProviderResult):
        message = raw.error_message or "本地邮箱验证服务请求失败"
        if raw.error_code == "http_401":
            message = "本地邮箱验证服务 Token 无效"
        elif raw.error_code in {"request_timeout", "network_error"}:
            message = "本地邮箱验证服务不可达或 SMTP 探测超时，将使用备用验证平台"
        return ProviderResult(
            False, provider.provider, error_code=raw.error_code, error_message=message, raw=raw.raw
        )
    result = str(raw.get("result") or "unknown").strip().lower()
    allowed = {"valid", "risky", "invalid", "disposable", "unknown"}
    if result not in allowed:
        result = "unknown"
    return ProviderResult(
        True,
        provider.provider,
        data={
            "result": result,
            "score": int(raw.get("score") or 0),
            "is_catch_all": bool(raw.get("is_catch_all")),
            "is_disposable": bool(raw.get("is_disposable")),
            "domain_deliverable": bool(raw.get("domain_deliverable")),
            "mailbox_exists": bool(raw.get("mailbox_exists")),
            "smtp_check": bool(raw.get("smtp_check")),
            "reason": raw.get("reason"),
            "cached": bool(raw.get("cached")),
            "adapter_version": raw.get("adapter_version") or "v1",
        },
        raw=raw,
    )


def _execute_configured_catalog_provider(
    provider: ProviderConfig,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> ProviderResult:
    """Execute catalog vendors entirely from System Settings request mappings."""
    adapter = str(config.get("adapter") or "").strip().lower()
    supported = CATALOG_SUPPORTED_TYPES.get(adapter, set())
    if provider.type not in supported:
        return _unsupported_type(provider, adapter or "Catalog Provider", supported)
    api_key = _api_key_result(provider, config)
    if isinstance(api_key, ProviderResult):
        return api_key
    endpoint_url = _endpoint_url_result(provider, config)
    if isinstance(endpoint_url, ProviderResult):
        return endpoint_url

    values = _catalog_template_values(payload)
    headers = {str(key): str(value) for key, value in (config.get("request_headers") or {}).items()}
    headers.setdefault("Accept", "application/json")
    api_key_header = str(config.get("api_key_header") or "").strip()
    api_key_query_param = str(config.get("api_key_query_param") or "").strip()
    if not api_key_header and not api_key_query_param:
        return ProviderResult(
            False,
            provider.provider,
            error_code="missing_api_key_auth",
            error_message="Catalog Provider API key header or query parameter must be configured in System Settings",
        )
    api_key_prefix = str(config.get("api_key_prefix") or "").strip()
    if api_key_query_param:
        endpoint_url = _with_query(endpoint_url, {api_key_query_param: api_key})
    else:
        headers[api_key_header] = (
            f"{api_key_prefix} {api_key}".strip() if api_key_prefix else api_key
        )

    request_query = _render_config_template(config.get("request_query") or {}, values)
    if not isinstance(request_query, dict):
        return ProviderResult(
            False,
            provider.provider,
            error_code="invalid_request_query",
            error_message="Catalog Provider request query must be a JSON object",
        )
    request_body = _render_config_template(config.get("request_body") or {}, values)
    if not isinstance(request_body, dict):
        return ProviderResult(
            False,
            provider.provider,
            error_code="invalid_request_body",
            error_message="Catalog Provider request body must be a JSON object",
        )
    method = str(config.get("request_method") or "").strip().upper()
    if method not in {"GET", "POST"}:
        return ProviderResult(
            False,
            provider.provider,
            error_code="invalid_request_method",
            error_message="Catalog Provider request method must be GET or POST",
        )
    if method == "POST":
        headers.setdefault("Content-Type", "application/json")
    raw = _request_json(
        provider,
        _with_query(endpoint_url, request_query),
        method,
        headers,
        request_body if method == "POST" else None,
    )
    if isinstance(raw, ProviderResult):
        return raw

    poll_result_url = _at_path(raw, str(config.get("poll_result_url_path") or ""))
    if isinstance(poll_result_url, str) and poll_result_url.strip():
        polled = _request_json(provider, poll_result_url.strip(), "GET", headers, None)
        if isinstance(polled, ProviderResult):
            return polled
        raw = polled

    if provider.type == "email_verifier":
        return _catalog_verification_result(provider, raw, config)
    items_path = str(config.get("response_items_path") or "").strip()
    items = _at_path(raw, items_path)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return ProviderResult(
            False,
            provider.provider,
            raw=raw,
            error_code="invalid_response_items_path",
            error_message="Catalog Provider response items path did not resolve to an array",
        )
    field_map = config.get("response_field_map") or {}
    if not isinstance(field_map, dict):
        return ProviderResult(
            False,
            provider.provider,
            raw=raw,
            error_code="invalid_response_field_map",
            error_message="Catalog Provider response field map must be a JSON object",
        )
    mapped = [_map_catalog_item(item, field_map) for item in items if isinstance(item, dict)]
    if provider.type == "company_search":
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [_catalog_company(item) for item in mapped]},
            raw=raw,
        )
    if provider.type == "contact_search":
        return ProviderResult(
            True,
            provider.provider,
            data={"contacts": [_catalog_contact(item) for item in mapped]},
            raw=raw,
        )
    if provider.type in {"email_finder", "brand_email_search"}:
        emails = [_catalog_email(item) for item in mapped]
        return ProviderResult(
            True,
            provider.provider,
            data={"emails": [item for item in emails if item is not None]},
            raw=raw,
        )
    return _unsupported_type(
        provider,
        "Catalog Provider",
        {
            "company_search",
            "contact_search",
            "email_finder",
            "brand_email_search",
            "email_verifier",
        },
    )


def _catalog_template_values(payload: dict[str, Any]) -> dict[str, Any]:
    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    domain = _domain(
        str(payload.get("domain") or company.get("domain") or company.get("website") or "")
    )
    return {
        "email": _email(payload) or str(payload.get("email") or ""),
        "domain": domain,
        "company_name": str(
            company.get("brand_name")
            or company.get("name")
            or payload.get("company_name")
            or (_strings(payload.get("brand_keywords")) or [""])[0]
        ),
        "first_name": str(contact.get("first_name") or payload.get("first_name") or ""),
        "last_name": str(contact.get("last_name") or payload.get("last_name") or ""),
        "full_name": " ".join(
            part
            for part in (
                str(contact.get("first_name") or payload.get("first_name") or "").strip(),
                str(contact.get("last_name") or payload.get("last_name") or "").strip(),
            )
            if part
        ),
        "linkedin_url": str(contact.get("linkedin_url") or payload.get("linkedin_url") or ""),
        "titles": _strings(payload.get("titles")),
        "countries": _strings(payload.get("countries")),
        "categories": _strings(payload.get("categories")),
        "brand_keywords": _strings(payload.get("brand_keywords")),
        "official_domains": [
            domain
            for value in _strings(payload.get("official_domains"))
            if (domain := _registrable_domain(value))
        ],
        "limit": payload.get("limit") or payload.get("brand_limit") or 10,
    }


def _render_config_template(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {str(key): _render_config_template(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_config_template(item, values) for item in value]
    if not isinstance(value, str):
        return value
    if value.startswith("{{") and value.endswith("}}") and value[2:-2].strip() in values:
        return values[value[2:-2].strip()]
    rendered = value
    for key, item in values.items():
        replacement = ",".join(str(part) for part in item) if isinstance(item, list) else str(item)
        rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
    return rendered


def _map_catalog_item(item: dict[str, Any], field_map: dict[str, Any]) -> dict[str, Any]:
    return {
        str(target): _at_path(item, str(source))
        for target, source in field_map.items()
        if isinstance(source, str)
    }


def _catalog_company(item: dict[str, Any]) -> dict[str, Any]:
    website = item.get("website") or item.get("website_url") or item.get("domain")
    country = item.get("headquarters_country") or item.get("country")
    return {
        **item,
        "brand_name": item.get("brand_name") or item.get("name") or item.get("organization_name"),
        "legal_name": item.get("legal_name") or item.get("brand_name") or item.get("name"),
        "website": _website(website),
        "domain": _domain(str(item.get("domain") or website or "")),
        "country": country,
        "headquarters_country": country,
        "country_scope": item.get("country_scope") or "headquarters",
    }


def _catalog_contact(item: dict[str, Any]) -> dict[str, Any]:
    emails = item.get("emails") if isinstance(item.get("emails"), list) else [item.get("email")]
    normalized_emails = [str(value).strip().lower() for value in emails if str(value or "").strip()]
    return {
        **item,
        "first_name": item.get("first_name") or item.get("name"),
        "last_name": item.get("last_name") or "",
        "email": normalized_emails[0] if normalized_emails else None,
        "emails": list(dict.fromkeys(normalized_emails)),
    }


def _catalog_email(item: dict[str, Any]) -> dict[str, Any] | None:
    address = str(item.get("address") or item.get("email") or "").strip().lower()
    if not address:
        return None
    try:
        confidence = int(item.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    return {
        **item,
        "address": address,
        "type": item.get("type") or "personal",
        "confidence": confidence,
    }


def _catalog_verification_result(
    provider: ProviderConfig, raw: dict[str, Any], config: dict[str, Any]
) -> ProviderResult:
    status = _at_path(raw, str(config.get("result_path") or ""))
    if status in (None, ""):
        return ProviderResult(
            False,
            provider.provider,
            raw=raw,
            error_code="invalid_result_path",
            error_message="Catalog Provider result path did not return a verification status",
        )
    status_map = config.get("result_map") if isinstance(config.get("result_map"), dict) else {}
    normalized = str(status_map.get(str(status), status)).strip().lower()
    score = _at_path(raw, str(config.get("score_path") or ""))
    try:
        score_value = int(score or 0)
    except (TypeError, ValueError):
        score_value = 0
    return ProviderResult(
        True, provider.provider, data={"result": normalized, "score": score_value}, raw=raw
    )


def _request_json(
    provider: ProviderConfig,
    url: str,
    method: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> dict[str, Any] | ProviderResult:
    headers.setdefault("User-Agent", "BuyerReach/1.0")
    headers.setdefault("Accept", "application/json")
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with urlopen(request, timeout=30) as response:
                raw = json.loads(response.read().decode("utf-8"))
            if not isinstance(raw, dict):
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="invalid_response",
                    error_message="Provider returned a non-object JSON response",
                )
            return raw
        except HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            detail = exc.read().decode("utf-8", errors="replace")
            if attempt == 0 and _is_transient_gateway_403(exc.code, detail):
                continue
            return ProviderResult(
                False,
                provider.provider,
                raw={"retry_after": retry_after} if retry_after else {},
                error_code=f"http_{exc.code}",
                error_message=format_http_error(exc.code, detail, retry_after),
            )
        except (URLError, TimeoutError, ssl.SSLError, ConnectionResetError) as exc:
            if attempt < max_attempts - 1:
                time.sleep(0.5 * (2**attempt))
                continue
            return ProviderResult(
                False, provider.provider, error_code="request_failed", error_message=str(exc)
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return ProviderResult(
                False, provider.provider, error_code="request_failed", error_message=str(exc)
            )
    return ProviderResult(
        False,
        provider.provider,
        error_code="request_failed",
        error_message="Provider request failed after retries",
    )


def _is_transient_gateway_403(status_code: int, body: str) -> bool:
    normalized = body.lower()
    if status_code != 403 or "<html" not in normalized:
        return False
    return any(sig in normalized for sig in ("cloudfront", "cloudflare-nginx", "cloudflare"))


def _api_key_result(provider: ProviderConfig, config: dict[str, Any]) -> str | ProviderResult:
    api_key = str(config.get("api_key") or "").strip()
    if api_key:
        return api_key
    return ProviderResult(
        False,
        provider.provider,
        error_code="missing_api_key",
        error_message="Provider API Key is not configured",
    )


def _endpoint_url_result(provider: ProviderConfig, config: dict[str, Any]) -> str | ProviderResult:
    endpoint_url = str(config.get("endpoint_url") or "").strip()
    parsed = urlparse(endpoint_url)
    if endpoint_url and parsed.scheme in {"http", "https"} and parsed.netloc:
        return endpoint_url
    return ProviderResult(
        False,
        provider.provider,
        error_code="missing_endpoint_url" if not endpoint_url else "invalid_endpoint_url",
        error_message="Provider endpoint URL must be configured in System Settings",
    )


def _quota_endpoint_url_result(
    provider: ProviderConfig, config: dict[str, Any]
) -> str | ProviderResult:
    endpoint_url = str(config.get("quota_endpoint_url") or "").strip()
    parsed = urlparse(endpoint_url)
    if endpoint_url and parsed.scheme in {"http", "https"} and parsed.netloc:
        return endpoint_url
    return ProviderResult(
        False,
        provider.provider,
        error_code="missing_quota_endpoint_url"
        if not endpoint_url
        else "invalid_quota_endpoint_url",
        error_message="Provider quota endpoint URL must be configured for automatic failover",
    )


def _configured_endpoint_url_result(
    provider: ProviderConfig,
    config: dict[str, Any],
    key: str,
    capability: str,
) -> str | ProviderResult:
    endpoint_url = str(config.get(key) or "").strip()
    parsed = urlparse(endpoint_url)
    if endpoint_url and parsed.scheme in {"http", "https"} and parsed.netloc:
        return endpoint_url
    return ProviderResult(
        False,
        provider.provider,
        error_code=f"missing_{key}" if not endpoint_url else f"invalid_{key}",
        error_message=f"{capability} endpoint URL must be configured in System Settings",
    )


def _at_path(value: object, path: str) -> object:
    current = value
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _quota_response_value(raw: dict[str, Any], path: str, config: dict[str, Any]) -> object:
    if not path:
        return None
    if not path.startswith("$endpoint."):
        return _at_path(raw, path)
    endpoint_path = urlparse(str(config.get("endpoint_url") or "")).path.strip("/")
    for key, value in raw.items():
        if (
            endpoint_path
            and _quota_endpoint_key_matches(key, endpoint_path)
            and isinstance(value, dict)
        ):
            return _at_path(value, path.removeprefix("$endpoint."))
    return None


def _quota_endpoint_key_matches(key: object, endpoint_path: str) -> bool:
    key_text = str(key)
    if endpoint_path in key_text:
        return True
    try:
        parts = json.loads(key_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(parts, list):
        return False
    normalized = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    return endpoint_path == normalized or endpoint_path in normalized


def _unsupported_type(provider: ProviderConfig, vendor: str, supported: set[str]) -> ProviderResult:
    return ProviderResult(
        False,
        provider.provider,
        error_code="unsupported_provider_type",
        error_message=f"{vendor} supports: {', '.join(sorted(supported))}",
    )


def _first_list(raw: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            if items:
                return items
    return []


def _apollo_company(item: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    website = item.get("website_url") or item.get("website") or item.get("primary_domain")
    headquarters_country = item.get("organization_country") or item.get("country")
    categories = _provider_search_categories(_strings((payload or {}).get("categories")))
    company = {
        "brand_name": item.get("name") or item.get("organization_name"),
        "legal_name": item.get("legal_name") or item.get("name"),
        "website": _website(website),
        "domain": _domain(str(website or item.get("primary_domain") or "")),
        "country": headquarters_country,
        "headquarters_country": headquarters_country,
        "country_scope": "headquarters",
        "category": item.get("industry") or item.get("industry_name"),
    }
    if payload and str(payload.get("mode") or "") == "brand_discovery":
        company.update(
            {
                "semantic_match": True,
                "semantic_category_match": bool(categories),
                "source_title": "Apollo organization search match",
                "source_excerpt": (
                    "Apollo returned this company for the configured location and category filters."
                ),
            }
        )
    return company


def _apollo_contact(item: dict[str, Any]) -> dict[str, Any]:
    emails = [
        str(value).strip().lower()
        for value in (item.get("email"), item.get("work_email"), item.get("personal_email"))
        if str(value or "").strip()
    ]
    return {
        "provider_person_id": item.get("id") or item.get("person_id"),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name") or "",
        "last_name_obfuscated": item.get("last_name_obfuscated"),
        "title": item.get("title"),
        "linkedin_url": item.get("linkedin_url"),
        "email": emails[0] if emails else None,
        "emails": list(dict.fromkeys(emails)),
    }


def _hunter_domain_email(item: dict[str, Any]) -> dict[str, Any] | None:
    address = str(item.get("value") or item.get("email") or "").strip().lower()
    if not address:
        return None
    verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
    return {
        "address": address,
        "type": str(item.get("type") or "personal"),
        "confidence": int(item.get("confidence") or 0),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name") or "",
        "title": item.get("position"),
        "verification_status": verification.get("status"),
        "sources": item.get("sources") if isinstance(item.get("sources"), list) else [],
    }


def _hunter_discover_query(payload: dict[str, Any]) -> str:
    countries = ", ".join(_provider_search_countries(_strings(payload.get("countries"))))
    categories = _provider_search_categories(_strings(payload.get("categories")))
    keywords = ", ".join(_hunter_discover_keywords(payload))
    parts = ["Companies"]
    if countries:
        parts.append(f"headquartered in {countries}")
    targets = categories or ([keywords] if keywords else [])
    if targets:
        parts.append(f"in the {_natural_or(targets)} industry")
    return " ".join(parts).strip() if len(parts) > 1 else ""


def _hunter_discover_keywords(payload: dict[str, Any]) -> list[str]:
    """Keep concise market terms and drop task instructions from the Provider query."""
    countries = _strings(payload.get("countries"))
    instruction_markers = (
        "寻找",
        "查找",
        "获取",
        "联系人",
        "负责人",
        "采购",
        "邮箱",
        "邮件",
        "find ",
        "looking for",
        "contact",
        "email",
        "buyer",
        "procurement",
        "purchasing",
    )
    normalized: list[str] = []
    for value in _strings(payload.get("brand_keywords")):
        lowered = value.casefold()
        if any(marker in lowered for marker in instruction_markers):
            continue
        cleaned = value
        for country in countries:
            cleaned = cleaned.replace(country, "")
        cleaned = cleaned.strip(" ,，、;；:-")
        cleaned = cleaned.removesuffix("品牌").removesuffix("公司").strip()
        if cleaned and cleaned.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(cleaned)
    return normalized


def _hunter_discover_company(
    item: dict[str, Any],
    payload: dict[str, Any],
    query: str,
    applied_filters: dict[str, Any],
    semantic_result_count: int,
    semantic_trust_limit: int,
) -> dict[str, Any]:
    domain = _domain(str(item.get("domain") or ""))
    emails_count = item.get("emails_count") if isinstance(item.get("emails_count"), dict) else {}
    categories = _provider_search_categories(_strings(payload.get("categories")))
    name = item.get("organization") or item.get("name")
    headquarters_country = _hunter_headquarters_country(item, applied_filters)
    actual_category = _hunter_company_category(item)
    category_evidence = _hunter_category_evidence(applied_filters, categories)
    industry_filter = applied_filters.get("industry")
    provider_industries = (
        [str(value).strip() for value in industry_filter.get("include", []) if str(value).strip()]
        if isinstance(industry_filter, dict) and isinstance(industry_filter.get("include"), list)
        else []
    )
    return {
        "brand_name": name,
        "legal_name": name,
        "website": _website(domain),
        "domain": domain,
        "country": headquarters_country,
        "headquarters_country": headquarters_country,
        "country_scope": "headquarters" if headquarters_country else None,
        # Never copy the user's requested category into a company field. This is
        # actual Provider data only; absence means the industry is unverified.
        "category": actual_category,
        "emails_count": int(emails_count.get("total") or 0),
        "semantic_match": True,
        "semantic_category_match": bool(categories),
        "semantic_result_count": semantic_result_count,
        "semantic_category_selective": semantic_result_count <= semantic_trust_limit,
        "source_title": "Hunter Discover semantic company match",
        "source_excerpt": "Hunter Discover returned this company for the configured semantic filters.",
        "provider_query": query,
        "provider_filters": applied_filters,
        "raw_data": item,
        "semantic_category_provider_confirmed": category_evidence is not None,
        "company_category_evidence": category_evidence,
        "provider_industry_terms": provider_industries,
    }


def _hunter_company_category(item: dict[str, Any]) -> str | None:
    for key in ("industry", "industry_name", "category", "category_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("industries", "categories"):
        value = item.get(key)
        if isinstance(value, list):
            names = [
                str(entry.get("name") if isinstance(entry, dict) else entry).strip()
                for entry in value
                if str(entry.get("name") if isinstance(entry, dict) else entry).strip()
            ]
            if names:
                return ", ".join(dict.fromkeys(names))
    return None


def _hunter_headquarters_country(
    item: dict[str, Any], applied_filters: dict[str, Any]
) -> str | None:
    """Use a per-company value or a Provider-confirmed headquarters filter, not query input."""
    headquarters = item.get("headquarters") if isinstance(item.get("headquarters"), dict) else {}
    direct = (
        item.get("headquarters_country")
        or headquarters.get("country")
        or item.get("organization_country")
        or item.get("country")
    )
    if direct:
        return str(direct).strip() or None
    location_filter = applied_filters.get("headquarters_location")
    if not isinstance(location_filter, dict):
        return None
    included = location_filter.get("include")
    if not isinstance(included, list) or len(included) != 1 or not isinstance(included[0], dict):
        return None
    country = str(included[0].get("country") or "").strip()
    return country or None


def _hunter_category_filter_confirmed(
    applied_filters: dict[str, Any], categories: list[str]
) -> bool:
    return _hunter_category_evidence(applied_filters, categories) is not None


def _hunter_category_evidence(applied_filters: dict[str, Any], categories: list[str]) -> str | None:
    """Use Hunter's applied related-industry filter as company-level evidence."""
    industry = applied_filters.get("industry")
    if not isinstance(industry, dict):
        return None
    included = industry.get("include")
    if not isinstance(included, list) or not categories:
        return None
    text = " ".join(str(item) for item in included if str(item).strip()).casefold()
    if not text:
        return None
    industry_tokens = set(_hunter_filter_term(text).split())
    category_tokens = {
        token
        for category in categories
        for token in _hunter_filter_term(category).split()
        if len(token) >= 3
    }
    fashion_category_tokens = {
        "fashion",
        "apparel",
        "accessory",
        "accessories",
        "leather",
        "luggage",
        "bag",
        "bags",
        "handbag",
        "handbags",
        "wallet",
        "wallets",
        "belt",
        "belts",
    }
    related_industry_terms = {
        "fashion",
        "apparel",
        "accessories",
        "accessory",
        "leather goods",
        "leather product",
        "luggage",
        "bag",
        "bags",
        "handbag",
        "handbags",
        "wallet",
        "wallets",
        "belt",
        "belts",
        "travel goods",
        "retail",
    }
    direct_match = bool(category_tokens & industry_tokens)
    fashion_family_match = bool(category_tokens & fashion_category_tokens) and any(
        term in text for term in related_industry_terms
    )
    if direct_match or fashion_family_match:
        return "hunter_applied_industry_filter"
    return None


def _hunter_filter_term(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _with_query(url: str, query: dict[str, Any]) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    for key, value in query.items():
        if value in (None, ""):
            continue
        pairs.append((str(key), value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs, doseq=True), parts.fragment)
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    return []


def _provider_search_categories(categories: list[str]) -> list[str]:
    """Translate user-facing category labels into English Provider search terms."""
    terms: list[str] = []
    for category in categories:
        normalized = category.strip().casefold()
        mapped: tuple[str, ...] | None = None
        for label, values in PROVIDER_CATEGORY_TERMS.items():
            if label in normalized:
                mapped = values
                break
        if mapped is not None:
            if "时尚" in normalized and "fashion" not in terms:
                terms.append("fashion")
            terms.extend(mapped)
        elif normalized.isascii() and normalized:
            english_terms = [
                values
                for label, values in PROVIDER_ENGLISH_CATEGORY_TERMS.items()
                if re.search(rf"\b{re.escape(label)}s?\b", normalized)
            ]
            if english_terms:
                for values in english_terms:
                    terms.extend(values)
            else:
                terms.append(category.strip())
    return list(dict.fromkeys(term for term in terms if term))[:12]


def _provider_search_countries(countries: list[str]) -> list[str]:
    normalized: list[str] = []
    for country in countries:
        key = country.strip().casefold()
        normalized.append(PROVIDER_COUNTRY_NAMES.get(key, country.strip()))
    return list(dict.fromkeys(value for value in normalized if value))


def _provider_search_country_codes(countries: list[str]) -> list[str]:
    codes: list[str] = []
    for country in _provider_search_countries(countries):
        value = country.strip()
        code = value.upper() if len(value) == 2 and value.isascii() else PROVIDER_COUNTRY_CODES.get(
            value.casefold()
        )
        if code:
            codes.append(code)
    return list(dict.fromkeys(codes))


def _provider_business_roles(company_types: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            PROVIDER_BUSINESS_ROLES.get(value.strip().casefold(), value.strip())
            for value in company_types
            if value.strip()
        )
    )


def _natural_or(values: list[str]) -> str:
    unique = list(dict.fromkeys(value for value in values if value))
    if len(unique) < 2:
        return unique[0] if unique else ""
    return f"{', '.join(unique[:-1])} or {unique[-1]}"


def _email(payload: dict[str, Any]) -> str:
    return str(payload.get("email") or payload.get("address") or "").strip().lower()


def _website(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text if text.startswith(("http://", "https://")) else f"https://{text}"


def _domain(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    return (parsed.hostname or "").removeprefix("www.").lower()


def _registrable_domain(value: str) -> str:
    """Reduce a company website host to the domain accepted by company-search APIs."""
    hostname = _domain(value).strip(".")
    labels = [label for label in hostname.split(".") if label]
    if len(labels) <= 2:
        return hostname
    if len(labels[-1]) == 2 and labels[-2] in {"ac", "co", "com", "edu", "gov", "net", "org"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _hunter_status(status: str) -> str:
    lowered = status.lower()
    if lowered == "valid":
        return "valid"
    if lowered in {"accept_all", "webmail", "unknown"}:
        return "risky" if lowered != "unknown" else "unknown"
    return "invalid"


def _zerobounce_status(status: str) -> str:
    lowered = status.lower()
    if lowered == "valid":
        return "valid"
    if lowered in {"catch-all", "catch_all", "unknown"}:
        return "risky" if lowered != "unknown" else "unknown"
    if lowered in {"do_not_mail", "spamtrap", "abuse"}:
        return "do_not_contact"
    return "invalid"


def _status_score(status: str) -> int:
    lowered = status.lower()
    if lowered == "valid":
        return 100
    if lowered in {"accept_all", "webmail", "catch-all", "catch_all"}:
        return 50
    return 0
