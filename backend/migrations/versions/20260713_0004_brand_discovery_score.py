"""add brand discovery score

Revision ID: 20260713_0004
Revises: 20260713_0003
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0004"
down_revision: str | None = "20260713_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("brand", sa.Column("discovery_score", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("brand", "discovery_score", server_default=None)


def downgrade() -> None:
    op.drop_column("brand", "discovery_score")
