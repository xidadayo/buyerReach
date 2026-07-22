from email_validator import EmailNotValidError, validate_email
from urllib.parse import urlparse

from app.modules.email_inference import infer_emails
from app.modules.models import ProviderConfig
from app.providers.base import ProviderResult
from app.providers.local import slugify
from app.shared.enums import EmailStatus


def execute_builtin_provider(provider: ProviderConfig, payload: dict, config: dict | None = None) -> ProviderResult:
    if provider.type == "company_search":
        return _search_companies(provider, payload, config)
    if provider.type == "contact_search":
        return _search_contacts(provider, payload, config)
    if provider.type == "email_finder":
        return _find_emails(provider, payload, config)
    if provider.type == "brand_email_search":
        return _find_emails(provider, payload, config)
    if provider.type == "email_verifier":
        return _verify_email(provider, payload)
    return ProviderResult(
        ok=False,
        provider=provider.provider,
        error_code="unsupported_builtin_provider",
        error_message=f"Built-in adapter does not support provider type: {provider.type}",
    )


def _search_companies(provider: ProviderConfig, payload: dict, config: dict | None = None) -> ProviderResult:
    config = config or provider.config or {}
    configured = config.get("companies") or config.get("seed_companies")
    if isinstance(configured, list) and configured:
        companies = [_normalize_company(item, config) for item in configured if isinstance(item, dict)]
        return ProviderResult(ok=True, provider=provider.provider, data={"companies": companies}, raw={"adapter": "builtin"})

    keywords = _string_list(payload.get("brand_keywords")) or _string_list(config.get("brand_keywords"))
    if not keywords and payload.get("test"):
        keywords = ["BuyerReach Demo"]
    countries = _string_list(payload.get("countries")) or _string_list(config.get("countries"))
    categories = _string_list(payload.get("categories")) or _string_list(config.get("categories"))
    limit = int(payload.get("brand_limit") or config.get("brand_limit") or 100)

    companies = []
    for index, keyword in enumerate(keywords[:limit]):
        company = _company_from_keyword(
            keyword,
            config,
            countries[index % len(countries)] if countries else config.get("default_country"),
            categories[index % len(categories)] if categories else config.get("default_category"),
        )
        companies.append(company)
    return ProviderResult(ok=True, provider=provider.provider, data={"companies": companies}, raw={"adapter": "builtin"})


def _search_contacts(provider: ProviderConfig, payload: dict, config: dict | None = None) -> ProviderResult:
    config = config or provider.config or {}
    configured = config.get("contacts") or config.get("default_contacts")
    limit = int(payload.get("limit") or config.get("limit") or 5)
    if isinstance(configured, list) and configured:
        contacts = [_normalize_contact(item) for item in configured if isinstance(item, dict)]
        return ProviderResult(ok=True, provider=provider.provider, data={"contacts": contacts[:limit]}, raw={"adapter": "builtin"})

    titles = _string_list(payload.get("titles")) or _string_list(config.get("titles")) or ["Head of Buying"]
    contacts = []
    for title in titles[:limit]:
        contacts.append(
            {
                "first_name": _first_name_for_title(title),
                "last_name": "Team",
                "title": title,
                "linkedin_url": None,
            }
        )
    return ProviderResult(ok=True, provider=provider.provider, data={"contacts": contacts}, raw={"adapter": "builtin"})


def _find_emails(provider: ProviderConfig, payload: dict, config: dict | None = None) -> ProviderResult:
    config = config or provider.config or {}
    configured = config.get("emails") or config.get("default_emails")
    if isinstance(configured, list) and configured:
        emails = [_normalize_email_item(item) for item in configured]
        return ProviderResult(ok=True, provider=provider.provider, data={"emails": emails}, raw={"adapter": "builtin"})

    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    domain = str(payload.get("domain") or config.get("domain") or "").strip().lower()
    if not domain:
        return ProviderResult(ok=True, provider=provider.provider, data={"emails": []}, raw={"adapter": "builtin"})

    first_name = str(contact.get("first_name") or "sales")
    last_name = str(contact.get("last_name") or "")
    candidates = infer_emails(first_name, last_name, domain, min_confidence=int(config.get("min_confidence") or 40))
    if not candidates:
        candidates = [{"address": f"sales@{domain}", "confidence": 55, "pattern": "generic"}]
    emails = [
        {
            "address": candidate["address"],
            "type": "generic" if candidate.get("pattern") == "generic" else "personal",
            "confidence": candidate.get("confidence", 50),
        }
        for candidate in candidates[: int(config.get("limit") or 3)]
    ]
    return ProviderResult(ok=True, provider=provider.provider, data={"emails": emails}, raw={"adapter": "builtin"})


