"""align batch tables with TimestampMixin ownership columns

Revision ID: 20260722_0034
Revises: 20260722_0033
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0034"
down_revision: str | None = "20260722_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("batch_import", sa.Column("updated_by", sa.Uuid(), nullable=True))
    op.add_column("exact_brand_target", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.add_column("exact_brand_target", sa.Column("updated_by", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("exact_brand_target", "updated_by")
    op.drop_column("exact_brand_target", "created_by")
    op.drop_column("batch_import", "updated_by")
