from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.models import DomainEvent


def add_event(
    db: Session,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    candidate_id: str | None = None,
    schema_version: str = "1",
) -> DomainEvent:
    sequence = (
        db.scalar(
            select(func.max(DomainEvent.sequence)).where(
                DomainEvent.aggregate_type == aggregate_type,
                DomainEvent.aggregate_id == aggregate_id,
            )
        )
        or 0
    ) + 1
    event = DomainEvent(
        event_name=event_type,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        candidate_id=candidate_id,
        sequence=sequence,
        schema_version=schema_version,
        payload=payload,
    )
    db.add(event)
    return event


def mark_publish_failed(event: DomainEvent, error: str) -> None:
    event.publish_attempts += 1
    event.last_error = error[:2000]


def pending_events(db: Session, limit: int = 100) -> list[DomainEvent]:
    return list(
        db.scalars(
            select(DomainEvent)
            .where(DomainEvent.published_at.is_(None))
            .order_by(DomainEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
