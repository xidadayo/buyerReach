"""add local email verifier rollout settings

Revision ID: 20260717_0023
Revises: 20260716_0022
Create Date: 2026-07-17
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260717_0023"
down_revision: str | None = "20260716_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("vendor_strategy", "task_vendor_plan"):
        op.add_column(table, sa.Column("local_verification_mode", sa.String(20), nullable=False, server_default="disabled"))
        op.add_column(table, sa.Column("local_verification_rollout", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("local_verification_sample", sa.Integer(), nullable=False, server_default="10"))
    op.execute(
        """
        INSERT INTO vendor_credential (id, vendor, encrypted_api_key, enabled, created_at, updated_at)
        SELECT gen_random_uuid(), 'aftership_local', '', false, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM vendor_credential WHERE vendor = 'aftership_local')
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM vendor_credential WHERE vendor = 'aftership_local' AND encrypted_api_key = '' AND enabled = false")
    for table in ("task_vendor_plan", "vendor_strategy"):
        op.drop_column(table, "local_verification_sample")
        op.drop_column(table, "local_verification_rollout")
        op.drop_column(table, "local_verification_mode")
