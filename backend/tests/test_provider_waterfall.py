from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules import services
from app.modules.models import (
    ApiUsage,
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    ProviderConfig,
    SearchTask,
    TaskStageCheckpoint,
)
from app.providers.base import ProviderResult
from app.shared.models import utc_now


def _provider(
    name: str, provider_type: str, priority: int, quota: int | None = None
) -> ProviderConfig:
    return ProviderConfig(
        provider=name, type=provider_type, priority=priority, quota=quota, enabled=True, config={}
    )


def test_waterfall_falls_back_after_provider_failure(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        if provider.provider == "primary":
            return ProviderResult(False, provider.provider, error_message="rate limited")
        return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add_all(
            [
                _provider("primary", "company_search", 10),
                _provider("fallback", "company_search", 20),
            ]
        )
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", {}, "companies"
        )

        assert provider is not None and provider.provider == "fallback"
        assert items == [{"brand_name": "Acme"}]
        assert errors == ["primary: rate limited"]
        assert calls == ["primary", "fallback"]


def test_waterfall_falls_back_when_results_fail_business_filter(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        country = "US" if provider.provider == "primary" else "IT"
        return ProviderResult(
            True,
            provider.provider,
            data={
                "companies": [{"brand_name": provider.provider, "headquarters_country": country}]
            },
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add_all(
            [
                _provider("primary", "company_search", 10),
                _provider("fallback", "company_search", 20),
            ]
        )
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {},
            "companies",
            item_filter=lambda candidates: [
                item for item in candidates if item.get("headquarters_country") == "IT"
            ],
        )

        assert provider is not None and provider.provider == "fallback"
        assert items == [{"brand_name": "fallback", "headquarters_country": "IT"}]
        assert errors == ["primary: 1 mapped companies failed business filters"]
        assert calls == ["primary", "fallback"]


def test_waterfall_routes_company_providers_by_task_mode(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "Semantic Bags", "domain": "bags.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add_all(
            [
                ProviderConfig(
                    provider="hunter-discover",
                    type="company_search",
                    priority=10,
                    enabled=True,
                    config={"supported_modes": ["brand_discovery"]},
                ),
                ProviderConfig(
                    provider="apollo-company",
                    type="company_search",
                    priority=20,
                    enabled=True,
                    config={"supported_modes": ["exact_brand"]},
                ),
            ]
        )
        db.flush()

        provider, _, _ = services.execute_provider_waterfall(
            db, "company_search", {"mode": "brand_discovery"}, "companies"
        )

        assert provider is not None and provider.provider == "hunter-discover"
        assert calls == ["hunter-discover"]


def test_brand_discovery_searches_each_country_category_combination(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[tuple[str, str]] = []

    def fake_execute(provider, payload):
        country = payload["countries"][0]
        category = payload["categories"][0]
        calls.append((country, category))
        slug = f"{country}-{category}".lower()
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": slug, "domain": f"{slug}.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add(_provider("company-source", "company_search", 10))
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {
                "mode": "brand_discovery",
                "countries": ["France", "Italy"],
                "categories": ["bags", "belts"],
                "category_match_mode": "any",
            },
            "companies",
        )

        usage = db.query(ApiUsage).filter(ApiUsage.provider == "company-source").one()
        assert provider is not None and provider.provider == "company-source"
        assert calls == [
            ("France", "bags"),
            ("France", "belts"),
            ("Italy", "bags"),
            ("Italy", "belts"),
        ]
        assert len(items) == 4
        assert usage.calls == 4
        assert errors == []


def test_compound_brand_discovery_keeps_categories_in_one_provider_query(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    payloads: list[dict] = []

    def fake_execute(provider, payload):
        payloads.append(payload)
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "Moda Bags", "domain": "modabags.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add(_provider("company-source", "company_search", 10))
        db.flush()
        provider, _, _ = services.execute_provider_waterfall(
            db,
            "company_search",
            {
                "mode": "brand_discovery",
                "countries": ["Mexico"],
                "categories": ["fast fashion", "luggage"],
                "category_match_mode": "all",
            },
            "companies",
        )

    assert provider is not None
    assert payloads[0]["categories"] == ["fast fashion", "luggage"]


def test_discovery_does_not_send_person_roles_to_company_search(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    payloads: list[dict] = []

    def fake_execute(provider, payload):
        payloads.append(payload)
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "bags", "domain": "bags.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add(_provider("company-source", "company_search", 10))
        db.flush()
        services.execute_provider_waterfall(
            db,
            "company_search",
            {
                "mode": "brand_discovery",
                "countries": ["US"],
                "categories": ["fashion luggage"],
                "company_types": ["distributor", "buyer"],
            },
            "companies",
        )

    assert len(payloads) == 1
    assert payloads[0]["countries"] == ["US"]
    assert payloads[0]["categories"] == ["fashion luggage"]
    assert "company_types" not in payloads[0]


def test_waterfall_skips_vendor_when_quota_endpoint_reports_exhausted(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        return ProviderResult(
            True, provider.provider, data={"emails": [{"address": "buyer@acme.com"}]}
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        exhausted = _provider("exhausted", "email_finder", 10)
        exhausted.config = {"adapter": "hunter"}
        db.add_all([exhausted, _provider("fallback", "email_finder", 20)])
        db.flush()

        def fake_quota(provider, _config):
            if provider.provider == "exhausted":
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="quota_exhausted",
                    error_message="Provider reports no remaining quota; provider reports reset at 2026-08-01T00:00:00Z",
                )
            return None

        monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)

        provider, items, errors = services.execute_provider_waterfall(
            db, "email_finder", {}, "emails"
        )

        assert provider is not None and provider.provider == "fallback"
        assert items == [{"address": "buyer@acme.com"}]
        assert errors == [
            "exhausted: Provider reports no remaining quota; provider reports reset at 2026-08-01T00:00:00Z"
        ]
        assert calls == ["fallback"]


def test_hunter_discover_ignores_depleted_search_credits(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    quota_calls: list[str] = []
    execute_calls: list[str] = []

    def fake_quota(provider, _config):
        quota_calls.append(provider.provider)
        return ProviderResult(
            False,
            provider.provider,
            error_code="quota_exhausted",
            error_message="Provider reports no remaining quota",
        )

    def fake_execute(provider, _payload):
        execute_calls.append(provider.provider)
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "Free Discover Result"}]},
        )

    monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)
    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {
            "adapter": "hunter",
            "supported_modes": ["brand_discovery"],
            "discovery_pagination_enabled": True,
        }
        db.add(hunter)
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {"mode": "brand_discovery", "countries": ["Mexico"], "categories": ["Bags"]},
            "companies",
        )

        assert provider is not None and provider.provider == "hunter-company-search"
        assert items == [{"brand_name": "Free Discover Result"}]
        assert errors == []
        assert quota_calls == []
        assert execute_calls == ["hunter-company-search"]


