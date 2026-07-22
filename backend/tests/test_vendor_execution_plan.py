from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.core.database import Base
from app.modules import services
from app.modules.models import (
    Brand,
    EmailVerification,
    ProviderConfig,
    SearchTask,
    SourceEvidence,
    VendorCredential,
    VendorStrategy,
)
from app.modules.schemas import ContactCreate, EmailCreate, SearchTaskCreate
from app.pipeline import vendor_pipeline
from app.pipeline.vendor_pipeline import (
    CompanyPipelineResult,
    VendorPipelineResult,
    _company_filter,
    _filter_contacts_for_titles,
)
from app.providers.base import ProviderResult
from app.shared.enums import SourceType


def _db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _provider(vendor: str) -> ProviderConfig:
    return ProviderConfig(
        provider=f"{vendor}-company-search",
        type="company_search",
        priority=10 if vendor == "apollo" else 20,
        enabled=True,
        config={"adapter": vendor},
    )


def test_waterfall_allowed_vendors_never_calls_unselected_vendor(monkeypatch) -> None:
    calls: list[str] = []
    with _db() as db:
        db.add_all([_provider("apollo"), _provider("hunter")])
        db.flush()

        def execute(provider, _payload):
            calls.append(provider.provider)
            return ProviderResult(
                True,
                provider.provider,
                data={"companies": [{"brand_name": provider.provider}]},
            )

        monkeypatch.setattr(services, "execute_provider", execute)
        monkeypatch.setattr(services, "provider_quota_available", lambda *_args, **_kwargs: None)
        provider, items, errors = services.execute_provider_waterfall(
            db,
            "company_search",
            {},
            "companies",
            allowed_vendors={"hunter"},
        )

        assert provider is not None and provider.provider == "hunter-company-search"
        assert items == [{"brand_name": "hunter-company-search"}]
        assert errors == []
        assert calls == ["hunter-company-search"]


def test_exact_brand_filter_rejects_wrong_domain_before_contact_stage() -> None:
    task = SearchTask(
        name="exact",
        mode="exact_brand",
        filters={"brand_keywords": ["Mango"], "official_domains": ["mango.com"]},
        progress={},
    )
    filtered = _company_filter(task)(
        [
            {"brand_name": "Mango", "domain": "wrong.example"},
            {"brand_name": "Mango", "domain": "mango.com"},
        ]
    )
    assert filtered == [{"brand_name": "Mango", "domain": "mango.com"}]


def test_apollo_hq_filter_evidence_passes_discovery_business_filter() -> None:
    task = SearchTask(
        name="italian bags",
        mode="brand_discovery",
        filters={
            "countries": ["意大利"],
            "categories": ["bag"],
            "require_website": True,
            "min_relevance": 45,
        },
        progress={},
    )
    company = {
        "brand_name": "Italian Bags",
        "domain": "bags.example",
        "website": "https://bags.example",
        "country": "Italy",
        "headquarters_country": "Italy",
        "country_scope": "headquarters",
        "country_evidence": "apollo_organization_locations_filter",
        "semantic_match": True,
        "semantic_category_match": True,
    }
    assert _company_filter(task)([company])[0]["brand_name"] == "Italian Bags"


def test_contact_stage_rejects_non_target_titles_for_every_vendor() -> None:
    contacts = [
        {"first_name": "Alice", "title": "Head of Buying"},
        {"first_name": "Bob", "title": "Sales Manager"},
    ]
    assert _filter_contacts_for_titles(contacts, ["Buyer", "Head of Buying"]) == [contacts[0]]


def test_new_vendor_plan_freezes_selected_vendors_and_adapter_versions() -> None:
    with _db() as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=["hunter"],
                verification_vendor="hunter",
                adapter_version="v1",
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="apollo",
                    encrypted_api_key=encrypt_secret("apollo-key"),
                    enabled=True,
                    last_test_ok=True,
                ),
                VendorCredential(
                    vendor="hunter",
                    encrypted_api_key=encrypt_secret("hunter-key"),
                    enabled=True,
                    last_test_ok=True,
                ),
            ]
        )
        payload = SearchTaskCreate(
            name="both",
            mode="exact_brand",
            brand_keywords=["Mango"],
            official_domains=["mango.com"],
            selected_vendors=["apollo", "hunter"],
        )
        task = services.create_search_task(db, payload)
        plan = services.ensure_task_vendor_plan(db, task)

        assert plan.execution_mode == "apollo_hunter"
        assert plan.selected_vendors == ["apollo", "hunter"]
        assert plan.vendor_routes["apollo"]["adapter_version"] == "apollo-v8"
        assert plan.vendor_routes["hunter"]["adapter_version"] == "hunter-v11"
        assert "api_key" not in str(plan.vendor_routes).casefold()


def test_selected_vendor_requires_available_credential() -> None:
    with _db() as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=[],
                verification_vendor=None,
                adapter_version="v1",
            )
        )
        payload = SearchTaskCreate(
            name="missing",
            mode="exact_brand",
            brand_keywords=["Mango"],
            official_domains=["mango.com"],
            selected_vendors=["apollo"],
        )
        try:
            services.create_search_task(db, payload)
        except ValueError as exc:
            assert "apollo" in str(exc)
        else:
            raise AssertionError("missing credential must reject task creation")


def test_brand_discovery_vendor_plan_keeps_target_titles() -> None:
    payload = SearchTaskCreate(
        name="discovery",
        mode="brand_discovery",
        countries=["US"],
        categories=["bags"],
        target_titles=["Buyer"],
        selected_vendors=["apollo"],
    )
    assert payload.target_titles == ["Buyer"]


