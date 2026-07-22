from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.models import ProviderConfig
from app.providers.base import ProviderResult
from app.providers.vendors import check_vendor_provider_quota, execute_vendor_provider


ADAPTER_VERSION = "v1"


@dataclass(frozen=True)
class VendorWorkflowAdapter:
    vendor: str
    display_name: str
    supported_types: frozenset[str]
    configs: dict[str, dict[str, Any]]
    verification_only: bool = False
    capabilities: dict[str, Any] | None = None

    def provider(
        self, provider_type: str, api_key: str, *, priority: int = 100
    ) -> ProviderConfig | None:
        config = self.configs.get(provider_type)
        if config is None:
            return None
        return ProviderConfig(
            provider=f"{self.vendor}-{provider_type.replace('_', '-')}",
            type=provider_type,
            priority=priority,
            enabled=True,
            config={
                **config,
                "adapter": self.vendor,
                "api_key": api_key,
                "adapter_version": (
                    "hunter-v11"
                    if self.vendor == "hunter"
                    else "apollo-v8"
                    if self.vendor == "apollo"
                    else ADAPTER_VERSION
                ),
                "capabilities": self.capabilities or {},
            },
        )

    def check_availability(self, api_key: str) -> ProviderResult:
        provider_type = (
            "email_verifier" if self.verification_only else next(iter(self.supported_types))
        )
        provider = self.provider(provider_type, api_key)
        if provider is None:
            return ProviderResult(
                False,
                self.vendor,
                error_code="unsupported_type",
                error_message="Vendor has no testable capability",
            )
        result = check_vendor_provider_quota(provider, provider.config)
        return result or ProviderResult(
            False,
            self.vendor,
            error_code="missing_availability_api",
            error_message="Vendor has no availability API",
        )

    def execute(self, provider_type: str, api_key: str, payload: dict[str, Any]) -> ProviderResult:
        provider = self.provider(provider_type, api_key)
        if provider is None:
            return ProviderResult(
                False,
                self.vendor,
                error_code="unsupported_type",
                error_message=f"{self.display_name} does not support {provider_type}",
            )
        return execute_vendor_provider(provider, payload, provider.config)


_APOLLO_QUOTA = {
    "quota_endpoint_url": "https://api.apollo.io/api/v1/usage_stats/api_usage_stats",
    "quota_method": "POST",
    "quota_api_key_header": "x-api-key",
    "quota_remaining_path": "$endpoint.minute.left_over",
}

APOLLO = VendorWorkflowAdapter(
    vendor="apollo",
    display_name="Apollo",
    supported_types=frozenset({"company_search", "contact_search"}),
    capabilities={
        "supports_industry_include": True,
        "supports_industry_exclude": False,
        "supports_keyword_tags": True,
        "supports_business_type": True,
        "country_semantics": "headquarters",
        "category_semantics": "recall",
    },
    configs={
        "company_search": {
            **_APOLLO_QUOTA,
            "endpoint_url": "https://api.apollo.io/api/v1/mixed_companies/search",
            "supported_modes": ["brand_discovery", "exact_brand"],
            "limit": 20,
        },
        "contact_search": {
            **_APOLLO_QUOTA,
            "endpoint_url": "https://api.apollo.io/api/v1/mixed_people/api_search",
            "bulk_enrichment_endpoint_url": "https://api.apollo.io/api/v1/people/bulk_match",
            "bulk_enrichment_batch_size": 10,
            "bulk_enrichment_reveal_personal_emails": False,
            "bulk_enrichment_reveal_phone_number": False,
            "limit": 20,
        },
    },
)

_HUNTER_QUOTA = {
    "quota_endpoint_url": "https://api.hunter.io/v2/account",
    "quota_method": "GET",
    "quota_api_key_header": "X-API-KEY",
    "quota_remaining_path": "data.requests.credits.remaining",
    "quota_available_path": "data.requests.searches.available",
    "quota_used_path": "data.requests.searches.used",
    "quota_reset_at_path": "data.reset_date",
}

HUNTER = VendorWorkflowAdapter(
    vendor="hunter",
    display_name="Hunter",
    supported_types=frozenset(
        {"company_search", "contact_search", "brand_email_search", "email_finder", "email_verifier"}
    ),
    capabilities={
        "supports_industry_include": False,
        "supports_industry_exclude": False,
        "supports_keyword_tags": True,
        "supports_business_type": False,
        "country_semantics": "headquarters_or_registered",
        "category_semantics": "recall",
    },
    configs={
        "company_search": {
            **_HUNTER_QUOTA,
            "endpoint_url": "https://api.hunter.io/v2/discover",
            "domain_finder_endpoint_url": "https://api.hunter.io/v2/domain-finder",
            "company_enrichment_endpoint_url": "https://api.hunter.io/v2/companies/find",
            "supported_modes": ["brand_discovery", "exact_brand"],
            "limit": 20,
        },
        "contact_search": {
            **_HUNTER_QUOTA,
            "endpoint_url": "https://api.hunter.io/v2/domain-search",
            "limit": 20,
        },
        "brand_email_search": {
            **_HUNTER_QUOTA,
            "endpoint_url": "https://api.hunter.io/v2/domain-search",
            "email_count_endpoint_url": "https://api.hunter.io/v2/email-count",
            "limit": 20,
        },
        "email_finder": {
            **_HUNTER_QUOTA,
            "endpoint_url": "https://api.hunter.io/v2/email-finder",
            "limit": 1,
        },
        "email_verifier": {
            **_HUNTER_QUOTA,
            "quota_available_path": "data.requests.verifications.available",
            "quota_used_path": "data.requests.verifications.used",
            "endpoint_url": "https://api.hunter.io/v2/email-verifier",
        },
    },
)

