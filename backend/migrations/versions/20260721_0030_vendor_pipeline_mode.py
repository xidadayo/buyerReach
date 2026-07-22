"""add vendor pipeline execution mode, selected vendors, vendor routes to TaskVendorPlan

Revision ID: 20260721_0030
Revises: 20260721_0029
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260721_0030"
down_revision = "20260721_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_vendor_plan",
        sa.Column(
            "execution_mode",
            sa.String(30),
            nullable=False,
            server_default="legacy_waterfall",
        ),
    )
    op.add_column(
        "task_vendor_plan",
        sa.Column(
            "selected_vendors",
            postgresql.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "task_vendor_plan",
        sa.Column(
            "pipeline_source",
            sa.String(30),
            nullable=False,
            server_default="legacy_global_strategy",
        ),
    )
    op.add_column(
        "task_vendor_plan",
        sa.Column(
            "vendor_routes",
            postgresql.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_vendor_plan", "vendor_routes")
    op.drop_column("task_vendor_plan", "pipeline_source")
    op.drop_column("task_vendor_plan", "selected_vendors")
    op.drop_column("task_vendor_plan", "execution_mode")
