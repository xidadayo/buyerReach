"""add login lockout fields

Revision ID: 20260713_0003
Revises: 20260713_0002
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0003"
down_revision: str | None = "20260713_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("user", "failed_login_attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("user", "locked_until")
    op.drop_column("user", "failed_login_attempts")
