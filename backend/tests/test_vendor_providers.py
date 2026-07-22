import json
from types import SimpleNamespace

from app.modules.services import provider_test_payload
from app.providers import http, vendors


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_hunter_company_enrichment_returns_actual_company_fields(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse(
            {
                "data": {
                    "name": "Moda",
                    "domain": "moda.example",
                    "category": {"industry": "Apparel", "subIndustry": "Fashion"},
                    "description": "Fast-fashion accessories brand",
                    "tags": ["handbags", "accessories"],
                }
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-company-search", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {"operation": "company_enrichment", "domain": "moda.example"},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "company_enrichment_endpoint_url": "https://configured.example/companies/find",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data["company"]["category"]["subIndustry"] == "Fashion"
    assert result.data["company"]["tags"] == ["handbags", "accessories"]
    assert captured["url"] == "https://configured.example/companies/find?domain=moda.example"


def test_apollo_company_search_converts_filters_and_normalizes_response(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "organizations": [
                    {
                        "name": "Acme",
                        "primary_domain": "acme.com",
                        "country": "US",
                        "industry": "Apparel",
                    }
                ]
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "brand_keywords": ["Acme"],
            "official_domains": ["https://www.acme.com"],
            "countries": ["US"],
            "brand_limit": 10,
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/companies",
            "api_key": "token",
        },
    )

    assert result.ok is True
    assert result.data["companies"] == [
        {
            "brand_name": "Acme",
            "legal_name": "Acme",
            "website": "https://acme.com",
            "domain": "acme.com",
            "country": "US",
            "headquarters_country": "US",
            "country_scope": "headquarters",
            "country_evidence": "apollo_response",
            "category": "Apparel",
            "industry": "Apparel",
            "industry_source": "apollo_company_profile",
            "industry_confidence": 85,
        }
    ]
    assert captured["request"].get_header("X-api-key") == "token"
    assert captured["request"].full_url == "https://configured.example/companies"
    assert json.loads(captured["request"].data) == {
        "page": 1,
        "per_page": 10,
        "q_organization_name": "Acme",
        "q_organization_domains_list": ["acme.com"],
    }
    assert captured["timeout"] == 30


def test_apollo_company_search_uses_accounts_when_organizations_are_empty(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(
            {
                "organizations": [],
                "accounts": [
                    {
                        "name": "MANGO",
                        "website_url": "http://www.mango.com",
                        "primary_domain": "mango.com",
                    }
                ],
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {"mode": "exact_brand", "brand_keywords": ["MANGO"], "official_domains": ["mango.com"]},
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/companies",
            "api_key": "token",
        },
    )

    assert result.ok is True
    assert result.data["companies"][0]["brand_name"] == "MANGO"
    assert result.data["companies"][0]["domain"] == "mango.com"
    assert result.data["companies"][0]["industry_source"] is None
    assert result.data["companies"][0]["industry_confidence"] is None


def test_apollo_brand_discovery_uses_category_tags_instead_of_company_name(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "organizations": [
                    {
                        "name": "Maison Bags",
                        "primary_domain": "maisonbags.fr",
                        "country": "FR",
                        "industry": "Fashion",
                    }
                ]
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "mode": "brand_discovery",
            "brand_keywords": [],
            "categories": ["bags"],
            "countries": ["France"],
            "brand_limit": 10,
            "discovery_page": 3,
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/companies",
            "api_key": "token",
        },
    )

    assert result.ok is True
    body = json.loads(captured["request"].data)
    assert body["q_organization_keyword_tags"] == ["bags", "handbags", "travel bags"]
    assert body["organization_locations"] == ["France"]
    assert body["page"] == 3
    assert "q_organization_name" not in body
    assert captured["timeout"] == 30


def test_apollo_single_hq_filter_supplies_country_evidence_when_response_omits_it(
    monkeypatch,
):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "organizations": [
                    {"name": "Italian Bags", "primary_domain": "bags.example"}
                ]
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {
            "mode": "brand_discovery",
            "categories": ["bag"],
            "countries": ["意大利"],
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/companies",
            "api_key": "token",
        },
    )

    company = result.data["companies"][0]
    assert company["headquarters_country"] == "Italy"
    assert company["country_evidence"] == "apollo_organization_locations_filter"


def test_apollo_brand_discovery_translates_chinese_search_filters(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        assert timeout == 30
        captured["body"] = json.loads(request.data)
        return FakeResponse({"organizations": []})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "mode": "brand_discovery",
            "categories": ["箱包"],
            "countries": ["美国"],
            "company_types": ["采购商"],
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/companies",
            "api_key": "token",
        },
    )

    assert result.ok is True
    assert captured["body"]["organization_locations"] == ["United States"]
    assert captured["body"]["q_organization_keyword_tags"] == [
        "luggage",
        "bags",
        "handbags",
        "travel bags",
    ]


