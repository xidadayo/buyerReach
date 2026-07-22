"""add disabled Prospeo Provider configurations

Revision ID: 20260715_0013
Revises: 20260715_0012
Create Date: 2026-07-15
"""

from collections.abc import Sequence
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0013"
down_revision: str | None = "20260715_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMMON_CONFIG = {
    "adapter": "prospeo",
    "api_key": "",
    "api_key_header": "X-KEY",
    "request_method": "POST",
    "request_headers": {},
    "request_query": {},
    "quota_endpoint_url": "https://api.prospeo.io/account-information",
    "quota_method": "GET",
    "quota_api_key_header": "X-KEY",
    "quota_remaining_path": "response.remaining_credits",
    "quota_used_path": "response.used_credits",
    "quota_reset_at_path": "response.next_quota_renewal_date",
    "quota_headers": {"User-Agent": "BuyerReach/1.0"},
    "quota_request_body": {},
}


def upgrade() -> None:
    configurations = [
        (
            "prospeo-company-search",
            "company_search",
            120,
            {
                **_COMMON_CONFIG,
                "endpoint_url": "https://api.prospeo.io/search-company",
                "supported_modes": ["exact_brand"],
                "request_body": {
                    "page": 1,
                    "filters": {
                        "company": {
                            "names": {"include": "{{brand_keywords}}"},
                            "websites": {"include": "{{official_domains}}"},
                        },
                    },
                },
                "response_items_path": "results",
                "response_field_map": {
                    "brand_name": "company.name",
                    "legal_name": "company.name",
                    "website": "company.website",
                    "domain": "company.domain",
                    "category": "company.industry",
                    "headquarters_country": "company.location.country",
                    "country": "company.location.country",
                },
                "limit": 25,
            },
        ),
        (
            "prospeo-contact-search",
            "contact_search",
            120,
            {
                **_COMMON_CONFIG,
                "endpoint_url": "https://api.prospeo.io/search-person",
                "request_body": {
                    "page": 1,
                    "filters": {
                        "company": {"websites": {"include": ["{{domain}}"]}},
                        "person_job_title": {"include": "{{titles}}"},
                    },
                },
                "response_items_path": "results",
                "response_field_map": {
                    "first_name": "person.first_name",
                    "last_name": "person.last_name",
                    "title": "person.current_job_title",
                    "linkedin_url": "person.linkedin_url",
                },
                "limit": 25,
            },
        ),
        (
            "prospeo-email-finder",
            "email_finder",
            120,
            {
                **_COMMON_CONFIG,
                "endpoint_url": "https://api.prospeo.io/enrich-person",
                "request_body": {
                    "only_verified_email": True,
                    "data": {
                        "first_name": "{{first_name}}",
                        "last_name": "{{last_name}}",
                        "full_name": "{{full_name}}",
                        "company_website": "{{domain}}",
                    },
                },
                "response_items_path": "person",
                "response_field_map": {"address": "email.email"},
                "limit": 1,
            },
        ),
    ]
    connection = op.get_bind()
    for provider, provider_type, priority, config in configurations:
        connection.execute(
            sa.text(
                """
                INSERT INTO provider_config (
                    provider, type, priority, quota, enabled, config,
                    id, created_at, updated_at, created_by, updated_by
                )
                SELECT CAST(:provider AS varchar(80)), CAST(:provider_type AS varchar(80)),
                    CAST(:priority AS integer), NULL, false, CAST(:config AS json),
                    CAST(:id AS uuid), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM provider_config
                    WHERE provider = CAST(:provider AS varchar(80))
                      AND type = CAST(:provider_type AS varchar(80))
                )
                """
            ),
            {
                "provider": provider,
                "provider_type": provider_type,
                "priority": priority,
                "config": json.dumps(config),
                "id": str(uuid4()),
            },
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM provider_config
        WHERE provider IN (
            'prospeo-company-search',
            'prospeo-contact-search',
            'prospeo-email-finder'
        )
        """
    )
