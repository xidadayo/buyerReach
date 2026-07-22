import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class OwnershipMixin:
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)


class ExternalRefMixin:
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    raw_payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
