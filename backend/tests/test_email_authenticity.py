from types import SimpleNamespace
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules import services
from app.modules.models import Brand, EmailAddress, EmailVerification, SourceEvidence
from app.modules.schemas import ContactCreate, EmailCreate
from app.shared.enums import EmailPool, EmailStatus, SourceType


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _verified_result(*, catch_all: bool = False):
    provider = SimpleNamespace(provider="test-verifier")
    return provider, {
        "result": "risky" if catch_all else "valid",
        "score": 50 if catch_all else 95,
        "is_catch_all": catch_all,
        "domain_deliverable": True,
        "mailbox_exists": not catch_all,
    }, []


def test_contact_email_requires_delivery_identity_domain_and_evidence(monkeypatch) -> None:
    monkeypatch.setattr(services, "execute_email_verifier_waterfall", lambda _db, _address: _verified_result())
    with _session() as db:
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        db.add(brand)
        db.flush()
        contact = services.create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Head of Buying"),
            provider="apollo",
        )
        email = services.create_email(
            db,
            EmailCreate(contact_id=contact.id, address="jane.buyer@acme.com"),
            provider="hunter",
        )
        services.verify_email(db, email.id)
        db.flush()

        assert email.status == EmailStatus.valid
        assert email.authenticity_level == "verified"
        assert email.pool == EmailPool.valid
        assert email.domain_matches_brand is True
        assert email.deliverability_score == 95
        assert email.identity_score >= 90
        assert email.evidence_score >= 75
        assert email.confidence_score >= 80
        assert contact.status == "valid"
        verification = db.scalar(select(EmailVerification).where(EmailVerification.email_id == email.id))
        assert verification is not None
        assert verification.authenticity_level == "verified"


def test_catch_all_email_never_enters_valid_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "execute_email_verifier_waterfall",
        lambda _db, _address: _verified_result(catch_all=True),
    )
    with _session() as db:
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        db.add(brand)
        db.flush()
        contact = services.create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Buyer"),
        )
        email = services.create_email(db, EmailCreate(contact_id=contact.id, address="jane.buyer@acme.com"))
        services.verify_email(db, email.id)

        assert email.is_catch_all is True
        assert email.authenticity_level == "risky"
        assert email.pool == EmailPool.manual_review
        assert email.confidence_score <= 69
        assert contact.status == "pending_review"


def test_official_generic_email_can_be_verified_for_its_brand(monkeypatch) -> None:
    monkeypatch.setattr(services, "execute_email_verifier_waterfall", lambda _db, _address: _verified_result())
    with _session() as db:
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        db.add(brand)
        db.flush()
        email = services.create_email(
            db,
            EmailCreate(brand_id=brand.id, address="sales@acme.com", type="generic"),
            provider="website_parser",
        )
        db.add(
            SourceEvidence(
                entity_type="email",
                entity_id=str(email.id),
                source_type=SourceType.official_website,
                url="https://acme.com/contact",
                title="Official contact page",
                confidence=95,
                provider="website_parser",
            )
        )
        db.flush()
        services.verify_email(db, email.id)

        assert email.authenticity_level == "verified"
        assert email.pool == EmailPool.valid
        detail = services.email_authenticity_detail(db, email.id)
        assert detail["evidence_count"] == 1
        assert len(detail["verification_history"]) == 1


def test_website_parser_reverifies_existing_unknown_email_after_evidence(monkeypatch) -> None:
    parsed_result = SimpleNamespace(
        url="https://acme.com/contact",
        domain="acme.com",
        emails=[
            SimpleNamespace(
                address="info@acme.com",
                type="generic",
                source="mailto",
                confidence=95,
                url="https://acme.com/contact",
            )
        ],
        phones=[],
        social_links={},
        page_title="Contact",
        text_snippet="Contact us",
        content_hash="contact-page",
        error=None,
        elapsed_ms=10,
        pages_scanned=1,
        attempted_urls=["https://acme.com/contact"],
    )
    monkeypatch.setattr("app.modules.website_parser.parse_website", lambda *_args, **_kwargs: parsed_result)
    verified: list[str] = []

    def fake_verify(db, email_id):
        email = db.get(EmailAddress, email_id)
        evidence = db.scalar(
            select(SourceEvidence).where(
                SourceEvidence.entity_type == "email",
                SourceEvidence.entity_id == str(email.id),
                SourceEvidence.source_type == SourceType.official_website,
            )
        )
        assert evidence is not None
        email.status = EmailStatus.valid
        email.authenticity_level = "verified"
        email.pool = EmailPool.valid
        verified.append(email.address)
        return email

    monkeypatch.setattr(services, "verify_email", fake_verify)
    with _session() as db:
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        db.add(brand)
        db.flush()
        db.add(
            EmailAddress(
                brand_id=brand.id,
                address="info@acme.com",
                normalized_address="info@acme.com",
                domain="acme.com",
                type="generic",
                status=EmailStatus.unknown,
                authenticity_level="risky",
                pool=EmailPool.manual_review,
                provider="website_parser",
                last_verified_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
        )
        db.flush()

        services._parse_brand_website(db, None, brand)

        assert verified == ["info@acme.com"]


def test_contact_unknown_email_is_reverified(monkeypatch) -> None:
    verified: list[str] = []

    def fake_verify(db, email_id):
        email = db.get(EmailAddress, email_id)
        email.status = EmailStatus.valid
        verified.append(email.address)
        return email

    monkeypatch.setattr(services, "verify_email", fake_verify)
    with _session() as db:
        brand = Brand(name="Acme", normalized_name="acme", primary_website="https://acme.com")
        db.add(brand)
        db.flush()
        contact = services.create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Buyer"),
        )
        email = services.create_email(db, EmailCreate(contact_id=contact.id, address="jane@acme.com"))
        email.status = EmailStatus.unknown
        email.authenticity_level = "risky"
        email.pool = EmailPool.manual_review
        email.last_verified_at = datetime(2026, 7, 15, tzinfo=UTC)

        services._verify_unverified_emails(db, contact.id)

        assert verified == ["jane@acme.com"]


def test_email_list_can_filter_to_verified_pool() -> None:
    with _session() as db:
        verified = services.create_email(db, EmailCreate(address="verified@example.com"))
        verified.authenticity_level = "verified"
        verified.confidence_score = 90
        verified.pool = EmailPool.valid
        services.create_email(db, EmailCreate(address="unverified@example.com"))
        db.flush()

        result = services.list_emails(
            db,
            1,
            50,
            authenticity_level="verified",
            pool=EmailPool.valid,
            min_confidence=80,
        )

        assert result["total"] == 1
        assert result["items"][0]["address"] == "verified@example.com"