def test_repeated_hunter_discovery_advances_to_next_result_page(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    offsets: list[int] = []

    def fake_execute(provider, payload):
        offsets.append(int(payload.get("discovery_offset") or 0))
        offset = offsets[-1]
        return ProviderResult(
            True,
            provider.provider,
            data={
                "companies": [
                    {"brand_name": f"Brand {offset}", "domain": f"brand-{offset}.example"}
                ]
            },
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    filters = {"mode": "brand_discovery", "countries": ["France"], "categories": ["bags"]}
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {
            "adapter": "hunter",
            "supported_modes": ["brand_discovery"],
            "discovery_pagination_enabled": True,
        }
        first = SearchTask(name="first", mode="brand_discovery", filters=filters, progress={})
        second = SearchTask(name="second", mode="brand_discovery", filters=filters, progress={})
        db.add_all([hunter, first, second])
        db.flush()

        services.execute_provider_waterfall(db, "company_search", filters, "companies", task=first)
        services.execute_provider_waterfall(db, "company_search", filters, "companies", task=second)

    assert offsets == [0, 100]


def test_hunter_discovery_defaults_to_first_page_without_premium_pagination(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    offsets: list[int] = []

    def fake_execute(provider, payload):
        offsets.append(int(payload.get("discovery_offset") or 0))
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "First Page", "domain": "first.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    filters = {"mode": "brand_discovery", "countries": ["US"], "categories": ["bags"]}
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {"adapter": "hunter", "supported_modes": ["brand_discovery"]}
        first = SearchTask(name="first", mode="brand_discovery", filters=filters, progress={})
        second = SearchTask(name="second", mode="brand_discovery", filters=filters, progress={})
        db.add_all([hunter, first, second])
        db.flush()

        first_provider, _, _ = services.execute_provider_waterfall(
            db, "company_search", filters, "companies", task=first
        )
        second_provider, second_items, _ = services.execute_provider_waterfall(
            db, "company_search", filters, "companies", task=second
        )

    assert offsets == [0, 0]
    assert first_provider is not None
    assert second_provider is not None
    assert second_items == [{"brand_name": "First Page", "domain": "first.example"}]


def test_hunter_without_new_candidates_continues_to_apollo(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        company = (
            {"brand_name": "Known Bags", "domain": "known.example", "country": "US"}
            if provider.provider == "hunter-company-search"
            else {"brand_name": "New Apollo Bags", "domain": "new-apollo.example", "country": "US"}
        )
        return ProviderResult(True, provider.provider, data={"companies": [company]})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    filters = {"mode": "brand_discovery", "countries": ["US"], "categories": ["bags"]}
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {"adapter": "hunter", "supported_modes": ["brand_discovery"]}
        apollo = _provider("apollo-company-search", "company_search", 20)
        apollo.config = {"adapter": "apollo", "supported_modes": ["brand_discovery"]}
        known = DiscoveryCandidate(
            name="Known Bags",
            normalized_name="known-bags",
            domain="known.example",
            normalized_domain="known.example",
            dedupe_key="domain:known.example",
            provider="hunter-company-search",
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        task = SearchTask(name="fallback", mode="brand_discovery", filters=filters, progress={})
        db.add_all([hunter, apollo, known, task])
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", filters, "companies", task=task
        )

    assert calls == ["hunter-company-search", "apollo-company-search"]
    assert provider is not None and provider.provider == "apollo-company-search"
    assert items == [
        {"brand_name": "New Apollo Bags", "domain": "new-apollo.example", "country": "US"}
    ]
    assert errors == []


def test_repeated_hunter_discovery_excludes_previously_returned_candidate(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fake_execute(provider, _payload):
        return ProviderResult(
            True,
            provider.provider,
            data={"companies": [{"brand_name": "Repeat Brand", "domain": "repeat.example"}]},
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    filters = {"mode": "brand_discovery", "countries": ["France"], "categories": ["bags"]}
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {
            "adapter": "hunter",
            "supported_modes": ["brand_discovery"],
            "discovery_pagination_enabled": True,
        }
        first = SearchTask(name="first", mode="brand_discovery", filters=filters, progress={})
        second = SearchTask(name="second", mode="brand_discovery", filters=filters, progress={})
        candidate = DiscoveryCandidate(
            name="Repeat Brand",
            normalized_name="repeat-brand",
            domain="repeat.example",
            normalized_domain="repeat.example",
            dedupe_key="domain:repeat.example",
            provider="hunter-company-search",
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
        db.add_all([hunter, first, second])
        db.flush()
        provider, _, _ = services.execute_provider_waterfall(
            db, "company_search", filters, "companies", task=first
        )
        assert provider is not None
        db.add(candidate)
        db.flush()
        db.add(
            DiscoveryCandidateHit(
                candidate_id=candidate.id, task_id=first.id, provider=provider.provider
            )
        )
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", filters, "companies", task=second
        )
        checkpoint = db.scalar(
            select(TaskStageCheckpoint).where(TaskStageCheckpoint.task_id == second.id)
        )

    assert provider is None
    assert items == []
    assert errors == []
    assert checkpoint is not None and checkpoint.error_code == "no_new_candidates"


def test_repeated_apollo_discovery_advances_to_next_result_page(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    pages: list[int] = []

    def fake_execute(provider, payload):
        pages.append(int(payload.get("discovery_page") or 1))
        page = pages[-1]
        return ProviderResult(
            True,
            provider.provider,
            data={
                "companies": [{"brand_name": f"Apollo {page}", "domain": f"apollo-{page}.example"}]
            },
        )

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    filters = {"mode": "brand_discovery", "countries": ["France"], "categories": ["bags"]}
    with Session(engine) as db:
        apollo = _provider("apollo-company-search", "company_search", 10)
        apollo.config = {"adapter": "apollo", "supported_modes": ["brand_discovery"]}
        first = SearchTask(name="first", mode="brand_discovery", filters=filters, progress={})
        second = SearchTask(name="second", mode="brand_discovery", filters=filters, progress={})
        db.add_all([apollo, first, second])
        db.flush()

        services.execute_provider_waterfall(db, "company_search", filters, "companies", task=first)
        services.execute_provider_waterfall(db, "company_search", filters, "companies", task=second)

    assert pages == [1, 2]


def test_hunter_paid_company_lookup_still_checks_search_credits(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    execute_calls: list[str] = []

    def fake_quota(provider, _config):
        return ProviderResult(
            False,
            provider.provider,
            error_code="quota_exhausted",
            error_message="Provider reports no remaining quota",
        )

    def fake_execute(provider, _payload):
        execute_calls.append(provider.provider)
        return ProviderResult(
            True, provider.provider, data={"companies": [{"brand_name": "Unexpected"}]}
        )

    monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)
    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        hunter = _provider("hunter-company-search", "company_search", 10)
        hunter.config = {"adapter": "hunter", "supported_modes": ["exact_brand"]}
        db.add(hunter)
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {"mode": "exact_brand", "brand_keywords": ["Mango"]},
            "companies",
        )

        assert provider is None
        assert items == []
        assert errors == ["hunter-company-search: Provider reports no remaining quota"]
        assert execute_calls == []


def test_waterfall_stops_rate_limited_provider_without_persisting_a_circuit(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fake_execute(provider, _payload):
        if provider.provider == "primary":
            return ProviderResult(
                False, provider.provider, error_code="http_429", error_message="too many requests"
            )
        return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        primary = _provider("primary", "company_search", 10)
        primary.config = {"adapter": "builtin"}
        fallback = _provider("fallback", "company_search", 20)
        db.add_all([primary, fallback])
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", {}, "companies"
        )
        db.refresh(primary)

        assert provider is not None and provider.provider == "fallback"
        assert items == [{"brand_name": "Acme"}]
        assert errors == ["primary: too many requests"]
        runtime = services.decrypt_provider_config(primary.config or {})
        assert "circuit_last_error" not in runtime
        assert "circuit_open_until" not in runtime


def test_waterfall_does_not_infer_low_quota_or_persist_a_quota_snapshot(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fake_quota(provider, _config):
        if provider.provider == "primary":
            return ProviderResult(
                True, provider.provider, data={"remaining": 1, "reset_at": "2026-08-01T00:00:00Z"}
            )
        return None

    def fake_execute(provider, _payload):
        return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

    monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)
    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        primary = _provider("primary", "company_search", 10)
        primary.config = {"adapter": "builtin", "quota_soft_threshold": 1}
        fallback = _provider("fallback", "company_search", 20)
        db.add_all([primary, fallback])
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", {}, "companies"
        )
        db.refresh(primary)

        assert provider is not None and provider.provider == "primary"
        assert items == [{"brand_name": "Acme"}]
        assert errors == []
        runtime = services.decrypt_provider_config(primary.config or {})
        assert "quota_remaining" not in runtime
        assert "quota_reset_at" not in runtime
        assert "circuit_open_until" not in runtime


def test_waterfall_ignores_legacy_local_circuit_state(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls: list[str] = []

    def fake_execute(provider, _payload):
        calls.append(provider.provider)
        return ProviderResult(True, provider.provider, data={"companies": [{"brand_name": "Acme"}]})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        primary = _provider("primary", "company_search", 10)
        primary.config = {"adapter": "builtin", "circuit_open_until": "2099-01-01T00:00:00+00:00"}
        fallback = _provider("fallback", "company_search", 20)
        db.add_all([primary, fallback])
        db.flush()

        provider, items, errors = services.execute_provider_waterfall(
            db, "company_search", {}, "companies"
        )

        assert provider is not None and provider.provider == "primary"
        assert items == [{"brand_name": "Acme"}]
        assert errors == []
        assert calls == ["primary"]


def test_verifier_waterfall_uses_next_provider_after_unknown_result(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fake_execute(provider, _payload):
        if provider.provider == "first-verifier":
            return ProviderResult(True, provider.provider, data={"result": "unknown", "score": 0})
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 95})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add_all(
            [
                _provider("first-verifier", "email_verifier", 10),
                _provider("second-verifier", "email_verifier", 20),
            ]
        )
        db.flush()

        provider, result, errors = services.execute_email_verifier_waterfall(db, "buyer@acme.com")

        assert provider is not None and provider.provider == "second-verifier"
        assert result["result"] == "valid"
        assert result["score"] == 95
        assert errors == ["first-verifier: returned an inconclusive result"]


def test_verifier_waterfall_uses_backup_when_quota_endpoint_reports_exhausted(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fake_execute(provider, _payload):
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 95})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        first = _provider("first-verifier", "email_verifier", 10)
        first.config = {"adapter": "zerobounce"}
        second = _provider("second-verifier", "email_verifier", 20)
        db.add_all([first, second])
        db.flush()

        def fake_quota(provider, _config):
            if provider.provider == "first-verifier":
                return ProviderResult(
                    False,
                    provider.provider,
                    error_code="quota_exhausted",
                    error_message="Provider reports no remaining quota",
                )
            return None

        monkeypatch.setattr(services, "check_vendor_provider_quota", fake_quota)

        provider, result, errors = services.execute_email_verifier_waterfall(db, "buyer@acme.com")

        assert provider is not None and provider.provider == "second-verifier"
        assert result["result"] == "valid"
        assert errors == ["first-verifier: Provider reports no remaining quota"]


def test_local_verifier_shadow_mode_keeps_paid_result(monkeypatch) -> None:
    from app.core.crypto import encrypt_secret
    from app.modules.models import VendorCredential, VendorStrategy

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []

    def fake_execute(provider, _payload):
        adapter = provider.config["adapter"]
        calls.append(adapter)
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 90})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    with Session(engine) as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=[],
                verification_vendor="zerobounce",
                adapter_version="v1",
                local_verification_mode="shadow",
                local_verification_rollout=100,
                local_verification_sample=10,
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="aftership_local",
                    encrypted_api_key=encrypt_secret("token"),
                    enabled=True,
                ),
                VendorCredential(
                    vendor="zerobounce", encrypted_api_key=encrypt_secret("key"), enabled=True
                ),
            ]
        )
        db.flush()
        provider, result, _errors = services.execute_email_verifier_waterfall(db, "buyer@acme.com")
        assert provider is not None and provider.provider.startswith("zerobounce-")
        assert calls == ["aftership_local", "zerobounce"]
        assert result["local_comparison"]["result"] == "valid"


