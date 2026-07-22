"""add task and vendor provenance to source evidence

Revision ID: 20260722_0031
Revises: 20260721_0030
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_0031"
down_revision: str | None = "20260721_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_evidence", sa.Column("task_id", sa.Uuid(), nullable=True))
    op.add_column("source_evidence", sa.Column("stage_run_id", sa.Uuid(), nullable=True))
    op.add_column("source_evidence", sa.Column("vendor_request_id", sa.String(255), nullable=True))
    op.add_column("source_evidence", sa.Column("provider_record_id", sa.String(255), nullable=True))
    op.add_column("source_evidence", sa.Column("adapter_version", sa.String(40), nullable=True))
    op.add_column("source_evidence", sa.Column("input_hash", sa.String(64), nullable=True))
    op.add_column("source_evidence", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_evidence", sa.Column("normalized_evidence", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_source_evidence_task_id", "source_evidence", "search_task", ["task_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_source_evidence_stage_run_id",
        "source_evidence",
        "pipeline_stage_run",
        ["stage_run_id"],
        ["id"],
    )
    op.create_index("ix_source_evidence_task_id", "source_evidence", ["task_id"])
    op.create_index("ix_source_evidence_stage_run_id", "source_evidence", ["stage_run_id"])
    op.create_index(
        "ix_source_evidence_vendor_record",
        "source_evidence",
        ["provider", "provider_record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_evidence_vendor_record", table_name="source_evidence")
    op.drop_index("ix_source_evidence_stage_run_id", table_name="source_evidence")
    op.drop_index("ix_source_evidence_task_id", table_name="source_evidence")
    op.drop_constraint("fk_source_evidence_stage_run_id", "source_evidence", type_="foreignkey")
    op.drop_constraint("fk_source_evidence_task_id", "source_evidence", type_="foreignkey")
    for column in (
        "normalized_evidence",
        "observed_at",
        "input_hash",
        "adapter_version",
        "provider_record_id",
        "vendor_request_id",
        "stage_run_id",
        "task_id",
    ):
        op.drop_column("source_evidence", column)
