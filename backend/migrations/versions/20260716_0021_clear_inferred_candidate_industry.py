"""clear candidate industries copied from search input

Revision ID: 20260716_0021
Revises: 20260715_0020
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260716_0021"
down_revision: str | None = "20260715_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Historical Hunter candidates stored the requested search category rather
    # than a per-company value returned by Hunter Discover.
    op.execute("UPDATE discovery_candidate SET industry = NULL WHERE provider ILIKE 'hunter%'")


def downgrade() -> None:
    # Fabricated historical values cannot be reconstructed safely.
    pass