def test_local_verifier_active_mode_stops_on_clear_valid(monkeypatch) -> None:
    from app.core.crypto import encrypt_secret
    from app.modules.models import VendorCredential, VendorStrategy

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []

    def fake_execute(provider, _payload):
        calls.append(provider.config["adapter"])
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 90})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    with Session(engine) as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=[],
                verification_vendor="zerobounce",
                adapter_version="v1",
                local_verification_mode="active",
                local_verification_rollout=100,
                local_verification_sample=1,
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="aftership_local",
                    encrypted_api_key=encrypt_secret("token"),
                    enabled=True,
                ),
                VendorCredential(
                    vendor="zerobounce", encrypted_api_key=encrypt_secret("key"), enabled=True
                ),
            ]
        )
        db.flush()
        provider, result, _errors = services.execute_email_verifier_waterfall(db, "buyer0@acme.com")
        assert provider is not None and provider.provider.startswith("aftership_local-")
        assert result["result"] == "valid"
        assert calls == ["aftership_local"]


def test_local_verifier_disabled_mode_does_not_call_local(monkeypatch) -> None:
    from app.core.crypto import encrypt_secret
    from app.modules.models import VendorCredential, VendorStrategy

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)

    def fake_execute(provider, _payload):
        calls.append(provider.config["adapter"])
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 90})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=[],
                verification_vendor="zerobounce",
                adapter_version="v1",
                local_verification_mode="disabled",
                local_verification_rollout=100,
                local_verification_sample=10,
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="aftership_local",
                    encrypted_api_key=encrypt_secret("token"),
                    enabled=True,
                ),
                VendorCredential(
                    vendor="zerobounce", encrypted_api_key=encrypt_secret("key"), enabled=True
                ),
            ]
        )
        db.flush()
        provider, _result, _errors = services.execute_email_verifier_waterfall(db, "buyer@acme.com")
        assert provider is not None and provider.provider.startswith("zerobounce-")
        assert calls == ["zerobounce"]


