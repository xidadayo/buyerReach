"""add a dedicated brand domain email search provider

Revision ID: 20260714_0008
Revises: 20260714_0007
Create Date: 2026-07-14
"""

from collections.abc import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0008"
down_revision: str | None = "20260714_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO provider_config (
                provider, type, priority, quota, enabled, config,
                id, created_at, updated_at, created_by, updated_by
            )
            SELECT
                'hunter-domain-email-search', 'brand_email_search',
                source.priority, source.quota, source.enabled, source.config,
                :provider_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                source.created_by, source.updated_by
            FROM provider_config AS source
            WHERE source.provider = 'hunter-email-finder'
              AND source.type = 'email_finder'
              AND NOT EXISTS (
                  SELECT 1 FROM provider_config
                  WHERE provider = 'hunter-domain-email-search'
                    AND type = 'brand_email_search'
              )
            LIMIT 1
            """
        ),
        {"provider_id": str(uuid4())},
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM provider_config "
        "WHERE provider = 'hunter-domain-email-search' "
        "AND type = 'brand_email_search'"
    )
