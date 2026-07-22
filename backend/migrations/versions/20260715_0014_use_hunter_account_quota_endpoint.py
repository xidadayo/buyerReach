"""use Hunter account endpoint for quota checks

Revision ID: 20260715_0014
Revises: 20260715_0013
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0014"
down_revision: str | None = "20260715_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HUNTER_ACCOUNT_ENDPOINT = "https://api.hunter.io/v2/account"
_LEGACY_HUNTER_USAGE_ENDPOINT = "https://api.hunter.io/v2/usage"


def upgrade() -> None:
    # Quota checks are configuration-driven; only update Hunter configurations.
    op.execute(
        f"""
        UPDATE provider_config
        SET config = (
            config::jsonb || jsonb_build_object(
                'quota_endpoint_url', '{_HUNTER_ACCOUNT_ENDPOINT}'
            )
        )::json
        WHERE config->>'adapter' = 'hunter'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE provider_config
        SET config = (
            config::jsonb || jsonb_build_object(
                'quota_endpoint_url', '{_LEGACY_HUNTER_USAGE_ENDPOINT}'
            )
        )::json
        WHERE config->>'adapter' = 'hunter'
          AND config->>'quota_endpoint_url' = '{_HUNTER_ACCOUNT_ENDPOINT}'
        """
    )