def test_local_catch_all_falls_through_to_paid_verifier(monkeypatch) -> None:
    from app.core.crypto import encrypt_secret
    from app.modules.models import VendorCredential, VendorStrategy

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)

    def fake_execute(provider, _payload):
        adapter = provider.config["adapter"]
        calls.append(adapter)
        if adapter == "aftership_local":
            return ProviderResult(
                True, provider.provider, data={"result": "risky", "score": 45, "is_catch_all": True}
            )
        return ProviderResult(True, provider.provider, data={"result": "valid", "score": 95})

    monkeypatch.setattr(services, "execute_provider", fake_execute)
    with Session(engine) as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=[],
                verification_vendor="zerobounce",
                adapter_version="v1",
                local_verification_mode="active",
                local_verification_rollout=100,
                local_verification_sample=10,
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="aftership_local",
                    encrypted_api_key=encrypt_secret("token"),
                    enabled=True,
                ),
                VendorCredential(
                    vendor="zerobounce", encrypted_api_key=encrypt_secret("key"), enabled=True
                ),
            ]
        )
        db.flush()
        provider, result, _errors = services.execute_email_verifier_waterfall(db, "buyer@acme.com")
        assert provider is not None and provider.provider.startswith("zerobounce-")
        assert calls == ["aftership_local", "zerobounce"]
        assert result["result"] == "valid"
        assert result["local_comparison"]["is_catch_all"] is True
