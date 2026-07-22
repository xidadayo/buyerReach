"""deduplicate active contact positions and enforce their identity

Revision ID: 20260722_0033
Revises: 20260722_0032
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0033"
down_revision: str | None = "20260722_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the oldest row as the canonical position. Task and Vendor provenance
    # remain in task results/source evidence rather than duplicate positions.
    op.execute(
        sa.text(
            """
            DELETE FROM contact_position
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        row_number() OVER (
                            PARTITION BY
                                contact_id,
                                coalesce(cast(company_id AS text), ''),
                                coalesce(cast(brand_id AS text), ''),
                                lower(trim(title))
                            ORDER BY created_at, id
                        ) AS duplicate_rank
                    FROM contact_position
                    WHERE deleted_at IS NULL
                ) AS ranked_positions
                WHERE duplicate_rank > 1
            )
            """
        )
    )
    op.create_index(
        "uq_contact_position_active_identity",
        "contact_position",
        [
            "contact_id",
            sa.text("coalesce(cast(company_id AS text), '')"),
            sa.text("coalesce(cast(brand_id AS text), '')"),
            sa.text("lower(trim(title))"),
        ],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_contact_position_active_identity", table_name="contact_position")
