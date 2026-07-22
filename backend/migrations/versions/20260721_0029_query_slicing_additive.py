"""add query plan / slice / slice run / capacity lease tables and search_task additive fields

Revision ID: 20260721_0029
Revises: 20260720_0028
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260721_0029"
down_revision = "20260720_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── search_query_plan ────────────────────────────────────────────────
    op.create_table(
        "search_query_plan",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(),
            sa.ForeignKey("search_task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, default=1),
        sa.Column("schema_version", sa.String(40), nullable=False, default="1.0.0"),
        sa.Column("generator_type", sa.String(20), nullable=False, default="local_rules"),
        sa.Column("generator_version", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, default="draft"),
        sa.Column("target_result_count", sa.Integer(), nullable=False, default=100),
        sa.Column("candidate_fetch_limit", sa.Integer(), nullable=True),
        sa.Column("max_provider_calls", sa.Integer(), nullable=True),
        sa.Column("budget_limit", sa.Float(), nullable=True),
        sa.Column("repeat_mode", sa.String(40), nullable=False, default="new_only"),
        sa.Column("filter_policy", postgresql.JSON(), nullable=False, default=dict),
        sa.Column("source_policy", postgresql.JSON(), nullable=False, default=dict),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_search_query_plan_task_id", "search_query_plan", ["task_id"])
    op.create_index("ix_search_query_plan_org_id", "search_query_plan", ["organization_id"])
    op.create_index("ix_search_query_plan_task_status", "search_query_plan", ["task_id", "status"])
    op.create_unique_constraint("uq_search_query_plan_task_version", "search_query_plan", ["task_id", "version"])

    # ── search_query_slice ───────────────────────────────────────────────
    op.create_table(
        "search_query_slice",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("search_query_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slice_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False, default="core"),
        sa.Column("target_concept_ids", postgresql.JSON(), nullable=False, default=list),
        sa.Column("countries", postgresql.JSON(), nullable=False, default=list),
        sa.Column("target_concepts", postgresql.JSON(), nullable=False, default=list),
        sa.Column("business_types", postgresql.JSON(), nullable=False, default=list),
        sa.Column("include_terms", postgresql.JSON(), nullable=False, default=list),
        sa.Column("exclude_terms", postgresql.JSON(), nullable=False, default=list),
        sa.Column("match_mode", sa.String(10), nullable=False, default="any"),
        sa.Column("priority", sa.Integer(), nullable=False, default=0),
        sa.Column("enabled", sa.Boolean(), nullable=False, default=True),
        sa.Column("status", sa.String(20), nullable=False, default="draft"),
        sa.Column("origin", sa.String(20), nullable=False, default="generated"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("candidate_limit", sa.Integer(), nullable=True),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_search_query_slice_plan_id", "search_query_slice", ["plan_id"])
    op.create_index("ix_search_query_slice_plan_enabled", "search_query_slice", ["plan_id", "enabled"])
    op.create_unique_constraint("uq_search_query_slice_plan_key", "search_query_slice", ["plan_id", "slice_key"])
    op.create_unique_constraint("uq_search_query_slice_plan_hash", "search_query_slice", ["plan_id", "normalized_hash"])

    # ── search_query_slice_run ───────────────────────────────────────────
    op.create_table(
        "search_query_slice_run",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(),
            sa.ForeignKey("search_task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("search_query_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "query_slice_id",
            sa.String(64),
            sa.ForeignKey("search_query_slice.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("adapter_version", sa.String(80), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("cursor_key", sa.String(255), nullable=False),
        sa.Column("cursor", postgresql.JSON(), nullable=False, default=dict),
        sa.Column("status", sa.String(30), nullable=False, default="queued"),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, default=0),
        sa.Column("raw_count", sa.Integer(), nullable=False, default=0),
        sa.Column("new_count", sa.Integer(), nullable=False, default=0),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, default=0),
        sa.Column("filtered_count", sa.Integer(), nullable=False, default=0),
        sa.Column("qualified_count", sa.Integer(), nullable=False, default=0),
        sa.Column("review_count", sa.Integer(), nullable=False, default=0),
        sa.Column("call_count", sa.Integer(), nullable=False, default=0),
        sa.Column("cost", sa.Float(), nullable=False, default=0),
        sa.Column("consecutive_empty_pages", sa.Integer(), nullable=False, default=0),
        sa.Column("vendor_request_id", sa.String(255), nullable=True),
        sa.Column("normalized_output", postgresql.JSON(), nullable=False, default=dict),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_slice_run_task_id", "search_query_slice_run", ["task_id"])
    op.create_index("ix_slice_run_plan_id", "search_query_slice_run", ["plan_id"])
    op.create_index("ix_slice_run_query_slice_id", "search_query_slice_run", ["query_slice_id"])
    op.create_index("ix_slice_run_status", "search_query_slice_run", ["status"])
    op.create_index("ix_slice_run_status_lease", "search_query_slice_run", ["status", "lease_expires_at"])
    op.create_unique_constraint(
        "uq_slice_run_idempotency",
        "search_query_slice_run",
        ["task_id", "query_slice_id", "provider", "operation", "adapter_version", "input_hash", "cursor_key"],
    )

    # ── scheduler_capacity_lease ─────────────────────────────────────────
    op.create_table(
        "scheduler_capacity_lease",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_type", sa.String(40), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("holder_type", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.String(64), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False, default=1),
        sa.Column("lease_owner", sa.String(120), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_capacity_lease_scope_holder",
        "scheduler_capacity_lease",
        ["scope_type", "scope_key", "holder_type", "holder_id"],
    )
    op.create_index("ix_capacity_lease_scope", "scheduler_capacity_lease", ["scope_type", "scope_key"])
    op.create_index("ix_capacity_lease_owner", "scheduler_capacity_lease", ["lease_owner"])
    op.create_index("ix_capacity_lease_expires", "scheduler_capacity_lease", ["lease_expires_at"])

    # ── search_task additive fields ──────────────────────────────────────
    op.add_column("search_task", sa.Column("active_query_plan_id", sa.String(64), nullable=True))
    op.add_column("search_task", sa.Column("target_result_count", sa.Integer(), nullable=True))
    op.add_column("search_task", sa.Column("candidate_fetch_limit", sa.Integer(), nullable=True))
    op.add_column("search_task", sa.Column("max_provider_calls", sa.Integer(), nullable=True))
    op.add_column("search_task", sa.Column("repeat_mode", sa.String(40), nullable=True))
    op.add_column("search_task", sa.Column("queue_reason", sa.String(255), nullable=True))
    op.add_column("search_task", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_task", sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_task", sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_task", sa.Column("active_slice_count", sa.Integer(), nullable=True))
    op.add_column("search_task", sa.Column("waiting_slice_count", sa.Integer(), nullable=True))
    op.add_column("search_task", sa.Column("completed_slice_count", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_search_task_active_query_plan",
        "search_task",
        "search_query_plan",
        ["active_query_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── discovery_candidate_hit additive source-hit fields ───────────────
    op.add_column("discovery_candidate_hit", sa.Column("plan_id", sa.String(64), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("query_slice_id", sa.String(64), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("slice_run_id", sa.String(64), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("source_record_id", sa.String(255), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("source_url", sa.String(1000), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("source_edition", sa.String(40), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("discovery_candidate_hit", sa.Column("source_evidence", postgresql.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("discovery_candidate_hit", sa.Column("source_input_hash", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_discovery_candidate_hit_plan",
        "discovery_candidate_hit",
        "search_query_plan",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_discovery_candidate_hit_slice",
        "discovery_candidate_hit",
        "search_query_slice",
        ["query_slice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_discovery_candidate_hit_slice_run",
        "discovery_candidate_hit",
        "search_query_slice_run",
        ["slice_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # ── discovery_candidate_hit ──────────────────────────────────────────
    op.drop_constraint("fk_discovery_candidate_hit_slice_run", "discovery_candidate_hit", type_="foreignkey")
    op.drop_constraint("fk_discovery_candidate_hit_slice", "discovery_candidate_hit", type_="foreignkey")
    op.drop_constraint("fk_discovery_candidate_hit_plan", "discovery_candidate_hit", type_="foreignkey")
    op.drop_column("discovery_candidate_hit", "source_input_hash")
    op.drop_column("discovery_candidate_hit", "source_evidence")
    op.drop_column("discovery_candidate_hit", "observed_at")
    op.drop_column("discovery_candidate_hit", "source_edition")
    op.drop_column("discovery_candidate_hit", "source_url")
    op.drop_column("discovery_candidate_hit", "source_record_id")
    op.drop_column("discovery_candidate_hit", "slice_run_id")
    op.drop_column("discovery_candidate_hit", "query_slice_id")
    op.drop_column("discovery_candidate_hit", "plan_id")

    # ── search_task ──────────────────────────────────────────────────────
    op.drop_constraint("fk_search_task_active_query_plan", "search_task", type_="foreignkey")
    op.drop_column("search_task", "completed_slice_count")
    op.drop_column("search_task", "waiting_slice_count")
    op.drop_column("search_task", "active_slice_count")
    op.drop_column("search_task", "last_progress_at")
    op.drop_column("search_task", "admitted_at")
    op.drop_column("search_task", "queued_at")
    op.drop_column("search_task", "queue_reason")
    op.drop_column("search_task", "repeat_mode")
    op.drop_column("search_task", "max_provider_calls")
    op.drop_column("search_task", "candidate_fetch_limit")
    op.drop_column("search_task", "target_result_count")
    op.drop_column("search_task", "active_query_plan_id")

    # ── scheduler_capacity_lease ─────────────────────────────────────────
    op.drop_index("ix_capacity_lease_expires", table_name="scheduler_capacity_lease")
    op.drop_index("ix_capacity_lease_owner", table_name="scheduler_capacity_lease")
    op.drop_index("ix_capacity_lease_scope", table_name="scheduler_capacity_lease")
    op.drop_constraint("uq_capacity_lease_scope_holder", "scheduler_capacity_lease", type_="unique")
    op.drop_table("scheduler_capacity_lease")

    # ── search_query_slice_run ───────────────────────────────────────────
    op.drop_constraint("uq_slice_run_idempotency", "search_query_slice_run", type_="unique")
    op.drop_index("ix_slice_run_status_lease", table_name="search_query_slice_run")
    op.drop_index("ix_slice_run_status", table_name="search_query_slice_run")
    op.drop_index("ix_slice_run_query_slice_id", table_name="search_query_slice_run")
    op.drop_index("ix_slice_run_plan_id", table_name="search_query_slice_run")
    op.drop_index("ix_slice_run_task_id", table_name="search_query_slice_run")
    op.drop_table("search_query_slice_run")

    # ── search_query_slice ───────────────────────────────────────────────
    op.drop_constraint("uq_search_query_slice_plan_hash", "search_query_slice", type_="unique")
    op.drop_constraint("uq_search_query_slice_plan_key", "search_query_slice", type_="unique")
    op.drop_index("ix_search_query_slice_plan_enabled", table_name="search_query_slice")
    op.drop_index("ix_search_query_slice_plan_id", table_name="search_query_slice")
    op.drop_table("search_query_slice")

    # ── search_query_plan ────────────────────────────────────────────────
    op.drop_constraint("uq_search_query_plan_task_version", "search_query_plan", type_="unique")
    op.drop_index("ix_search_query_plan_task_status", table_name="search_query_plan")
    op.drop_index("ix_search_query_plan_org_id", table_name="search_query_plan")
    op.drop_index("ix_search_query_plan_task_id", table_name="search_query_plan")
    op.drop_table("search_query_plan")
