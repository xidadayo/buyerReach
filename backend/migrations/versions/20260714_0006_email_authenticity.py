"""add evidence-based email authenticity fields

Revision ID: 20260714_0006
Revises: 20260713_0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0006"
down_revision: str | None = "20260713_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("email_address", "email_verification"):
        op.add_column(table_name, sa.Column("deliverability_score", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table_name, sa.Column("identity_score", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table_name, sa.Column("evidence_score", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table_name, sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table_name, sa.Column("authenticity_level", sa.String(length=40), nullable=False, server_default="unverified"))
        op.add_column(table_name, sa.Column("is_catch_all", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(table_name, sa.Column("is_disposable", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(table_name, sa.Column("domain_matches_brand", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("email_address", sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_address", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_address", sa.Column("verification_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_email_address_authenticity_level", "email_address", ["authenticity_level"])


def downgrade() -> None:
    op.drop_index("ix_email_address_authenticity_level", table_name="email_address")
    op.drop_column("email_address", "verification_summary")
    op.drop_column("email_address", "last_verified_at")
    op.drop_column("email_address", "evidence_count")
    for table_name in ("email_verification", "email_address"):
        op.drop_column(table_name, "domain_matches_brand")
        op.drop_column(table_name, "is_disposable")
        op.drop_column(table_name, "is_catch_all")
        op.drop_column(table_name, "authenticity_level")
        op.drop_column(table_name, "confidence_score")
        op.drop_column(table_name, "evidence_score")
        op.drop_column(table_name, "identity_score")
        op.drop_column(table_name, "deliverability_score")
