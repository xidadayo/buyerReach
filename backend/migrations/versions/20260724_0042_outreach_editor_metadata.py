"""Add template personalization and campaign schedule snapshots.

Revision ID: 20260724_0042
Revises: 20260724_0041
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
revision = "20260724_0042"
down_revision: str | None = "20260724_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
def upgrade() -> None:
    op.add_column("email_template", sa.Column("variable_defaults", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("email_template", sa.Column("missing_variable_policy", sa.String(20), nullable=False, server_default="block"))
def downgrade() -> None:
    op.drop_column("email_template", "missing_variable_policy")
    op.drop_column("email_template", "variable_defaults")
