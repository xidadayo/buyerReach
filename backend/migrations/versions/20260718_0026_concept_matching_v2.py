"""concept scope matching and relevance policy 2.0

Revision ID: 20260718_0026
Revises: 20260717_0025
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_0026"
down_revision = "20260717_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("search_intent", sa.Column("search_intent", sa.JSON(), nullable=True)),
        ("intent_schema_version", sa.Column("intent_schema_version", sa.String(40), nullable=True)),
        ("intent_prompt_version", sa.Column("intent_prompt_version", sa.String(40), nullable=True)),
        ("knowledge_snapshot", sa.Column("knowledge_snapshot", sa.JSON(), nullable=True)),
    ):
        op.add_column("search_task", column)
    for column in (
        sa.Column("company_profile", sa.JSON(), nullable=True),
        sa.Column("match_evaluation", sa.JSON(), nullable=True),
        sa.Column("evaluation_status", sa.String(40), nullable=True),
        sa.Column("target_relevance_score", sa.Integer(), nullable=True),
        sa.Column("relevance_rating", sa.String(20), nullable=True),
    ):
        op.add_column("discovery_candidate", column)
    op.create_index("ix_discovery_candidate_evaluation_status", "discovery_candidate", ["evaluation_status"])
    op.create_table("knowledge_pack", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pack_key", sa.String(120), nullable=False), sa.Column("version", sa.String(40), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False), sa.Column("concepts", sa.JSON(), nullable=False),
        sa.Column("relationships", sa.JSON(), nullable=False), sa.Column("ambiguity_rules", sa.JSON(), nullable=False),
        sa.Column("industry_mappings", sa.JSON(), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()), sa.Column("updated_by", sa.Uuid()),
        sa.UniqueConstraint("pack_key", "version", name="uq_knowledge_pack_version"))
    op.create_index("ix_knowledge_pack_pack_key", "knowledge_pack", ["pack_key"])
    op.create_index("ix_knowledge_pack_status", "knowledge_pack", ["status"])
    op.create_table("relevance_feedback", sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid()), sa.Column("department_id", sa.Uuid()), sa.Column("owner_id", sa.Uuid()),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("search_task.id"), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), sa.ForeignKey("discovery_candidate.id"), nullable=False),
        sa.Column("original_decision", sa.String(40), nullable=False), sa.Column("human_decision", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True), sa.Column("false_positive", sa.Boolean(), nullable=False),
        sa.Column("false_negative", sa.Boolean(), nullable=False), sa.Column("version_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()), sa.Column("updated_by", sa.Uuid()))
    op.create_index("ix_relevance_feedback_task_id", "relevance_feedback", ["task_id"])
    op.create_index("ix_relevance_feedback_candidate_id", "relevance_feedback", ["candidate_id"])


def downgrade() -> None:
    op.drop_table("relevance_feedback")
    op.drop_table("knowledge_pack")
    op.drop_index("ix_discovery_candidate_evaluation_status", table_name="discovery_candidate")
    for name in ("relevance_rating", "target_relevance_score", "evaluation_status", "match_evaluation", "company_profile"):
        op.drop_column("discovery_candidate", name)
    for name in ("knowledge_snapshot", "intent_prompt_version", "intent_schema_version", "search_intent"):
        op.drop_column("search_task", name)
