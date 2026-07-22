"""link emails to brands

Revision ID: 20260713_0005
Revises: 20260713_0004
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0005"
down_revision: str | None = "20260713_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("email_address", sa.Column("brand_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_email_address_brand_id", "email_address", "brand", ["brand_id"], ["id"])
    op.create_index("ix_email_address_brand_id", "email_address", ["brand_id"])
    op.execute(
        """
        UPDATE email_address AS email
        SET brand_id = position.brand_id
        FROM contact_position AS position
        WHERE email.contact_id = position.contact_id
          AND position.brand_id IS NOT NULL
          AND position.is_current = true
          AND position.deleted_at IS NULL
          AND email.brand_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE email_address AS email
        SET brand_id = brand.id
        FROM source_evidence AS email_source
        JOIN source_evidence AS brand_source ON brand_source.url = email_source.url
        JOIN brand ON brand_source.entity_id = CAST(brand.id AS VARCHAR)
        WHERE email_source.entity_type = 'email'
          AND brand_source.entity_type = 'brand'
          AND email_source.entity_id = CAST(email.id AS VARCHAR)
          AND email.brand_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_email_address_brand_id", table_name="email_address")
    op.drop_constraint("fk_email_address_brand_id", "email_address", type_="foreignkey")
    op.drop_column("email_address", "brand_id")
