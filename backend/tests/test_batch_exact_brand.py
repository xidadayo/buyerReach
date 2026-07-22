from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.batch_exact_brand import (
    EXECUTION_FAILED,
    EXECUTION_NO_MATCH,
    build_preview,
    confirm_batch_import,
    create_batch_import,
    get_targets_for_task,
    retry_targets,
)
from app.modules.schemas import BatchImportConfirm


def database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def sample_preview() -> dict:
    content = (
        "company_name,official_domain,country,external_id,notes\n"
        "Acme,https://www.acme.com/about,US,A-1,priority\n"
        "Duplicate,ACME.COM,US,A-2,duplicate\n"
        "Formula,=cmd|'/C calc'!A0,US,A-3,bad\n"
    ).encode()
    return build_preview("companies.csv", content)


def test_preview_normalizes_domain_and_rejects_duplicates_and_formulas() -> None:
    preview = sample_preview()
    assert preview["total_rows"] == 3
    assert preview["valid_rows"] == 1
    assert preview["duplicate_rows"] == 1
    assert preview["invalid_rows"] == 2
    assert preview["rows"][0]["normalized_domain"] == "acme.com"
    assert "DUPLICATE_DOMAIN" in preview["rows"][1]["validation_errors"]
    assert "FORMULA_INJECTION" in preview["rows"][2]["validation_errors"]


def test_confirmation_is_idempotent_and_retry_excludes_no_match() -> None:
    preview = sample_preview()
    config = BatchImportConfirm(
        name="Imported companies",
        selected_vendors=["apollo", "hunter"],
        target_titles=["Head of Buying", "Sourcing Manager"],
        retry_limit_per_target=2,
    )
    organization_id = uuid4()
    user_id = uuid4()

    with database() as db:
        batch = create_batch_import(
            db,
            filename=preview["filename"],
            file_hash=preview["file_hash"],
            parsed_rows=preview["rows"],
            organization_id=organization_id,
            created_by=user_id,
        )
        first = confirm_batch_import(
            db,
            batch_id=batch.id,
            config=config,
            organization_id=organization_id,
            user_id=user_id,
        )
        second = confirm_batch_import(
            db,
            batch_id=batch.id,
            config=config,
            organization_id=organization_id,
            user_id=user_id,
        )

        assert second["already_confirmed"] is True
        assert second["parent_task"].id == first["parent_task"].id
        assert len(second["targets"]) == 1
        target = second["targets"][0]
        assert target.max_attempts == 2

        target.execution_status = EXECUTION_NO_MATCH
        assert retry_targets(db, task_id=first["parent_task"].id, target_ids=[target.id]) == 0

        target.execution_status = EXECUTION_FAILED
        target.execution_attempts = 1
        assert retry_targets(db, task_id=first["parent_task"].id, target_ids=[target.id]) == 1
        assert target.execution_status == "queued"

        listing = get_targets_for_task(db, first["parent_task"].id)
        assert listing["summary"]["total"] == 1
        assert listing["summary"]["pending"] == 1


def test_same_file_is_idempotent_per_organization() -> None:
    preview = sample_preview()
    organization_id = uuid4()
    with database() as db:
        first = create_batch_import(
            db,
            filename="first.csv",
            file_hash=preview["file_hash"],
            parsed_rows=preview["rows"],
            organization_id=organization_id,
        )
        second = create_batch_import(
            db,
            filename="second.csv",
            file_hash=preview["file_hash"],
            parsed_rows=preview["rows"],
            organization_id=organization_id,
        )
        assert second.id == first.id
