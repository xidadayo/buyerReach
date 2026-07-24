"""Add revocable data-share grants without changing data ownership.

Revision ID: 20260724_0040
Revises: 20260724_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260724_0040"
down_revision: str | None = "20260724_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_share_grant",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("resource", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("source_unit_id", sa.Uuid(), sa.ForeignKey("organization_unit.id"), nullable=True),
        sa.Column("target_unit_id", sa.Uuid(), sa.ForeignKey("organization_unit.id"), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False, server_default="read"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), sa.ForeignKey("user.id"), nullable=True),
    )
    op.create_index("ix_data_share_grant_resource", "data_share_grant", ["resource"])
    op.create_index("ix_data_share_grant_entity", "data_share_grant", ["entity_id"])
    op.create_index("ix_data_share_grant_org", "data_share_grant", ["organization_id"])
    op.create_index("ix_data_share_grant_source_unit", "data_share_grant", ["source_unit_id"])
    op.create_index("ix_data_share_grant_target_unit", "data_share_grant", ["target_unit_id"])
    op.create_index("ix_share_grant_active_lookup", "data_share_grant", ["resource", "entity_id", "target_unit_id", "revoked_at"])


def downgrade() -> None:
    op.drop_table("data_share_grant")
