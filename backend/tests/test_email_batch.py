from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import Brand, Contact, ContactPosition, EmailAddress, Website
from app.modules.schemas import ContactCreate, EmailCreate
from app.modules.services import (
    bulk_archive_contacts,
    bulk_archive_emails,
    bulk_archive_brands,
    create_contact,
    create_email,
    dashboard,
    export_selected_contacts_csv,
    export_selected_emails_csv,
    list_brands,
    list_brand_hierarchy,
    refresh_contact_status,
    list_emails,
)
from app.shared.enums import EmailPool
from app.shared.models import utc_now


def test_bulk_archive_and_selected_email_export() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = create_email(db, EmailCreate(address="first@example.com"))
        second = create_email(db, EmailCreate(address="second@example.com"))
        untouched = create_email(db, EmailCreate(address="untouched@example.com"))
        db.flush()

        content, count = export_selected_emails_csv(db, [first.id, second.id])
        assert count == 2
        assert "first@example.com" in content.decode("utf-8-sig")
        assert "second@example.com" in content.decode("utf-8-sig")
        assert "untouched@example.com" not in content.decode("utf-8-sig")

        result = bulk_archive_emails(db, [first.id, second.id, first.id])
        assert result == {"requested": 2, "archived": 2, "skipped": 0}
        assert db.get(EmailAddress, first.id).deleted_at is not None
        assert db.get(EmailAddress, second.id).deleted_at is not None
        assert db.scalar(select(EmailAddress.deleted_at).where(EmailAddress.id == untouched.id)) is None


def test_brand_batch_archive() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = Brand(name="First", normalized_name="first")
        second = Brand(name="Second", normalized_name="second")
        untouched = Brand(name="Untouched", normalized_name="untouched")
        db.add_all([first, second, untouched])
        db.flush()

        result = bulk_archive_brands(db, [first.id, second.id, first.id])
        assert result == {
            "requested": 2,
            "archived": 2,
            "contacts_archived": 0,
            "contacts_preserved": 0,
            "emails_archived": 0,
            "positions_archived": 0,
            "websites_archived": 0,
            "skipped": 0,
        }
        assert db.get(Brand, first.id).deleted_at is not None
        assert db.get(Brand, second.id).deleted_at is not None
        assert db.get(Brand, untouched.id).deleted_at is None


def test_brand_archive_cascades_exclusive_contacts_emails_positions_and_websites() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        selected = Brand(name="Selected", normalized_name="selected")
        other = Brand(name="Other", normalized_name="other")
        db.add_all([selected, other])
        db.flush()

        exclusive = create_contact(
            db,
            ContactCreate(brand_id=selected.id, first_name="Exclusive", last_name="Buyer", title="Buyer"),
        )
        shared = create_contact(
            db,
            ContactCreate(brand_id=selected.id, first_name="Shared", last_name="Buyer", title="Buyer"),
        )
        shared_other_position = ContactPosition(
            contact_id=shared.id,
            brand_id=other.id,
            title="Advisor",
            is_current=True,
        )
        db.add(shared_other_position)
        exclusive_email = create_email(
            db,
            EmailCreate(contact_id=exclusive.id, brand_id=selected.id, address="exclusive@selected.com"),
        )
        shared_selected_email = create_email(
            db,
            EmailCreate(contact_id=shared.id, brand_id=selected.id, address="shared@selected.com"),
        )
        other_email = create_email(
            db,
            EmailCreate(contact_id=shared.id, brand_id=other.id, address="shared@other.com"),
        )
        direct_email = create_email(
            db,
            EmailCreate(brand_id=selected.id, address="info@selected.com", type="generic"),
        )
        website = Website(
            brand_id=selected.id,
            domain="selected.com",
            url="https://selected.com",
            is_primary=True,
        )
        db.add(website)
        db.flush()

        result = bulk_archive_brands(db, [selected.id])

        assert result == {
            "requested": 1,
            "archived": 1,
            "contacts_archived": 1,
            "contacts_preserved": 1,
            "emails_archived": 3,
            "positions_archived": 2,
            "websites_archived": 1,
            "skipped": 0,
        }
        assert selected.deleted_at is not None
        assert other.deleted_at is None
        assert db.get(Contact, exclusive.id).deleted_at is not None
        assert db.get(Contact, shared.id).deleted_at is None
        assert exclusive_email.deleted_at is not None
        assert shared_selected_email.deleted_at is not None
        assert direct_email.deleted_at is not None
        assert other_email.deleted_at is None
        assert website.deleted_at is not None
        assert shared_other_position.deleted_at is None
        selected_positions = list(
            db.scalars(select(ContactPosition).where(ContactPosition.brand_id == selected.id))
        )
        assert selected_positions and all(position.deleted_at is not None for position in selected_positions)


