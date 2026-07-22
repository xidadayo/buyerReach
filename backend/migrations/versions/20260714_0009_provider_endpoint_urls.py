"""move vendor endpoint URLs into provider configuration

Revision ID: 20260714_0009
Revises: 20260714_0008
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260714_0009"
down_revision: str | None = "20260714_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = (
            config::jsonb || jsonb_build_object(
                'endpoint_url',
                CASE
                    WHEN config->>'adapter' = 'apollo' AND type = 'company_search'
                        THEN 'https://api.apollo.io/api/v1/mixed_companies/search'
                    WHEN config->>'adapter' = 'apollo' AND type = 'contact_search'
                        THEN 'https://api.apollo.io/api/v1/mixed_people/api_search'
                    WHEN config->>'adapter' = 'hunter' AND type = 'brand_email_search'
                        THEN 'https://api.hunter.io/v2/domain-search'
                    WHEN config->>'adapter' = 'hunter' AND type = 'email_finder'
                        THEN 'https://api.hunter.io/v2/email-finder'
                    WHEN config->>'adapter' = 'hunter' AND type = 'email_verifier'
                        THEN 'https://api.hunter.io/v2/email-verifier'
                    WHEN config->>'adapter' = 'zerobounce' AND type = 'email_verifier'
                        THEN 'https://api.zerobounce.net/v2/validate'
                END
            )
        )::json
        WHERE COALESCE(config->>'endpoint_url', '') = ''
          AND (
              (config->>'adapter' = 'apollo' AND type IN ('company_search', 'contact_search'))
              OR (config->>'adapter' = 'hunter' AND type IN ('brand_email_search', 'email_finder', 'email_verifier'))
              OR (config->>'adapter' = 'zerobounce' AND type = 'email_verifier')
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = (config::jsonb - 'endpoint_url')::json
        WHERE config::jsonb ? 'endpoint_url'
        """
    )
