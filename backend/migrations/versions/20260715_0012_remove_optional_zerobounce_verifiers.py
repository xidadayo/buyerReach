"""remove optional ZeroBounce verifier templates

Revision ID: 20260715_0012
Revises: 20260714_0011
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0012"
down_revision: str | None = "20260714_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # BuyerReach uses only the real-time single-email verifier in its waterfall.
    op.execute(
        """
        DELETE FROM provider_config
        WHERE provider IN (
            'zerobounce-email-verifier-verify-plus',
            'zerobounce-email-verifier-activity-data',
            'zerobounce-email-verifier-batch'
        )
        """
    )


def downgrade() -> None:
    # Do not recreate removed optional templates or their user-managed settings.
    pass
