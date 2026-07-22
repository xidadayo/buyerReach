"""backfill organization ownership for legacy contacts

Revision ID: 20260722_0035
Revises: 20260722_0034
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0035"
down_revision: str | None = "20260722_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Prefer ownership already attached to a related brand, company, or email.
    # Only assign when all available evidence resolves to one organization.
    op.execute(
        sa.text(
            """
            WITH ownership_candidates AS (
                SELECT cp.contact_id, b.organization_id
                FROM contact_position cp
                JOIN brand b ON b.id = cp.brand_id
                WHERE cp.deleted_at IS NULL AND b.organization_id IS NOT NULL
                UNION
                SELECT cp.contact_id, company.organization_id
                FROM contact_position cp
                JOIN company ON company.id = cp.company_id
                WHERE cp.deleted_at IS NULL AND company.organization_id IS NOT NULL
                UNION
                SELECT email_address.contact_id, email_address.organization_id
                FROM email_address
                WHERE email_address.deleted_at IS NULL
                  AND email_address.contact_id IS NOT NULL
                  AND email_address.organization_id IS NOT NULL
            ), resolved_ownership AS (
                SELECT contact_id, min(cast(organization_id AS text)) AS organization_id
                FROM ownership_candidates
                GROUP BY contact_id
                HAVING count(DISTINCT organization_id) = 1
            )
            UPDATE contact
            SET organization_id = cast(resolved_ownership.organization_id AS uuid)
            FROM resolved_ownership
            WHERE contact.id = resolved_ownership.contact_id
              AND contact.organization_id IS NULL
            """
        )
    )
    # Legacy single-organization installations have no ownership evidence on
    # related entities. In that unambiguous case, assign the sole organization.
    op.execute(
        sa.text(
            """
            UPDATE contact
            SET organization_id = (SELECT id FROM organization LIMIT 1)
            WHERE organization_id IS NULL
              AND (SELECT count(*) FROM organization) = 1
            """
        )
    )


def downgrade() -> None:
    # Ownership cannot be safely distinguished from values written after this
    # migration. Preserve the backfill when rolling application code back.
    pass
