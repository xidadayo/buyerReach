from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.core.database import Base
from app.modules.models import Brand, CustomValue, EntityTag
from app.modules.schemas import (
    CustomFieldCreate,
    CustomFieldUpdate,
    TagCreate,
    TagUpdate,
)
from app.modules.services import (
    assign_entity_tag,
    create_custom_field,
    create_tag,
    delete_custom_field,
    delete_custom_value,
    delete_tag,
    list_custom_fields,
    list_entity_custom_values,
    list_entity_tags,
    list_tags,
    normalize_custom_value,
    remove_entity_tag,
    update_custom_field,
    update_tag,
    upsert_custom_value,
)


def database() -> tuple[object, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def brand(db: Session, name: str = "Acme") -> Brand:
    item = Brand(name=name, normalized_name=name.casefold())
    db.add(item)
    db.flush()
    return item


def test_tag_definition_and_entity_assignment_lifecycle() -> None:
    engine, db = database()
    try:
        item = brand(db)
        tag = create_tag(db, TagCreate(name="  Priority  ", module="brands"))
        other_module = create_tag(db, TagCreate(name="Buyer", module="contacts"))

        assert tag.name == "Priority"
        with pytest.raises(ValueError, match="already exists"):
            create_tag(db, TagCreate(name="Priority", module="brands"))

        assignment = assign_entity_tag(db, "brands", item.id, tag)
        repeated = assign_entity_tag(db, "brands", item.id, tag)
        assert repeated.id == assignment.id
        assert list_entity_tags(db, "brands", item.id)["items"][0]["name"] == "Priority"
        assert list_tags(db, "brands", 1, 100)["items"][0]["usage_count"] == 1

        with pytest.raises(ValueError, match="does not match"):
            assign_entity_tag(db, "brands", item.id, other_module)

        update_tag(db, tag, TagUpdate(name="Key Account"))
        assert tag.name == "Key Account"
        remove_entity_tag(db, "brands", item.id, tag)
        assert list_entity_tags(db, "brands", item.id)["total"] == 0
        with pytest.raises(LookupError, match="not found"):
            remove_entity_tag(db, "brands", item.id, tag)

        assign_entity_tag(db, "brands", item.id, tag)
        delete_tag(db, tag)
        db.flush()
        assert db.scalar(select(EntityTag).where(EntityTag.tag_id == tag.id)) is None
    finally:
        db.close()
        engine.dispose()


def test_custom_field_definition_and_value_lifecycle() -> None:
    engine, db = database()
    try:
        item = brand(db)
        field = create_custom_field(
            db,
            CustomFieldCreate(
                module="brands",
                name="Annual purchase volume",
                type="number",
                is_required=True,
            ),
        )
        optional = create_custom_field(
            db,
            CustomFieldCreate(module="brands", name="Notes", type="text"),
        )

        value = upsert_custom_value(db, "brands", item.id, field, 125000)
        assert value.value == 125000
        assert upsert_custom_value(db, "brands", item.id, field, 150000).id == value.id

        metadata = list_entity_custom_values(db, "brands", item.id)
        assert metadata["total"] == 2
        values_by_name = {entry["name"]: entry for entry in metadata["items"]}
        assert values_by_name["Annual purchase volume"]["value"] == 150000
        assert values_by_name["Notes"]["has_value"] is False
        assert list_custom_fields(db, "brands", 1, 100)["total"] == 2

        with pytest.raises(ValueError, match="must be numeric"):
            upsert_custom_value(db, "brands", item.id, field, "150000")
        with pytest.raises(ValueError, match="cannot be changed"):
            update_custom_field(db, field, CustomFieldUpdate(type="text"))
        with pytest.raises(ValueError, match="cannot be deleted"):
            delete_custom_value(db, "brands", item.id, field)

        optional_value = upsert_custom_value(db, "brands", item.id, optional, "  Follow up  ")
        assert optional_value.value == "Follow up"
        delete_custom_value(db, "brands", item.id, optional)
        db.flush()
        assert db.get(CustomValue, optional_value.id) is None

        delete_custom_field(db, field)
        db.flush()
        assert db.get(CustomValue, value.id) is None
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("field_type", "raw_value", "expected"),
    [
        ("date", "2026-07-14", "2026-07-14"),
        ("multi_select", ["Europe", "Europe", "Retail"], ["Europe", "Retail"]),
        ("boolean", True, True),
        ("url", "https://example.com/profile", "https://example.com/profile"),
        ("email", "Buyer@Example.COM", "Buyer@example.com"),
        ("phone", "+86 138-0013-8000", "+86 138-0013-8000"),
    ],
)
def test_custom_value_type_normalization(field_type: str, raw_value, expected) -> None:
    engine, db = database()
    try:
        field = create_custom_field(
            db,
            CustomFieldCreate(module="contacts", name=f"Field {field_type}", type=field_type),
        )
        assert normalize_custom_value(field, raw_value) == expected
    finally:
        db.close()
        engine.dispose()


def test_metadata_routes_are_registered() -> None:
    registered = {
        (method, route.path)
        for route in api_router.routes
        for method in (route.methods or set())
    }
    expected = {
        ("GET", "/tags/{tag_id}"),
        ("PATCH", "/tags/{tag_id}"),
        ("DELETE", "/tags/{tag_id}"),
        ("GET", "/custom-fields/{field_id}"),
        ("PATCH", "/custom-fields/{field_id}"),
        ("DELETE", "/custom-fields/{field_id}"),
        ("GET", "/entities/{entity_type}/{entity_id}/tags"),
        ("PUT", "/entities/{entity_type}/{entity_id}/tags/{tag_id}"),
        ("DELETE", "/entities/{entity_type}/{entity_id}/tags/{tag_id}"),
        ("GET", "/entities/{entity_type}/{entity_id}/custom-values"),
        ("PUT", "/entities/{entity_type}/{entity_id}/custom-values/{field_id}"),
        ("DELETE", "/entities/{entity_type}/{entity_id}/custom-values/{field_id}"),
    }
    assert expected <= registered


def test_missing_entity_is_rejected() -> None:
    engine, db = database()
    try:
        tag = create_tag(db, TagCreate(name="Priority", module="brands"))
        with pytest.raises(LookupError, match="Entity not found"):
            assign_entity_tag(
                db,
                "brands",
                UUID("00000000-0000-0000-0000-000000000001"),
                tag,
            )
    finally:
        db.close()
        engine.dispose()