def test_brand_hierarchy_groups_contacts_and_emails_by_brand() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        brand = Brand(name="Acme", normalized_name="acme")
        db.add(brand)
        db.flush()
        contact = create_contact(db, ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Buyer"))
        contact_email = create_email(db, EmailCreate(contact_id=contact.id, address="jane@acme.com"))
        contact_email.authenticity_level = "verified"
        contact_email.pool = EmailPool.valid
        refresh_contact_status(db, contact.id)
        generic_email = create_email(db, EmailCreate(brand_id=brand.id, address="sales@acme.com", type="generic"))
        db.flush()

        hierarchy = list_brand_hierarchy(db, 1, 50)
        item = hierarchy["items"][0]
        assert item["contact_count"] == 1
        assert item["discovered_contact_count"] == 1
        assert item["invalid_contact_count"] == 0
        assert item["email_count"] == 2
        assert item["contacts"][0]["emails"][0]["id"] == str(contact_email.id)
        assert item["brand_emails"][0]["id"] == str(generic_email.id)


def test_brand_list_reports_active_related_email_count() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        brand = Brand(name="Acme", normalized_name="acme")
        other = Brand(name="Other", normalized_name="other")
        db.add_all([brand, other])
        db.flush()
        contact = create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Buyer"),
        )
        create_email(db, EmailCreate(contact_id=contact.id, address="jane@acme.com"))
        create_email(db, EmailCreate(brand_id=brand.id, address="sales@acme.com", type="generic"))
        archived = create_email(
            db,
            EmailCreate(brand_id=brand.id, address="old@acme.com", type="generic"),
        )
        archived.deleted_at = utc_now()
        create_email(db, EmailCreate(brand_id=other.id, address="sales@other.com", type="generic"))
        db.flush()

        result = list_brands(db, 1, 50)
        items_by_name = {item["name"]: item for item in result["items"]}

        assert items_by_name["Acme"]["email_count"] == 2
        assert items_by_name["Other"]["email_count"] == 1


def test_email_list_filters_by_direct_and_current_brand_relationships() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        brand = Brand(name="Acme", normalized_name="acme")
        other = Brand(name="Other", normalized_name="other")
        db.add_all([brand, other])
        db.flush()
        contact = create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Jane", last_name="Buyer", title="Buyer"),
        )
        related_contact_email = create_email(
            db,
            EmailCreate(contact_id=contact.id, address="jane@acme.com"),
        )
        related_direct_email = create_email(
            db,
            EmailCreate(brand_id=brand.id, address="sales@acme.com", type="generic"),
        )
        create_email(
            db,
            EmailCreate(brand_id=other.id, address="sales@other.com", type="generic"),
        )
        db.flush()

        result = list_emails(db, 1, 50, brand_id=brand.id)

        assert result["total"] == 2
        assert {item["id"] for item in result["items"]} == {
            str(related_contact_email.id),
            str(related_direct_email.id),
        }


def test_contact_without_verified_email_is_not_counted_as_valid() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        brand = Brand(name="Acme", normalized_name="acme")
        db.add(brand)
        db.flush()
        contact = create_contact(
            db,
            ContactCreate(brand_id=brand.id, first_name="Name", last_name="Only", title="Buyer"),
        )
        db.flush()

        hierarchy = list_brand_hierarchy(db, 1, 50)
        item = hierarchy["items"][0]
        assert contact.status == "invalid"
        assert item["contact_count"] == 0
        assert item["valid_contact_count"] == 0
        assert item["discovered_contact_count"] == 1
        assert item["invalid_contact_count"] == 1
        assert item["contacts"][0]["is_valid"] is False
        metrics = dashboard(db)["metrics"]
        assert metrics["valid_contacts"] == 0
        assert metrics["discovered_contacts"] == 1


def test_contact_batch_export_includes_emails_and_archiving_cascades() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        selected = create_contact(db, ContactCreate(first_name="Jane", last_name="Buyer", title="Buyer"))
        untouched = create_contact(db, ContactCreate(first_name="John", last_name="Buyer", title="Buyer"))
        selected_email = create_email(db, EmailCreate(contact_id=selected.id, address="jane@example.com"))
        untouched_email = create_email(db, EmailCreate(contact_id=untouched.id, address="john@example.com"))
        db.flush()

        content, count = export_selected_contacts_csv(db, [selected.id])
        csv = content.decode("utf-8-sig")
        assert count == 1
        assert "Jane Buyer" in csv
        assert "jane@example.com" in csv
        assert "john@example.com" not in csv

        result = bulk_archive_contacts(db, [selected.id])
        assert result["contacts_archived"] == 1
        assert result["emails_archived"] == 1
        assert db.get(EmailAddress, selected_email.id).deleted_at is not None
        assert db.get(EmailAddress, untouched_email.id).deleted_at is None


def test_archiving_last_email_refreshes_contact_with_autoflush_disabled() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, autoflush=False) as db:
        contact = create_contact(db, ContactCreate(first_name="Jane", last_name="Buyer", title="Buyer"))
        email = create_email(db, EmailCreate(contact_id=contact.id, address="jane@example.com"))
        email.authenticity_level = "verified"
        email.pool = EmailPool.valid
        db.flush()
        refresh_contact_status(db, contact.id)
        assert contact.status == "valid"

        bulk_archive_emails(db, [email.id])

        assert contact.status == "invalid"
