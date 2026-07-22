"""add traceable candidate industry enrichment fields

Revision ID: 20260716_0022
Revises: 20260716_0021
Create Date: 2026-07-16
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0022"
down_revision: str | None = "20260716_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("discovery_candidate", sa.Column("industry_source", sa.String(80), nullable=True))
    op.add_column("discovery_candidate", sa.Column("industry_confidence", sa.Integer(), nullable=True))
    op.add_column("discovery_candidate", sa.Column("industry_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("discovery_candidate", sa.Column("industry_enrichment_status", sa.String(40), nullable=True))
    op.add_column("discovery_candidate", sa.Column("industry_enrichment_error", sa.Text(), nullable=True))
    op.add_column("discovery_candidate", sa.Column("industry_enriched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("industry_enriched_at", "industry_enrichment_error", "industry_enrichment_status", "industry_details", "industry_confidence", "industry_source"):
        op.drop_column("discovery_candidate", name)
