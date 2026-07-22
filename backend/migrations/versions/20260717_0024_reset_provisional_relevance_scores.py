"""reset relevance scores that were derived from search constraints

Revision ID: 20260717_0024
Revises: 20260717_0023
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260717_0024"
down_revision: str | None = "20260717_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE discovery_candidate
        SET relevance_score = 0
        WHERE industry_source IS NULL OR industry_confidence IS NULL
        """
    )
    # A discovery hit records admission by a search task, not an independent
    # industry evaluation. Historical values used the removed additive formula.
    op.execute("UPDATE discovery_candidate_hit SET relevance_score = 0")


def downgrade() -> None:
    # The old values cannot be reconstructed because they were not evidence-led.
    pass
