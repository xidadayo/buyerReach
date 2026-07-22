"""add vendor workflow credentials, strategy, plans, and checkpoints

Revision ID: 20260715_0019
Revises: 20260715_0018
Create Date: 2026-07-15
"""

from collections.abc import Sequence
from uuid import uuid4

from alembic import op
from sqlalchemy import select

from app.modules.models import ProviderConfig, TaskStageCheckpoint, TaskVendorPlan, VendorCredential, VendorStrategy
from app.shared.models import utc_now


revision: str = "20260715_0019"
down_revision: str | None = "20260715_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VENDORS = ("apollo", "hunter", "prospeo", "zerobounce")


def upgrade() -> None:
    bind = op.get_bind()
    VendorCredential.__table__.create(bind, checkfirst=True)
    VendorStrategy.__table__.create(bind, checkfirst=True)
    TaskVendorPlan.__table__.create(bind, checkfirst=True)
    TaskStageCheckpoint.__table__.create(bind, checkfirst=True)

    rows = bind.execute(
        select(
            ProviderConfig.provider,
            ProviderConfig.type,
            ProviderConfig.priority,
            ProviderConfig.enabled,
            ProviderConfig.config,
        ).order_by(ProviderConfig.enabled.desc(), ProviderConfig.priority.asc())
    ).mappings().all()
    now = utc_now()
    credentials: dict[str, dict] = {
        vendor: {"encrypted_api_key": "", "enabled": False} for vendor in VENDORS
    }
    for row in rows:
        config = row["config"] if isinstance(row["config"], dict) else {}
        vendor = str(config.get("adapter") or "").strip().lower()
        if vendor not in credentials:
            continue
        credentials[vendor]["enabled"] = credentials[vendor]["enabled"] or bool(row["enabled"])
        if not credentials[vendor]["encrypted_api_key"] and str(config.get("api_key") or "").strip():
            credentials[vendor]["encrypted_api_key"] = str(config["api_key"])

    for vendor, values in credentials.items():
        bind.execute(
            VendorCredential.__table__.insert().values(
                id=uuid4(),
                vendor=vendor,
                encrypted_api_key=values["encrypted_api_key"],
                enabled=values["enabled"],
                created_at=now,
                updated_at=now,
            )
        )

    company_vendors: list[str] = []
    for row in rows:
        config = row["config"] if isinstance(row["config"], dict) else {}
        vendor = str(config.get("adapter") or "").strip().lower()
        if row["enabled"] and row["type"] == "company_search" and vendor in VENDORS and vendor not in company_vendors:
            company_vendors.append(vendor)
    primary = company_vendors[0] if company_vendors else "apollo"
    verification = next(
        (
            str((row["config"] or {}).get("adapter") or "").strip().lower()
            for row in rows
            if row["enabled"] and row["type"] == "email_verifier"
        ),
        "zerobounce",
    )
    bind.execute(
        VendorStrategy.__table__.insert().values(
            id=uuid4(),
            name="default",
            primary_vendor=primary,
            fallback_vendors=[vendor for vendor in company_vendors if vendor != primary],
            verification_vendor=verification,
            adapter_version="v1",
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    TaskStageCheckpoint.__table__.drop(op.get_bind(), checkfirst=True)
    TaskVendorPlan.__table__.drop(op.get_bind(), checkfirst=True)
    VendorStrategy.__table__.drop(op.get_bind(), checkfirst=True)
    VendorCredential.__table__.drop(op.get_bind(), checkfirst=True)