_PROSPEO_QUOTA = {
    "quota_endpoint_url": "https://api.prospeo.io/account-information",
    "quota_method": "GET",
    "quota_api_key_header": "X-KEY",
    "quota_headers": {"User-Agent": "BuyerReach/1.0"},
    "quota_remaining_path": "response.remaining_credits",
    "quota_used_path": "response.used_credits",
    "quota_reset_at_path": "response.next_quota_renewal_date",
}

PROSPEO = VendorWorkflowAdapter(
    vendor="prospeo",
    display_name="Prospeo",
    supported_types=frozenset({"company_search", "contact_search", "email_finder"}),
    configs={
        "company_search": {
            **_PROSPEO_QUOTA,
            "endpoint_url": "https://api.prospeo.io/search-company",
            "request_method": "POST",
            "api_key_header": "X-KEY",
            "request_headers": {"User-Agent": "BuyerReach/1.0"},
            "request_body": {
                "page": 1,
                "filters": {
                    "company": {
                        "names": {"include": "{{brand_keywords}}"},
                        "websites": {"include": "{{official_domains}}"},
                    }
                },
            },
            "response_items_path": "results",
            "response_field_map": {
                "brand_name": "company.name",
                "legal_name": "company.name",
                "domain": "company.domain",
                "website": "company.website",
                "country": "company.location.country",
                "headquarters_country": "company.location.country",
                "category": "company.industry",
            },
            "supported_modes": ["exact_brand"],
            "limit": 25,
        },
        "contact_search": {
            **_PROSPEO_QUOTA,
            "endpoint_url": "https://api.prospeo.io/search-person",
            "request_method": "POST",
            "api_key_header": "X-KEY",
            "request_headers": {"User-Agent": "BuyerReach/1.0"},
            "request_body": {
                "page": 1,
                "filters": {
                    "company": {"websites": {"include": ["{{domain}}"]}},
                    "person_job_title": {"include": "{{titles}}"},
                },
            },
            "response_items_path": "results",
            "response_field_map": {
                "first_name": "person.first_name",
                "last_name": "person.last_name",
                "title": "person.current_job_title",
                "linkedin_url": "person.linkedin_url",
                "provider_person_id": "person.id",
            },
            "limit": 25,
        },
        "email_finder": {
            **_PROSPEO_QUOTA,
            "endpoint_url": "https://api.prospeo.io/enrich-person",
            "request_method": "POST",
            "api_key_header": "X-KEY",
            "request_headers": {"User-Agent": "BuyerReach/1.0"},
            "request_body": {
                "data": {
                    "full_name": "{{full_name}}",
                    "first_name": "{{first_name}}",
                    "last_name": "{{last_name}}",
                    "company_website": "{{domain}}",
                },
                "only_verified_email": True,
            },
            "response_items_path": "person",
            "response_field_map": {"address": "email.email"},
            "limit": 1,
        },
    },
)

ZEROBOUNCE = VendorWorkflowAdapter(
    vendor="zerobounce",
    display_name="ZeroBounce",
    supported_types=frozenset({"email_verifier"}),
    configs={
        "email_verifier": {
            "endpoint_url": "https://api.zerobounce.net/v2/validate",
            "quota_endpoint_url": "https://api.zerobounce.net/v2/getcredits",
            "quota_method": "GET",
            "quota_api_key_query_param": "api_key",
            "quota_remaining_path": "Credits",
            "validation_timeout": 30,
        }
    },
    verification_only=True,
)

AFTERSHIP_LOCAL = VendorWorkflowAdapter(
    vendor="aftership_local",
    display_name="本地邮箱验证",
    supported_types=frozenset({"email_verifier"}),
    configs={
        "email_verifier": {
            "endpoint_url": "http://email-verifier:8080/v1/verify",
            "quota_endpoint_url": "http://email-verifier:8080/ready",
            "validation_timeout": 30,
        }
    },
    verification_only=True,
)

WORKFLOW_ADAPTERS = {
    adapter.vendor: adapter for adapter in (APOLLO, HUNTER, PROSPEO, ZEROBOUNCE, AFTERSHIP_LOCAL)
}
SEARCH_VENDORS = ("apollo", "hunter", "prospeo")
VERIFICATION_VENDORS = ("aftership_local", "zerobounce", "hunter")


def adapter_for(vendor: str) -> VendorWorkflowAdapter | None:
    return WORKFLOW_ADAPTERS.get(vendor.strip().lower())
