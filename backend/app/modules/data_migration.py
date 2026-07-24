"""Resumable staging and conservative application of legacy customer data."""

import re
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.models import (
    Brand,
    Contact,
    DataImportBatch,
    DataImportRow,
    EmailAddress,
    AuditLog,
)
from app.shared.models import utc_now


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _email(value: object) -> str:
    return _norm(value)


def stage(db: Session, payload, actor) -> DataImportBatch:
    existing = db.scalar(
        select(DataImportBatch).where(DataImportBatch.file_hash == payload.source_fingerprint)
    )
    if existing:
        return existing
    batch = DataImportBatch(
        source_type=payload.source_type,
        file_hash=payload.source_fingerprint,
        organization_id=actor.organization_id,
        organization_unit_id=actor.organization_unit_id,
        status="previewed",
        summary={"total": len(payload.rows)},
        rollback_until=utc_now() + timedelta(days=30),
        created_by=actor.id,
    )
    db.add(batch)
    db.flush()
    conflicts = 0
    for number, raw in enumerate(payload.rows, 1):
        kind = _norm(raw.get("entity_type"))
        if kind not in {"brand", "contact", "email"}:
            kind = "invalid"
        normalized = {
            "name": _norm(raw.get("name")),
            "email": _email(raw.get("email")),
            "brand": _norm(raw.get("brand")),
        }
        match = None
        if kind == "email" and normalized["email"]:
            match = db.scalar(
                select(EmailAddress).where(
                    EmailAddress.organization_id == actor.organization_id,
                    EmailAddress.normalized_address == normalized["email"],
                )
            )
        elif kind == "brand" and normalized["name"]:
            match = db.scalar(
                select(Brand).where(
                    Brand.organization_id == actor.organization_id,
                    Brand.normalized_name == normalized["name"],
                )
            )
        elif kind == "contact" and normalized["name"]:
            match = db.scalar(
                select(Contact).where(
                    Contact.organization_id == actor.organization_id,
                    Contact.full_name == raw.get("name", "").strip(),
                )
            )
        status = "conflict" if match else ("invalid" if kind == "invalid" else "ready")
        conflicts += int(status == "conflict")
        db.add(
            DataImportRow(
                batch_id=batch.id,
                row_number=number,
                entity_type=kind,
                raw_data=raw,
                normalized_data=normalized,
                status=status,
                match_entity_id=getattr(match, "id", None),
                conflict={"reason": "existing_entity"} if match else {},
                created_by=actor.id,
            )
        )
    batch.summary = {
        "total": len(payload.rows),
        "conflicts": conflicts,
        "ready": len(payload.rows) - conflicts,
    }
    db.add(
        AuditLog(
            actor_id=str(actor.id),
            action="data_migration.stage",
            entity_type="data_import_batch",
            entity_id=str(batch.id),
            after=batch.summary,
            organization_id=actor.organization_id,
            organization_unit_id=actor.organization_unit_id,
        )
    )
    return batch


def apply(db: Session, batch: DataImportBatch, resolutions: dict[str, str], actor) -> dict:
    if batch.status in {"applied", "rolled_back"}:
        raise ValueError("Batch cannot be applied in its current state")
    applied = merged = skipped = 0
    rows = db.scalars(
        select(DataImportRow)
        .where(DataImportRow.batch_id == batch.id)
        .order_by(DataImportRow.row_number)
    ).all()
    for row in rows:
        action = resolutions.get(str(row.id), "merge" if row.status == "conflict" else "create")
        if row.status == "invalid" or action == "skip":
            row.status = "skipped"
            skipped += 1
            continue
        if row.status == "conflict":
            if action != "merge":
                raise ValueError(f"Conflict row {row.row_number} needs an explicit resolution")
            row.status = "merged"
            row.applied_entity_id = row.match_entity_id
            merged += 1
            continue
        raw, norm = row.raw_data, row.normalized_data
        if row.entity_type == "brand":
            entity = Brand(
                name=str(raw.get("name")).strip(),
                normalized_name=norm["name"],
                primary_website=raw.get("website"),
                organization_id=batch.organization_id,
                department_id=batch.organization_unit_id,
                owner_id=actor.id,
            )
        elif row.entity_type == "contact":
            name = str(raw.get("name")).strip()
            first, _, last = name.partition(" ")
            entity = Contact(
                first_name=first or "Unknown",
                last_name=last,
                full_name=name,
                organization_id=batch.organization_id,
                department_id=batch.organization_unit_id,
                owner_id=actor.id,
            )
        elif row.entity_type == "email":
            address = norm["email"]
            if "@" not in address:
                row.status = "invalid"
                skipped += 1
                continue
            entity = EmailAddress(
                address=address,
                normalized_address=address,
                domain=address.rsplit("@", 1)[1],
                organization_id=batch.organization_id,
                department_id=batch.organization_unit_id,
                owner_id=actor.id,
            )
        else:
            row.status = "skipped"
            skipped += 1
            continue
        db.add(entity)
        db.flush()
        row.applied_entity_id = entity.id
        row.status = "applied"
        applied += 1
    batch.status = "applied"
    batch.summary = {**batch.summary, "applied": applied, "merged": merged, "skipped": skipped}
    db.add(
        AuditLog(
            actor_id=str(actor.id),
            action="data_migration.apply",
            entity_type="data_import_batch",
            entity_id=str(batch.id),
            after=batch.summary,
            organization_id=actor.organization_id,
            organization_unit_id=actor.organization_unit_id,
        )
    )
    return batch.summary
