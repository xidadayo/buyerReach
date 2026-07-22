"""backfill legacy search task organization ownership

Revision ID: 20260720_0028
Revises: 20260720_0027
"""

from alembic import op


revision = "20260720_0028"
down_revision = "20260720_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing single-tenant installations created tasks before ownership was
    # wired through the API. Attach only unowned tasks to the seeded Default
    # organization; already-owned tenant data is never changed.
    op.execute(
        """
        UPDATE search_task
        SET organization_id = (SELECT id FROM organization WHERE name = 'Default' LIMIT 1)
        WHERE organization_id IS NULL
          AND EXISTS (SELECT 1 FROM organization WHERE name = 'Default')
        """
    )


def downgrade() -> None:
    # Ownership backfill is intentionally retained on application rollback.
    # Clearing it would reopen cross-tenant visibility and lose provenance.
    pass