def test_legacy_task_cannot_be_queued() -> None:
    with _db() as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=["hunter"],
                verification_vendor="hunter",
                adapter_version="v1",
            )
        )
        task = SearchTask(name="legacy", mode="exact_brand", status="draft", filters={}, progress={})
        db.add(task)
        db.flush()

        try:
            services.queue_search_task(db, task.id)
        except ValueError as exc:
            assert "Legacy tasks cannot be started" in str(exc)
        else:
            raise AssertionError("legacy task must not enter the execution queue")


def test_vendor_settings_only_expose_full_pipeline_vendors() -> None:
    with _db() as db:
        db.add_all(
            [
                VendorCredential(vendor="apollo", encrypted_api_key=encrypt_secret("a")),
                VendorCredential(vendor="hunter", encrypted_api_key=encrypt_secret("h")),
                VendorCredential(vendor="prospeo", encrypted_api_key=encrypt_secret("p")),
            ]
        )
        db.flush()

        assert [item["vendor"] for item in services.list_vendor_credentials(db)] == [
            "apollo",
            "hunter",
        ]


def test_email_verifier_routing_never_reintroduces_unselected_vendor() -> None:
    with _db() as db:
        db.add(
            VendorStrategy(
                name="default",
                primary_vendor="apollo",
                fallback_vendors=["hunter"],
                verification_vendor="hunter",
                adapter_version="v1",
            )
        )
        db.add_all(
            [
                VendorCredential(
                    vendor="apollo", encrypted_api_key=encrypt_secret("a"), enabled=True
                ),
                VendorCredential(
                    vendor="hunter", encrypted_api_key=encrypt_secret("h"), enabled=True
                ),
            ]
        )
        task = SearchTask(
            name="apollo",
            mode="exact_brand",
            filters={"selected_vendors": ["apollo"]},
            progress={},
        )
        db.add(task)
        db.flush()
        services.ensure_task_vendor_plan(db, task)

        providers = services.enabled_providers(db, "email_verifier", task)
        assert all("hunter" not in provider.provider for provider in providers)


def test_apollo_verified_status_is_persisted_as_reliable_source_evidence() -> None:
    with _db() as db:
        brand = Brand(
            name="Apollo",
            normalized_name="apollo",
            primary_website="https://apollo.example",
        )
        db.add(brand)
        db.flush()
        contact = services.create_contact(
            db,
            ContactCreate(
                brand_id=brand.id,
                first_name="Verified",
                last_name="Buyer",
                title="Buyer",
            ),
            provider="apollo",
        )
        email = services.create_email(
            db,
            EmailCreate(
                contact_id=contact.id,
                brand_id=brand.id,
                address="verified.buyer@apollo.example",
            ),
            provider="apollo",
        )
        db.add(
            SourceEvidence(
                entity_type="email",
                entity_id=str(email.id),
                source_type=SourceType.commercial_api,
                provider="apollo",
                confidence=100,
            )
        )
        db.flush()

        services._apply_provider_verification(
            db,
            email,
            {
                "verification_status": "verified",
                "verification_provider": "apollo",
                "verification_source": "Requested from Apollo",
            },
            "apollo",
        )
        db.flush()

        verification = db.query(EmailVerification).one()
        assert email.status == "valid"
        assert email.authenticity_level == "verified"
        assert email.pool == "valid"
        assert verification.provider == "apollo"
        assert verification.raw_result["vendor_status"] == "verified"
        assert verification.raw_result["verification_source"] == "Requested from Apollo"


def test_pipeline_persistence_keeps_contacts_scoped_to_their_company(monkeypatch) -> None:
    contacts_created: list[tuple[str, str]] = []
    emails_created: list[tuple[str, str]] = []
    companies: dict[str, SimpleNamespace] = {}

    def get_company(_db, payload):
        name = payload["brand_name"]
        return companies.setdefault(name, SimpleNamespace(id=uuid4(), legal_name=name))

    def create_brand(_db, payload, **_kwargs):
        return SimpleNamespace(id=uuid4(), name=payload.name)

    def create_contact(_db, payload, **_kwargs):
        contacts_created.append((str(payload.brand_id), payload.first_name))
        return SimpleNamespace(id=uuid4())

    def create_email(_db, payload, **_kwargs):
        emails_created.append((str(payload.contact_id), str(payload.address)))
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(vendor_pipeline, "get_or_create_company", get_company)
    monkeypatch.setattr(vendor_pipeline, "create_brand", create_brand)
    monkeypatch.setattr(vendor_pipeline, "create_contact", create_contact)
    monkeypatch.setattr(vendor_pipeline, "create_email", create_email)
    monkeypatch.setattr(vendor_pipeline, "_record_task_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(vendor_pipeline, "_record_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        vendor_pipeline, "_apply_provider_verification", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(vendor_pipeline, "_ensure_email_verified", lambda *_args, **_kwargs: None)

    result = VendorPipelineResult(
        vendor="apollo",
        ok=True,
        companies=[
            CompanyPipelineResult(
                company={"brand_name": "Alpha", "domain": "alpha.example"},
                contacts=[{"first_name": "Alice", "title": "Buyer"}],
                emails=[{"address": "alice@alpha.example", "contact_key": "name:alice||buyer"}],
            ),
            CompanyPipelineResult(
                company={"brand_name": "Beta", "domain": "beta.example"},
                contacts=[{"first_name": "Bob", "title": "Buyer"}],
                emails=[{"address": "bob@beta.example", "contact_key": "name:bob||buyer"}],
            ),
        ],
    )
    task = SearchTask(name="scope", mode="brand_discovery", filters={}, progress={})
    with _db() as db:
        vendor_pipeline._persist_vendor_result(db, task, result)

    assert [name for _, name in contacts_created] == ["Alice", "Bob"]
    assert [address for _, address in emails_created] == [
        "alice@alpha.example",
        "bob@beta.example",
    ]
