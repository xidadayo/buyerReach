"""add Prospeo quota user agent

Revision ID: 20260715_0018
Revises: 20260715_0017
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0018"
down_revision: str | None = "20260715_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            config::jsonb,
            '{quota_headers}',
            COALESCE(config::jsonb->'quota_headers', '{}'::jsonb)
                || jsonb_build_object('User-Agent', 'BuyerReach/1.0'),
            true
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND COALESCE(config::jsonb->'quota_headers'->>'User-Agent', '') = ''
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            config::jsonb,
            '{quota_headers}',
            COALESCE(config::jsonb->'quota_headers', '{}'::jsonb) - 'User-Agent',
            true
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND config::jsonb->'quota_headers'->>'User-Agent' = 'BuyerReach/1.0'
        """
    )
