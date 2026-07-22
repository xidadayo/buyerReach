"""expand versioned pipeline, durable outbox and score history

Revision ID: 20260717_0025
Revises: 20260717_0024
Create Date: 2026-07-17
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260717_0025"
down_revision: str | None = "20260717_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_task", sa.Column("configuration_version", sa.String(40), nullable=True))
    op.add_column("search_task", sa.Column("configuration_snapshot", sa.JSON(), nullable=True))
    op.add_column("search_task", sa.Column("pipeline_version", sa.String(40), nullable=True))
    op.add_column("search_task", sa.Column("trace_id", sa.String(64), nullable=True))
    op.create_index("ix_search_task_trace_id", "search_task", ["trace_id"])
    for column in (
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=True),
        sa.Column("aggregate_type", sa.String(80), nullable=True),
        sa.Column("aggregate_id", sa.String(64), nullable=True),
        sa.Column("candidate_id", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(20), nullable=True, server_default="1"),
        sa.Column("publish_attempts", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
    ):
        op.add_column("domain_event", column)
    for name in ("sequence", "event_type", "aggregate_type", "aggregate_id", "candidate_id"):
        op.create_index(f"ix_domain_event_{name}", "domain_event", [name])
    # Historical DomainEvent rows predate the Outbox publisher and must never
    # be replayed as newly pending notifications during this expand migration.
    op.execute(
        "UPDATE domain_event SET published_at = created_at "
        "WHERE event_type IS NULL AND published_at IS NULL"
    )
    op.create_table(
        "pipeline_stage_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("search_task.id"), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), sa.ForeignKey("discovery_candidate.id")),
        sa.Column("stage_name", sa.String(80), nullable=False),
        sa.Column("stage_version", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON()),
        sa.Column("output_payload", sa.JSON()),
        sa.Column("vendor_request_id", sa.String(120)),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_pipeline_stage_run_idempotency_key",
        "pipeline_stage_run",
        ["idempotency_key"],
        unique=True,
    )
    op.create_table(
        "relevance_score_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("search_task.id")),
        sa.Column(
            "candidate_id", sa.Uuid(), sa.ForeignKey("discovery_candidate.id"), nullable=False
        ),
        sa.Column("batch_id", sa.String(64)),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("ai_dimension_result", sa.JSON()),
        sa.Column("evidence_snapshot", sa.JSON()),
        sa.Column("score", sa.Integer()),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("prompt_version", sa.String(40)),
        sa.Column("adapter_version", sa.String(40)),
        sa.Column("scoring_policy_version", sa.String(40), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "recompute_batch",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("budget_limit", sa.Float()),
        sa.Column("cost_spent", sa.Float(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(40)),
        sa.Column("adapter_version", sa.String(40)),
        sa.Column("comparison", sa.JSON()),
    )


def downgrade() -> None:
    op.drop_table("recompute_batch")
    op.drop_table("relevance_score_history")
    op.drop_table("pipeline_stage_run")
    for name in ("candidate_id", "aggregate_id", "aggregate_type", "event_type", "sequence"):
        op.drop_index(f"ix_domain_event_{name}", table_name="domain_event")
    for name in (
        "last_error",
        "publish_attempts",
        "schema_version",
        "candidate_id",
        "aggregate_id",
        "aggregate_type",
        "event_type",
        "sequence",
    ):
        op.drop_column("domain_event", name)
    op.drop_index("ix_search_task_trace_id", table_name="search_task")
    for name in ("trace_id", "pipeline_version", "configuration_snapshot", "configuration_version"):
        op.drop_column("search_task", name)
