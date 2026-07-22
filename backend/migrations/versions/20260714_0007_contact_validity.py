"""derive contact validity from verified associated emails

Revision ID: 20260714_0007
Revises: 20260714_0006
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0007"
down_revision: str | None = "20260714_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "contact",
        "status",
        existing_type=sa.String(length=40),
        server_default="invalid",
        existing_nullable=False,
    )
    op.execute(
        """
        UPDATE contact AS c
        SET status = CASE
            WHEN EXISTS (
                SELECT 1 FROM email_address AS e
                WHERE e.contact_id = c.id
                  AND e.deleted_at IS NULL
                  AND e.authenticity_level = 'verified'
                  AND e.pool = 'valid'
            ) THEN 'valid'
            WHEN EXISTS (
                SELECT 1 FROM email_address AS e
                WHERE e.contact_id = c.id
                  AND e.deleted_at IS NULL
                  AND e.pool = 'manual_review'
            ) THEN 'pending_review'
            WHEN EXISTS (
                SELECT 1 FROM email_address AS e
                WHERE e.contact_id = c.id
                  AND e.deleted_at IS NULL
                  AND e.pool = 'raw'
            ) THEN 'pending_verification'
            ELSE 'invalid'
        END
        WHERE c.deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("UPDATE contact SET status = 'active' WHERE deleted_at IS NULL")
    op.alter_column(
        "contact",
        "status",
        existing_type=sa.String(length=40),
        server_default="active",
        existing_nullable=False,
    )