def test_hunter_email_finder_uses_contact_and_domain(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse({"data": {"email": "jane@acme.com", "score": 92}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-email", type="email_finder")

    result = vendors.execute_vendor_provider(
        provider,
        {"contact": {"first_name": "Jane", "last_name": "Doe"}, "domain": "https://www.acme.com"},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/email-finder",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data == {
        "emails": [{"address": "jane@acme.com", "type": "personal", "confidence": 92}]
    }
    assert "first_name=Jane" in captured["url"]
    assert "domain=acme.com" in captured["url"]
    assert "api_key" not in captured["url"]


def test_hunter_discover_builds_semantic_query_and_normalizes_companies(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "data": [
                    {
                        "domain": "example.it",
                        "organization": "Example Bags",
                        "emails_count": {"personal": 4, "generic": 2, "total": 6},
                    }
                ],
                "meta": {
                    "results": 1,
                    "filters": {"headquarters_location": {"include": [{"country": "IT"}]}},
                },
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {
            "mode": "brand_discovery",
            "brand_keywords": ["handbag manufacturer", "handbag supplier"],
            "categories": ["Handbags & Purses"],
            "countries": ["Italy"],
            "company_types": ["brand"],
        },
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data["companies"][0]["brand_name"] == "Example Bags"
    assert result.data["companies"][0]["domain"] == "example.it"
    assert result.data["companies"][0]["emails_count"] == 6
    assert result.data["companies"][0]["semantic_match"] is True
    assert result.data["companies"][0]["category"] is None
    assert result.data["companies"][0]["category"] is None
    assert result.data["companies"][0]["semantic_category_selective"] is True
    assert "handbag manufacturer" not in result.data["companies"][0]["source_excerpt"]
    assert "handbags" in result.data["companies"][0]["provider_query"]
    assert "handbag manufacturer" not in result.data["companies"][0]["provider_query"]
    assert result.data["companies"][0]["headquarters_country"] == "IT"
    assert captured["request"].full_url == "https://configured.example/discover"
    assert captured["request"].get_header("X-api-key") == "hunter key"
    body = json.loads(captured["request"].data)
    assert "query" not in body
    assert body["headquarters_location"] == {"include": [{"country": "IT"}]}
    assert body["keywords"] == {
        "include": ["handbags", "purses", "tote bags"],
        "match": "any",
    }
    assert captured["timeout"] == 30


def test_hunter_discover_does_not_copy_requested_country_without_provider_evidence(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse({"data": [{"domain": "global.example", "organization": "Global Bags"}]})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {"categories": ["Handbags"], "countries": ["Italy"]},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "api_key": "key",
        },
    )

    assert result.ok is True
    assert result.data["companies"][0]["country"] is None
    assert result.data["companies"][0]["headquarters_country"] is None


def test_hunter_discover_exposes_only_actual_provider_industry() -> None:
    company = vendors._hunter_discover_company(
        {"domain": "moda.example", "organization": "Moda", "industry": "Apparel"},
        {"categories": ["handbags"]},
        "Companies in handbags industry",
        {},
        1,
        100,
    )

    assert company["category"] == "Apparel"
    assert company["industry"] == "Apparel"
    assert company["industry_source"] == "hunter_company_profile"
    assert company["industry_confidence"] == 80


def test_hunter_discover_translates_chinese_search_filters_to_english(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        assert timeout == 30
        captured["body"] = json.loads(request.data)
        return FakeResponse({"data": [], "meta": {"results": 0, "filters": {}}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "mode": "brand_discovery",
            "categories": ["箱包"],
            "countries": ["US"],
            "company_types": ["分销商"],
        },
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "api_key": "key",
        },
    )

    assert result.ok is True
    body = captured["body"]
    assert "query" not in body
    assert body["headquarters_location"] == {"include": [{"country": "US"}]}
    assert body["keywords"] == {
        "include": ["luggage", "bags", "handbags", "travel bags"],
        "match": "any",
    }
    assert "company_type" not in body


def test_hunter_discover_marks_applied_category_filters_as_provider_evidence(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "data": [{"domain": "prune.com.ar", "organization": "Prune"}],
                "meta": {
                    "results": 100,
                    "filters": {
                        "headquarters_location": {"include": [{"country": "AR"}]},
                        "industry": {"include": ["Leather Product Manufacturing"]},
                        "keywords": {"include": ["luggage", "baggage"], "match": "any"},
                    },
                },
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {"categories": ["luggage"], "countries": ["Argentina"]},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "api_key": "key",
        },
    )

    company = result.data["companies"][0]
    assert company["headquarters_country"] == "AR"
    assert company["semantic_category_selective"] is False
    assert company["semantic_category_provider_confirmed"] is True
    assert company["company_category_evidence"] == "hunter_applied_industry_filter"
    assert company["provider_industry_terms"] == ["Leather Product Manufacturing"]


def test_hunter_discover_rejects_category_confirmation_widened_by_brand_keyword(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "data": [{"domain": "industrial.example", "organization": "Industrial Group"}],
                "meta": {
                    "filters": {
                        "keywords": {
                            "include": ["fashion luggage and bags", "brand"],
                            "match": "any",
                        }
                    }
                },
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")
    result = vendors.execute_vendor_provider(
        provider,
        {"categories": ["Fashion Luggage and Bags"], "company_types": ["brand"]},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "api_key": "key",
        },
    )

    company = result.data["companies"][0]
    assert company["semantic_category_provider_confirmed"] is False
    assert company["company_category_evidence"] is None


def test_hunter_domain_search_normalizes_emails_when_contact_is_missing(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "data": {
                    "emails": [
                        {
                            "value": "buyer@acme.com",
                            "type": "personal",
                            "confidence": 96,
                            "first_name": "Jane",
                            "last_name": "Doe",
                            "position": "Head of Buying",
                            "verification": {"status": "valid"},
                            "sources": [{"uri": "https://acme.com/team"}],
                        },
                        {"value": "info@acme.com", "type": "generic", "confidence": 80},
                    ]
                }
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-domain-email", type="brand_email_search")

    result = vendors.execute_vendor_provider(
        provider,
        {"domain_search": True, "domain": "https://www.acme.com", "limit": 5},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/domain-search",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data["emails"][0]["address"] == "buyer@acme.com"
    assert result.data["emails"][0]["title"] == "Head of Buying"
    assert result.data["emails"][1]["type"] == "generic"
    assert (
        captured["request"].full_url
        == "https://configured.example/domain-search?domain=acme.com&limit=5"
    )
    assert captured["request"].get_header("X-api-key") == "hunter key"
    assert captured["request"].get_header("User-agent") == "BuyerReach/1.0"
    assert captured["timeout"] == 30


def test_hunter_domain_finder_uses_configured_auxiliary_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        assert timeout == 30
        return FakeResponse({"data": {"domains": [{"domain": "acme.com"}]}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-discover", type="company_search")

    result = vendors.execute_vendor_provider(
        provider,
        {"operation": "domain_finder", "company_name": "Acme Corporation"},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/discover",
            "domain_finder_endpoint_url": "https://configured.example/domain-finder",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data["companies"] == [
        {"brand_name": "Acme Corporation", "domain": "acme.com", "website": "https://acme.com"}
    ]
    assert (
        captured["request"].full_url
        == "https://configured.example/domain-finder?company=Acme+Corporation"
    )
    assert captured["request"].get_header("X-api-key") == "hunter key"


def test_hunter_email_count_skips_domain_search_when_no_emails(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        assert timeout == 30
        return FakeResponse({"data": {"total": 0}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-domain-email", type="brand_email_search")

    result = vendors.execute_vendor_provider(
        provider,
        {"domain": "acme.com", "limit": 5},
        {
            "adapter": "hunter",
            "endpoint_url": "https://configured.example/domain-search",
            "email_count_endpoint_url": "https://configured.example/email-count",
            "api_key": "hunter key",
        },
    )

    assert result.ok is True
    assert result.data == {"emails": []}
    assert calls == ["https://configured.example/email-count?domain=acme.com"]


def test_apollo_bulk_enrichment_uses_configured_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        assert timeout == 30
        return FakeResponse(
            {
                "matches": [
                    {
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "title": "Buyer",
                        "email": "jane@acme.com",
                        "email_status": "verified",
                        "email_source": "apollo_native",
                    }
                ]
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-contact", type="contact_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "operation": "bulk_enrich",
            "company": {"domain": "acme.com"},
            "contacts": [{"first_name": "Jane", "last_name": "Doe"}],
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/contact-search",
            "bulk_enrichment_endpoint_url": "https://configured.example/bulk-match",
            "bulk_enrichment_reveal_personal_emails": False,
            "api_key": "master key",
        },
    )

    assert result.ok is True
    assert result.data["contacts"][0]["emails"] == ["jane@acme.com"]
    assert result.data["contacts"][0]["email_details"] == [
        {
            "address": "jane@acme.com",
            "verification_status": "verified",
            "verification_source": "apollo_native",
            "verification_provider": "apollo",
        }
    ]
    assert (
        captured["request"].full_url
        == "https://configured.example/bulk-match?reveal_personal_emails=False"
    )
    assert json.loads(captured["request"].data) == {
        "details": [{"first_name": "Jane", "last_name": "Doe", "domain": "acme.com"}]
    }


def test_apollo_search_identity_is_enriched_by_person_id(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse(
            {
                "matches": [
                    {
                        "id": "apollo-person-1",
                        "first_name": "Johnny",
                        "last_name": "Wei",
                        "title": "Sourcing Supervisor (window)",
                        "email": "johnny.wei@mango.com",
                    }
                ]
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-contact", type="contact_search")

    result = vendors.execute_vendor_provider(
        provider,
        {
            "operation": "bulk_enrich",
            "company": {"domain": "mango.com"},
            "contacts": [
                {
                    "provider_person_id": "apollo-person-1",
                    "first_name": "Johnny",
                    "last_name_obfuscated": "W**",
                    "title": "Sourcing Supervisor (window)",
                }
            ],
        },
        {
            "adapter": "apollo",
            "endpoint_url": "https://configured.example/contact-search",
            "bulk_enrichment_endpoint_url": "https://configured.example/bulk-match",
            "api_key": "master key",
        },
    )

    assert result.ok is True
    assert result.data["contacts"][0]["last_name"] == "Wei"
    assert result.data["contacts"][0]["emails"] == ["johnny.wei@mango.com"]
    assert json.loads(captured["request"].data) == {"details": [{"id": "apollo-person-1"}]}


def test_zerobounce_verifier_maps_risky_status(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse({"status": "catch-all"})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="zerobounce-verifier", type="email_verifier")

    result = vendors.execute_vendor_provider(
        provider,
        {"email": "buyer@acme.com"},
        {
            "adapter": "zerobounce",
            "endpoint_url": "https://configured.example/validate",
            "api_key": "key",
        },
    )

    assert result.ok is True
    assert result.data["result"] == "risky"
    assert result.data["score"] == 50
    assert result.data["is_catch_all"] is True
    assert result.data["mailbox_exists"] is False


def test_aftership_local_verifier_posts_with_bearer_token(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        assert timeout == 30
        return FakeResponse(
            {
                "result": "valid",
                "score": 90,
                "is_catch_all": False,
                "domain_deliverable": True,
                "mailbox_exists": True,
                "smtp_check": True,
                "reason": "smtp_recipient_accepted",
                "adapter_version": "v1",
            }
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="aftership-local", type="email_verifier")
    result = vendors.execute_vendor_provider(
        provider,
        {"email": "buyer@acme.com"},
        {
            "adapter": "aftership_local",
            "api_key": "internal-token",
            "endpoint_url": "http://email-verifier:8080/v1/verify",
        },
    )

    assert result.ok is True
    assert result.data["result"] == "valid"
    assert captured["request"].method == "POST"
    assert captured["request"].get_header("Authorization") == "Bearer internal-token"
    assert json.loads(captured["request"].data) == {"email": "buyer@acme.com", "smtp": True}


def test_generic_executor_routes_aftership_local_to_vendor_adapter(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse({"result": "valid", "score": 90})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(
        provider="aftership-local",
        type="email_verifier",
        config={
            "adapter": "aftership_local",
            "api_key": "internal-token",
            "endpoint_url": "http://email-verifier:8080/v1/verify",
        },
    )

    result = http.execute_provider(provider, {"email": "buyer@acme.com"})

    assert result.ok is True
    assert result.data["result"] == "valid"


def test_aftership_local_ready_is_a_free_connection_test(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.method == "GET"
        assert request.full_url.endswith("/ready")
        return FakeResponse({"status": "ready"})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="aftership-local", type="email_verifier")
    result = vendors.check_vendor_provider_quota(
        provider,
        {
            "adapter": "aftership_local",
            "api_key": "internal-token",
            "quota_endpoint_url": "http://email-verifier:8080/ready",
        },
    )
    assert result is not None and result.ok is True


def test_vendor_quota_check_uses_configured_endpoint_and_paths(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        assert timeout == 30
        return FakeResponse(
            {"data": {"credits": {"remaining": 0, "reset_at": "2026-08-01T00:00:00Z"}}}
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="hunter-email", type="email_finder")

    result = vendors.check_vendor_provider_quota(
        provider,
        {
            "adapter": "hunter",
            "api_key": "hunter key",
            "quota_endpoint_url": "https://configured.example/account",
            "quota_remaining_path": "data.credits.remaining",
            "quota_reset_at_path": "data.credits.reset_at",
        },
    )

    assert result is not None and result.ok is False
    assert result.error_code == "quota_exhausted"
    assert "2026-08-01T00:00:00Z" in str(result.error_message)
    assert captured["request"].full_url == "https://configured.example/account"
    assert captured["request"].get_header("X-api-key") == "hunter key"


def test_apollo_quota_check_uses_configured_post_and_endpoint_rate_limit(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        assert timeout == 30
        return FakeResponse(
            {'["api/v1/mixed_companies/search", "search"]': {"minute": {"left_over": 8}}}
        )

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.check_vendor_provider_quota(
        provider,
        {
            "adapter": "apollo",
            "api_key": "master key",
            "endpoint_url": "https://api.apollo.io/api/v1/mixed_companies/search",
            "quota_endpoint_url": "https://api.apollo.io/api/v1/usage_stats/api_usage_stats",
            "quota_method": "POST",
            "quota_api_key_header": "Authorization",
            "quota_api_key_prefix": "Bearer",
            "quota_remaining_path": "$endpoint.minute.left_over",
        },
    )

    assert result is not None and result.ok is True
    assert result.data["remaining"] == 8
    assert captured["request"].method == "POST"
    assert captured["request"].data == b"{}"
    assert captured["request"].get_header("Authorization") == "Bearer master key"


def test_apollo_quota_check_matches_real_usage_stats_endpoint_key(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse({'["api/v1/mixed_companies", "search"]': {"minute": {"left_over": 8}}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="apollo-company", type="company_search")

    result = vendors.check_vendor_provider_quota(
        provider,
        {
            "adapter": "apollo",
            "api_key": "master key",
            "endpoint_url": "https://api.apollo.io/api/v1/mixed_companies/search",
            "quota_endpoint_url": "https://api.apollo.io/api/v1/usage_stats/api_usage_stats",
            "quota_method": "POST",
            "quota_api_key_header": "x-api-key",
            "quota_remaining_path": "$endpoint.minute.left_over",
        },
    )

    assert result is not None and result.ok is True
    assert result.data["remaining"] == 8


def test_vendor_provider_rejects_missing_endpoint_url():
    provider = SimpleNamespace(provider="hunter-email", type="email_finder")

    result = vendors.execute_vendor_provider(
        provider,
        {"contact": {"first_name": "Jane", "last_name": "Doe"}, "domain": "acme.com"},
        {"adapter": "hunter", "api_key": "key"},
    )

    assert result.ok is False
    assert result.error_code == "missing_endpoint_url"


def test_provider_test_payload_supplies_email_for_email_verifier():
    provider = SimpleNamespace(type="email_verifier")

    assert provider_test_payload(provider, {"test_email": "qa@buyerreach.local"}) == {
        "email": "qa@buyerreach.local"
    }
    assert provider_test_payload(provider, {}) == {"email": "test@example.com"}


def test_provider_test_payload_supplies_domain_for_brand_email_search():
    provider = SimpleNamespace(type="brand_email_search")

    assert provider_test_payload(provider, {"test_domain": "mango.com", "limit": 8}) == {
        "domain_search": True,
        "domain": "mango.com",
        "limit": 8,
    }


def test_provider_test_payload_uses_configured_auxiliary_vendor_tests():
    hunter = SimpleNamespace(type="company_search")
    apollo = SimpleNamespace(type="contact_search")

    assert provider_test_payload(
        hunter,
        {
            "adapter": "hunter",
            "domain_finder_endpoint_url": "https://configured.example/domain-finder",
            "test_domain_finder_company": "Acme",
        },
    ) == {"operation": "domain_finder", "company_name": "Acme"}
    assert provider_test_payload(
        apollo,
        {
            "adapter": "apollo",
            "bulk_enrichment_endpoint_url": "https://configured.example/bulk-match",
            "test_bulk_enrichment": True,
            "test_domain": "acme.com",
        },
    ) == {
        "operation": "bulk_enrich",
        "company": {"brand_name": "BuyerReach Demo", "domain": "acme.com"},
        "contacts": [{"first_name": "Jane", "last_name": "Doe", "title": "Head of Buying"}],
    }
