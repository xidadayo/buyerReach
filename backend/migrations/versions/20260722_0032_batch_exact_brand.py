"""add BatchImport and ExactBrandTarget tables

Revision ID: 20260722_0032
Revises: 20260722_0031
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_0032"
down_revision: str | None = "20260722_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── batch_import ──────────────────────────────────────────────────────
    op.create_table(
        "batch_import",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("template_version", sa.String(40), nullable=False, server_default="exact-brand-import-v1"),
        sa.Column("file_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="uploaded"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.JSON(), nullable=True),
        sa.Column("parsed_preview", sa.JSON(), nullable=True),
        sa.Column("parent_task_id", sa.Uuid(), sa.ForeignKey("search_task.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batch_import_org", "batch_import", ["organization_id"])
    op.create_index("ix_batch_import_status", "batch_import", ["status"])
    op.create_index("ix_batch_import_parent_task", "batch_import", ["parent_task_id"])

    # ── exact_brand_target ────────────────────────────────────────────────
    op.create_table(
        "exact_brand_target",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_import_id", sa.Uuid(), sa.ForeignKey("batch_import.id"), nullable=False),
        sa.Column("search_task_id", sa.Uuid(), sa.ForeignKey("search_task.id"), nullable=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("company_name", sa.String(500), nullable=False),
        sa.Column("normalized_company_name", sa.String(500), nullable=False),
        sa.Column("official_domain", sa.String(500), nullable=False),
        sa.Column("normalized_domain", sa.String(255), nullable=False, index=True),
        sa.Column("country", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("raw_input", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("execution_status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(80), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("brand.id"), nullable=True),
        sa.Column("contact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reliable_email_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_email_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vendor_results", sa.JSON(), nullable=True),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_import_id", "row_number", name="uq_target_batch_row"),
        sa.UniqueConstraint("batch_import_id", "normalized_domain", name="uq_target_batch_domain"),
    )
    op.create_index("ix_target_execution_status", "exact_brand_target", ["execution_status"])
    op.create_index("ix_target_search_task", "exact_brand_target", ["search_task_id"])
    op.create_index("ix_target_org", "exact_brand_target", ["organization_id"])
    op.create_index("ix_target_lease_owner", "exact_brand_target", ["lease_owner"])
    op.create_index("ix_target_lease_expires", "exact_brand_target", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_table("exact_brand_target")
    op.drop_index("ix_batch_import_parent_task", table_name="batch_import")
    op.drop_index("ix_batch_import_status", table_name="batch_import")
    op.drop_index("ix_batch_import_org", table_name="batch_import")
    op.drop_table("batch_import")
