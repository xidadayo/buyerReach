from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules import services
from app.modules.models import Brand, Company, Contact, EmailAddress, ProviderConfig, SearchTask, TaskItem
from app.shared.enums import EmailStatus, TaskStatus


def test_domain_search_creates_brand_emails_when_contact_identity_is_incomplete(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    company_provider = ProviderConfig(
        provider="company-source", type="company_search", priority=10, enabled=True, config={}
    )
    contact_provider = ProviderConfig(
        provider="contact-source", type="contact_search", priority=10, enabled=True, config={}
    )
    hunter_provider = ProviderConfig(
        provider="hunter-domain-email-search", type="brand_email_search", priority=10, enabled=True, config={}
    )

    def fake_waterfall(_db, provider_type, payload, _items_path, **kwargs):
        if provider_type == "contact_search":
            candidates = [
                {"first_name": "Sam", "last_name": "", "title": "Sourcing Manager"}
            ]
            assert kwargs["item_filter"](candidates) == []
            return contact_provider, [], []
        assert provider_type == "brand_email_search"
        assert payload["domain_search"] is True
        assert payload["domain"] == "acme.com"
        return hunter_provider, [
            {
                "address": "jane@acme.com",
                "type": "personal",
                "first_name": "Jane",
                "last_name": "Doe",
                "title": "Head of Buying",
            },
            {"address": "info@acme.com", "type": "generic"},
        ], []

    verified: list[str] = []

    def fake_verify(_db, email_id, **_kwargs):
        email = _db.get(EmailAddress, email_id)
        email.status = EmailStatus.valid
        verified.append(email.address)
        return email

    monkeypatch.setattr(services, "execute_provider_waterfall", fake_waterfall)
    monkeypatch.setattr(services, "verify_email", fake_verify)

    with Session(engine) as db:
        db.add_all([company_provider, contact_provider, hunter_provider])
        db.add_all(
            [
                Brand(name="Existing Brand", normalized_name="existing-brand", status="active"),
                Contact(
                    first_name="Existing",
                    last_name="Contact",
                    full_name="Existing Contact",
                    status="valid",
                ),
                EmailAddress(
                    address="existing@example.com",
                    normalized_address="existing@example.com",
                    domain="example.com",
                    status=EmailStatus.valid,
                ),
            ]
        )
        task = SearchTask(
            name="Acme discovery",
            mode="exact_brand",
            status=TaskStatus.running,
            filters={
                "contacts_limit_per_brand": 5,
                "target_titles": ["Head of Buying"],
            },
            progress={},
        )
        db.add(task)
        db.flush()

        completed = services._ingest_discovery(
            db,
            task,
            company_provider,
            [{"brand_name": "Acme", "legal_name": "Acme", "domain": "acme.com"}],
        )

        assert completed is True
        company = db.scalar(select(Company).where(Company.domain == "acme.com"))
        brand = db.scalar(select(Brand).where(Brand.name == "Acme"))
        contact = db.scalar(select(Contact).where(Contact.full_name == "Jane Doe"))
        incomplete_contact = db.scalar(select(Contact).where(Contact.full_name == "Sam"))
        emails = list(
            db.scalars(
                select(EmailAddress)
                .where(EmailAddress.brand_id == brand.id)
                .order_by(EmailAddress.address)
            )
        )
        assert company is not None and brand is not None and contact is not None
        assert incomplete_contact is None
        assert [(email.address, email.contact_id, email.brand_id) for email in emails] == [
            ("jane@acme.com", contact.id, brand.id),
        ]
        assert verified == ["jane@acme.com"]
        task_items = list(
            db.scalars(select(TaskItem).where(TaskItem.stage == "email_domain_discovered"))
        )
        assert len(task_items) == 1
        contact_items = list(
            db.scalars(select(TaskItem).where(TaskItem.entity_type == "contact"))
        )
        assert len(contact_items) == 1
        assert task.progress == {
            "brands": 1,
            "websites": 0,
            "contacts": 1,
            "emails": 1,
        }


def test_contact_filter_requires_target_title_match() -> None:
    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "title": "Senior Sourcing Manager"},
        {"first_name": "John", "last_name": "Doe", "title": "Chief Financial Officer"},
    ]

    assert services._usable_contact_items(contacts, ["Sourcing Manager"]) == [contacts[0]]


def test_contact_filter_accepts_apollo_search_identity_for_enrichment() -> None:
    contact = {
        "provider_person_id": "apollo-person-1",
        "first_name": "Johnny",
        "last_name": "",
        "last_name_obfuscated": "W**",
        "title": "Sourcing Supervisor (window)",
    }

    assert services._usable_contact_items(
        [contact],
        ["Sourcing Supervisor"],
        allow_provider_id=True,
    ) == [contact]
    assert services._usable_contact_items([contact], ["Sourcing Supervisor"]) == []


def test_domain_search_records_when_no_provider_is_enabled() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        company = Company(legal_name="Acme", domain="acme.com")
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        task = SearchTask(
            name="Acme retry",
            mode="exact_brand",
            status=TaskStatus.running,
            filters={"contacts_limit_per_brand": 5},
            progress={},
        )
        db.add_all([company, brand, task])
        db.flush()

        discovered = services._discover_emails_by_domain(db, task, company, brand)

        item = db.scalar(select(TaskItem).where(TaskItem.task_id == task.id))
        assert discovered == 0
        assert item is not None
        assert item.stage == "email_provider_unavailable"
        assert item.status == TaskStatus.partial
        assert item.error_message == "No enabled brand_email_search Provider is configured"
