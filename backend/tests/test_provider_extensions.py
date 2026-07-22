from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules import services
from app.modules.models import ProviderConfig, SearchTask
from app.providers.base import ProviderResult
from app.shared.enums import TaskStatus


def test_hunter_domain_finder_fills_missing_company_domain(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    provider = ProviderConfig(
        provider="hunter-discover",
        type="company_search",
        priority=10,
        enabled=True,
        config={"adapter": "hunter", "domain_finder_endpoint_url": "https://configured.example/domain-finder"},
    )
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    monkeypatch.setattr(
        services,
        "execute_provider",
        lambda *_args: ProviderResult(True, "hunter-discover", data={"companies": [{"domain": "acme.com", "website": "https://acme.com"}]}),
    )

    with Session(engine) as db:
        task = SearchTask(name="Domain test", mode="exact_brand", status=TaskStatus.running, filters={}, progress={})
        db.add_all([provider, task])
        db.flush()
        payload = {"brand_name": "Acme"}

        services._resolve_company_domain(db, task, payload)

        assert payload == {"brand_name": "Acme", "domain": "acme.com", "website": "https://acme.com"}


def test_apollo_bulk_enrichment_merges_email_without_replacing_contact(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    provider = ProviderConfig(
        provider="apollo-contact-search",
        type="contact_search",
        priority=10,
        enabled=True,
        config={"adapter": "apollo", "bulk_enrichment_endpoint_url": "https://configured.example/bulk-match"},
    )
    monkeypatch.setattr(services, "provider_quota_available", lambda *_args: None)
    monkeypatch.setattr(
        services,
        "execute_provider",
        lambda *_args: ProviderResult(
            True,
            "apollo-contact-search",
            data={"contacts": [{"provider_person_id": "person-1", "first_name": "Jane", "last_name": "Doe", "email": "jane@acme.com", "emails": ["jane@acme.com"]}]},
        ),
    )

    with Session(engine) as db:
        task = SearchTask(name="Enrichment test", mode="exact_brand", status=TaskStatus.running, filters={}, progress={})
        db.add_all([provider, task])
        db.flush()

        result = services._enrich_contacts_with_apollo(
            db,
            task,
            provider,
            {"domain": "acme.com"},
            [{"provider_person_id": "person-1", "first_name": "Jane", "last_name": "", "last_name_obfuscated": "D**", "title": "Head of Buying"}],
        )

        assert result == [{"provider_person_id": "person-1", "first_name": "Jane", "last_name": "Doe", "last_name_obfuscated": "D**", "title": "Head of Buying", "email": "jane@acme.com", "emails": ["jane@acme.com"]}]
