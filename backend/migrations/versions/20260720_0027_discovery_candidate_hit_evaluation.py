"""task-scoped candidate match facts on discovery_candidate_hit

Revision ID: 20260720_0027
Revises: 20260718_0026

Additive expand step: DiscoveryCandidateHit becomes the source of truth for
"how did this candidate match THIS search task". The backfill copies the
candidate-level formal evaluation only onto the hit whose task_id equals the
candidate's last_task_id (the task that produced the evaluation). All other
historical hits stay unevaluated — no guessing or reassignment. The backfill
is idempotent: it only touches rows with evaluated_at IS NULL, so it can be
re-run safely after a partial failure.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260720_0027"
down_revision = "20260718_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("evaluation_status", sa.String(40), nullable=True),
        sa.Column("target_relevance_score", sa.Integer(), nullable=True),
        sa.Column("relevance_rating", sa.String(20), nullable=True),
        sa.Column("match_evaluation", sa.JSON(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scoring_policy_version", sa.String(40), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=True),
        sa.Column("evidence_schema_version", sa.String(40), nullable=True),
    ):
        op.add_column("discovery_candidate_hit", column)
    op.create_index(
        "ix_discovery_candidate_hit_task_eval",
        "discovery_candidate_hit",
        ["task_id", "evaluation_status"],
    )
    # Idempotent history backfill: only the hit belonging to the task that
    # produced the candidate's current formal evaluation may receive it.
    op.execute(
        """
        UPDATE discovery_candidate_hit SET
            evaluation_status = (
                SELECT dc.evaluation_status FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            target_relevance_score = (
                SELECT dc.target_relevance_score FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            relevance_rating = (
                SELECT dc.relevance_rating FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            match_evaluation = (
                SELECT dc.match_evaluation FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            evaluated_at = (
                SELECT COALESCE(dc.industry_enriched_at, dc.updated_at)
                FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            scoring_policy_version = (
                SELECT dc.match_evaluation->>'policy_version' FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            prompt_version = (
                SELECT CASE WHEN dc.match_evaluation IS NOT NULL
                       THEN 'concept-match-2.0.0' END
                FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id),
            evidence_schema_version = (
                SELECT dc.match_evaluation->>'evidence_schema_version'
                FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id)
        WHERE discovery_candidate_hit.evaluated_at IS NULL
          AND EXISTS (
                SELECT 1 FROM discovery_candidate dc
                WHERE dc.id = discovery_candidate_hit.candidate_id
                  AND dc.last_task_id = discovery_candidate_hit.task_id
                  AND dc.evaluation_status IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_candidate_hit_task_eval", table_name="discovery_candidate_hit"
    )
    for name in (
        "evidence_schema_version",
        "prompt_version",
        "scoring_policy_version",
        "evaluated_at",
        "match_evaluation",
        "relevance_rating",
        "target_relevance_score",
        "evaluation_status",
    ):
        op.drop_column("discovery_candidate_hit", name)