def _verify_email(provider: ProviderConfig, payload: dict) -> ProviderResult:
    address = str(payload.get("email") or payload.get("address") or "").strip().lower()
    if not address and payload.get("test"):
        address = "test@smuaccessory.com"
    if not address:
        return ProviderResult(False, provider.provider, error_code="missing_email", error_message="Email is required")
    try:
        normalized = validate_email(address, check_deliverability=False).normalized
        return ProviderResult(
            ok=True,
            provider=provider.provider,
            data={"result": EmailStatus.valid.value, "score": 75, "normalized": normalized},
            raw={"adapter": "builtin"},
        )
    except EmailNotValidError as exc:
        return ProviderResult(
            ok=True,
            provider=provider.provider,
            data={"result": EmailStatus.invalid.value, "score": 0, "reason": str(exc)},
            raw={"adapter": "builtin"},
        )


def _normalize_company(item: dict, config: dict) -> dict:
    name = str(item.get("brand_name") or item.get("name") or item.get("company_name") or "").strip()
    website = item.get("website") or item.get("url")
    if not website and name:
        website = _website_for_name(name, config)
    country = item.get("headquarters_country") or item.get("country") or config.get("default_country")
    return {
        "brand_name": name,
        "legal_name": item.get("legal_name") or item.get("company_name") or name,
        "website": website,
        "domain": item.get("domain") or _domain_from_url(website),
        "country": country,
        "headquarters_country": country,
        "country_scope": str(config.get("country_semantics") or "headquarters"),
        "category": item.get("category") or config.get("default_category"),
        "company_type": item.get("company_type") or config.get("default_company_type"),
        "source_url": item.get("source_url"),
        "source_title": item.get("source_title"),
        "source_excerpt": item.get("source_excerpt"),
    }


def _company_from_keyword(keyword: str, config: dict, country: str | None, category: str | None) -> dict:
    website = _website_for_name(keyword, config)
    return {
        "brand_name": keyword,
        "legal_name": f"{keyword} Co.",
        "website": website,
        "domain": _domain_from_url(website),
        "country": country,
        # This value is copied from the query and is not evidence of brand origin.
        "country_scope": "query_target",
        "category": category,
        "company_type": config.get("default_company_type") or "brand",
    }


def _normalize_contact(item: dict) -> dict:
    return {
        "first_name": str(item.get("first_name") or item.get("name") or "Buying").strip(),
        "last_name": str(item.get("last_name") or "").strip(),
        "title": str(item.get("title") or "Head of Buying").strip(),
        "linkedin_url": item.get("linkedin_url"),
    }


def _normalize_email_item(item: object) -> dict:
    if isinstance(item, str):
        return {"address": item, "type": "personal"}
    if isinstance(item, dict):
        return {
            "address": item.get("address") or item.get("email"),
            "type": item.get("type") or "personal",
            "confidence": item.get("confidence") or 70,
        }
    return {"address": "", "type": "personal"}


def _website_for_name(name: str, config: dict) -> str:
    website_map = config.get("website_map") if isinstance(config.get("website_map"), dict) else {}
    mapped = website_map.get(name) or website_map.get(name.lower())
    if mapped:
        return str(mapped)
    suffix = str(config.get("default_domain_suffix") or "com").strip(".")
    return f"https://{slugify(name)}.{suffix}"


def _domain_from_url(url: object) -> str | None:
    if not url:
        return None
    value = str(url)
    parsed = urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    domain = parsed.hostname or value.replace("https://", "").replace("http://", "").split("/")[0]
    return domain.removeprefix("www.").lower()


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    return []


def _first_name_for_title(title: str) -> str:
    lowered = title.lower()
    if "sourcing" in lowered or "procurement" in lowered:
        return "Sourcing"
    if "product" in lowered or "merchandising" in lowered:
        return "Product"
    return "Buying"
