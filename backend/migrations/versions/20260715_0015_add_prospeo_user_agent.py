"""add Prospeo request user agent

Revision ID: 20260715_0015
Revises: 20260715_0014
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0015"
down_revision: str | None = "20260715_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            config::jsonb,
            '{request_headers}',
            COALESCE(config::jsonb->'request_headers', '{}'::jsonb)
                || jsonb_build_object('User-Agent', 'BuyerReach/1.0'),
            true
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND COALESCE(config::jsonb->'request_headers'->>'User-Agent', '') = ''
        """
    )
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
    op.execute(
        """
        UPDATE provider_config
        SET config = (
            config::jsonb || jsonb_build_object(
                'connection_test_endpoint_url', 'https://api.prospeo.io/account-information',
                'connection_test_method', 'GET',
                'connection_test_headers', jsonb_build_object('User-Agent', 'BuyerReach/1.0'),
                'connection_test_body', '{}'::jsonb
            )
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND COALESCE(config->>'connection_test_endpoint_url', '') = ''
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            config::jsonb,
            '{request_headers}',
            COALESCE(config::jsonb->'request_headers', '{}'::jsonb) - 'User-Agent',
            true
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND config::jsonb->'request_headers'->>'User-Agent' = 'BuyerReach/1.0'
        """
    )
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
    op.execute(
        """
        UPDATE provider_config
        SET config = (
            config::jsonb
            - 'connection_test_endpoint_url'
            - 'connection_test_method'
            - 'connection_test_headers'
            - 'connection_test_body'
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND config->>'connection_test_endpoint_url' = 'https://api.prospeo.io/account-information'
        """
    )
