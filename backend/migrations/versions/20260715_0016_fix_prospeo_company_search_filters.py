"""fix Prospeo company search filters

Revision ID: 20260715_0016
Revises: 20260715_0015
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0016"
down_revision: str | None = "20260715_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            jsonb_set(
                config::jsonb,
                '{request_body}',
                '{
                    "page": 1,
                    "filters": {
                        "company": {
                            "names": {"include": "{{brand_keywords}}"},
                            "websites": {"include": "{{official_domains}}"}
                        }
                    }
                }'::jsonb,
                true
            ),
            '{supported_modes}',
            '["exact_brand"]'::jsonb,
            true
        )::json
        WHERE provider = 'prospeo-company-search'
          AND type = 'company_search'
          AND config->>'adapter' = 'prospeo'
        """
    )
    op.execute(
        """
        UPDATE provider_config
        SET config = (
            config::jsonb
            || jsonb_build_object(
                'connection_test_endpoint_url', 'https://api.prospeo.io/account-information',
                'connection_test_method', 'GET',
                'connection_test_headers', jsonb_build_object('User-Agent', 'BuyerReach/1.0'),
                'connection_test_body', '{}'::jsonb
            )
        )::json
        WHERE config->>'adapter' = 'prospeo'
          AND (
            COALESCE(config->>'connection_test_endpoint_url', '') = ''
            OR config->>'connection_test_endpoint_url' = 'https://api.prospeo.io/search-suggestions'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = jsonb_set(
            (config::jsonb - 'supported_modes'),
            '{request_body}',
            '{
                "page": 1,
                "filters": {
                    "company": {"names": {"include": "{{brand_keywords}}"}},
                    "company_industry": {"include": "{{categories}}"},
                    "company_location_search": {"include": "{{countries}}"}
                }
            }'::jsonb,
            true
        )::json
        WHERE provider = 'prospeo-company-search'
          AND type = 'company_search'
          AND config->>'adapter' = 'prospeo'
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
