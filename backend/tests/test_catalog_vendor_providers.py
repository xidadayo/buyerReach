import json
import ssl
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from app.modules.services import _validate_provider_config, provider_test_payload
from app.providers import http, vendors
from app.providers.base import ProviderResult


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_catalog_request_retries_transient_tls_failures_with_backoff(monkeypatch):
    attempts = []
    delays = []

    def fake_urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) < 3:
            raise URLError(ssl.SSLError("unexpected EOF while reading"))
        return FakeResponse({"data": {"companies": []}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    monkeypatch.setattr(vendors.time, "sleep", delays.append)
    provider = SimpleNamespace(provider="prospeo-company-search", type="company_search")

    result = vendors._request_json(provider, "https://configured.example/search", "POST", {}, {"domain": "mango.com"})

    assert result == {"data": {"companies": []}}
    assert len(attempts) == 3
    assert delays == [0.5, 1.0]


def test_catalog_contact_provider_uses_configured_templates_and_mapping(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse({"data": {"people": [{"first": "Jane", "last": "Doe", "mail": "Jane@Acme.com", "role": "Buyer"}]}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="prospeo-contacts", type="contact_search")
    config = {
        "adapter": "prospeo",
        "endpoint_url": "https://configured.example/people",
        "api_key": "token",
        "api_key_header": "X-API-Key",
        "request_method": "POST",
        "request_headers": {"X-Workspace": "primary"},
        "request_query": {"limit": "{{limit}}"},
        "request_body": {"first_name": "{{first_name}}", "domain": "{{domain}}", "titles": "{{titles}}"},
        "response_items_path": "data.people",
        "response_field_map": {"first_name": "first", "last_name": "last", "email": "mail", "title": "role"},
    }

    result = vendors.execute_vendor_provider(
        provider,
        {"company": {"domain": "acme.com"}, "contact": {"first_name": "Jane", "last_name": "Doe"}, "titles": ["Buyer"], "limit": 2},
        config,
    )

    assert result.ok is True
    assert result.data["contacts"] == [{"first_name": "Jane", "last_name": "Doe", "email": "jane@acme.com", "title": "Buyer", "emails": ["jane@acme.com"]}]
    assert captured["request"].get_header("X-api-key") == "token"
    assert captured["request"].get_header("X-workspace") == "primary"
    assert captured["request"].full_url == "https://configured.example/people?limit=2"
    assert json.loads(captured["request"].data) == {"first_name": "Jane", "domain": "acme.com", "titles": ["Buyer"]}


def test_execute_provider_routes_catalog_adapter_to_vendor_executor(monkeypatch):
    provider = SimpleNamespace(provider="prospeo-contacts", type="contact_search", config={"adapter": "prospeo"})
    expected = ProviderResult(True, provider.provider, data={"contacts": []})
    captured = {}

    def fake_execute_vendor_provider(received_provider, payload, config):
        captured.update(provider=received_provider, payload=payload, config=config)
        return expected

    monkeypatch.setattr(vendors, "execute_vendor_provider", fake_execute_vendor_provider)

    result = http.execute_provider(provider, {"company": {"domain": "acme.com"}})

    assert result is expected
    assert captured["provider"] is provider
    assert captured["config"]["adapter"] == "prospeo"


def test_catalog_connection_test_uses_configured_free_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse({"error": False, "job_title_suggestions": ["head of buying"]})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="prospeo-contacts", type="contact_search")

    result = vendors.test_catalog_provider_connection(
        provider,
        {
            "adapter": "prospeo",
            "api_key": "token",
            "api_key_header": "X-KEY",
            "connection_test_endpoint_url": "https://configured.example/search-suggestions",
            "connection_test_method": "POST",
            "connection_test_body": {"job_title_search": "buying"},
        },
    )

    assert result is not None and result.ok is True
    assert captured["request"].get_header("X-key") == "token"
    assert json.loads(captured["request"].data) == {"job_title_search": "buying"}


def test_get_quota_request_does_not_send_empty_body(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse({"response": {"remaining_credits": 12}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="prospeo-company-search", type="company_search")

    result = vendors.check_vendor_provider_quota(
        provider,
        {
            "adapter": "prospeo",
            "api_key": "token",
            "quota_endpoint_url": "https://configured.example/account-information",
            "quota_method": "GET",
            "quota_api_key_header": "X-KEY",
            "quota_headers": {},
            "quota_request_body": {},
            "quota_remaining_path": "response.remaining_credits",
        },
    )

    assert result is not None and result.ok is True
    assert captured["request"].data is None


def test_catalog_email_verifier_supports_query_key_and_status_mapping(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse({"verification": {"state": "deliverable", "score": 96}})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="emailable-verify", type="email_verifier")
    config = {
        "adapter": "emailable",
        "endpoint_url": "https://configured.example/verify",
        "api_key": "token",
        "api_key_query_param": "api_key",
        "request_method": "GET",
        "request_headers": {},
        "request_query": {"email": "{{email}}"},
        "request_body": {},
        "result_path": "verification.state",
        "score_path": "verification.score",
        "result_map": {"deliverable": "valid"},
    }

    result = vendors.execute_vendor_provider(provider, {"email": "test@example.com"}, config)

    assert result.ok is True
    assert result.data == {"result": "valid", "score": 96}
    assert "api_key=token" in captured["request"].full_url
    assert "email=test%40example.com" in captured["request"].full_url


def test_catalog_provider_configuration_requires_complete_mapping():
    config = {
        "adapter": "wappalyzer",
        "endpoint_url": "https://configured.example/lookup",
        "api_key": "token",
        "api_key_query_param": "key",
        "quota_endpoint_url": "https://configured.example/quota",
        "quota_api_key_query_param": "key",
        "quota_remaining_path": "data.remaining",
        "request_method": "GET",
        "request_headers": {},
        "request_query": {"url": "{{domain}}"},
        "request_body": {},
        "response_items_path": "data",
        "response_field_map": {"brand_name": "name"},
    }

    _validate_provider_config("company_search", config)
    with pytest.raises(ValueError, match="response items path"):
        _validate_provider_config("company_search", {**config, "response_items_path": ""})
    with pytest.raises(ValueError, match="does not support"):
        _validate_provider_config("email_verifier", config)


def test_catalog_test_payload_provides_company_name_for_templates():
    provider = SimpleNamespace(type="company_search")

    payload = provider_test_payload(provider, {"adapter": "crunchbase", "test_query": "BuyerReach Demo"})

    assert vendors._catalog_template_values(payload)["company_name"] == "BuyerReach Demo"


def test_catalog_templates_expose_normalized_official_domains(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeResponse({"results": []})

    monkeypatch.setattr(vendors, "urlopen", fake_urlopen)
    provider = SimpleNamespace(provider="prospeo-company-search", type="company_search")
    config = {
        "adapter": "prospeo",
        "endpoint_url": "https://configured.example/search-company",
        "api_key": "token",
        "api_key_header": "X-KEY",
        "request_method": "POST",
        "request_headers": {},
        "request_query": {},
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
        "response_field_map": {"brand_name": "company.name"},
    }

    result = vendors.execute_vendor_provider(
        provider,
        {"brand_keywords": ["Choles Bags"], "official_domains": ["https://www.cholesbags.example/"]},
        config,
    )

    assert result.ok is True
    assert json.loads(captured["request"].data)["filters"] == {
        "company": {
            "names": {"include": ["Choles Bags"]},
            "websites": {"include": ["cholesbags.example"]},
        }
    }


def test_catalog_company_search_reduces_subdomains_to_registrable_domains():
    values = vendors._catalog_template_values(
        {
            "official_domains": [
                "https://shop.mango.com/us/en",
                "https://store.example.co.uk/products",
            ]
        }
    )

    assert values["official_domains"] == ["mango.com", "example.co.uk"]
