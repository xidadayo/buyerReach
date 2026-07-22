"""add isolated discovery candidate pool and Hunter Discover provider

Revision ID: 20260714_0010
Revises: 20260714_0009
Create Date: 2026-07-14
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0010"
down_revision: str | None = "20260714_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_candidate",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("normalized_domain", sa.String(length=255), nullable=True),
        sa.Column("dedupe_key", sa.String(length=600), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("emails_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_task_id", sa.Uuid(), nullable=True),
        sa.Column("exact_task_id", sa.Uuid(), nullable=True),
        sa.Column("promoted_brand_id", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["last_task_id"], ["search_task.id"]),
        sa.ForeignKeyConstraint(["exact_task_id"], ["search_task.id"]),
        sa.ForeignKeyConstraint(["promoted_brand_id"], ["brand.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_discovery_candidate_name", "discovery_candidate", ["name"])
    op.create_index("ix_discovery_candidate_normalized_name", "discovery_candidate", ["normalized_name"])
    op.create_index("ix_discovery_candidate_domain", "discovery_candidate", ["domain"])
    op.create_index("ix_discovery_candidate_normalized_domain", "discovery_candidate", ["normalized_domain"])
    op.create_index("ix_discovery_candidate_dedupe_key", "discovery_candidate", ["dedupe_key"])
    op.create_index("ix_discovery_candidate_status", "discovery_candidate", ["status"])

    op.create_table(
        "discovery_candidate_hit",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["discovery_candidate.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["search_task.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "task_id", name="uq_discovery_candidate_task"),
    )
    op.create_index("ix_discovery_candidate_hit_candidate_id", "discovery_candidate_hit", ["candidate_id"])
    op.create_index("ix_discovery_candidate_hit_task_id", "discovery_candidate_hit", ["task_id"])

    _migrate_legacy_candidates()
    _add_hunter_discover_provider()


def _migrate_legacy_candidates() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT b.id, b.name, b.normalized_name, b.primary_website, b.country,
                   b.category, b.discovery_score, b.provider, b.created_at,
                   c.domain
            FROM brand b
            LEFT JOIN company c ON c.id = b.company_id
            WHERE b.status = 'pending_review' AND b.deleted_at IS NULL
            """
        )
    ).mappings()
    now = datetime.now(UTC)
    for row in rows:
        domain = str(row["domain"] or "").lower().removeprefix("www.") or None
        country = str(row["country"] or "").strip()
        key = f"domain:{domain}" if domain else f"name:{row['normalized_name']}|country:{country.casefold()}"
        connection.execute(
            sa.text(
                """
                INSERT INTO discovery_candidate (
                    id, name, normalized_name, domain, normalized_domain, dedupe_key,
                    website, country, industry, emails_count, relevance_score,
                    provider, raw_data, status, seen_count, first_seen_at, last_seen_at,
                    created_at, updated_at, created_by, updated_by
                ) VALUES (
                    :id, :name, :normalized_name, :domain, :domain, :dedupe_key,
                    :website, :country, :industry, 0, :relevance_score,
                    :provider, CAST(:raw_data AS json), 'pending', 1, :first_seen_at, :last_seen_at,
                    :created_at, :updated_at, NULL, NULL
                ) ON CONFLICT (dedupe_key) DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "name": row["name"],
                "normalized_name": row["normalized_name"],
                "domain": domain,
                "dedupe_key": key,
                "website": row["primary_website"],
                "country": row["country"],
                "industry": row["category"],
                "relevance_score": row["discovery_score"] or 0,
                "provider": row["provider"] or "legacy-discovery",
                "raw_data": "{}",
                "first_seen_at": row["created_at"] or now,
                "last_seen_at": now,
                "created_at": row["created_at"] or now,
                "updated_at": now,
            },
        )
    connection.execute(
        sa.text(
            "UPDATE brand SET status = 'migrated_candidate', deleted_at = CURRENT_TIMESTAMP "
            "WHERE status = 'pending_review' AND deleted_at IS NULL"
        )
    )


def _add_hunter_discover_provider() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO provider_config (
                provider, type, priority, quota, enabled, config,
                id, created_at, updated_at, created_by, updated_by
            )
            SELECT
                'hunter-discover', 'company_search', 50, source.quota, source.enabled,
                (
                    source.config::jsonb || jsonb_build_object(
                        'endpoint_url', 'https://api.hunter.io/v2/discover',
                        'supported_modes', jsonb_build_array('brand_discovery')
                    )
                )::json,
                :provider_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                source.created_by, source.updated_by
            FROM provider_config source
            WHERE source.type IN ('email_finder', 'brand_email_search')
              AND source.config->>'adapter' = 'hunter'
              AND NOT EXISTS (
                  SELECT 1 FROM provider_config
                  WHERE provider = 'hunter-discover' AND type = 'company_search'
              )
            ORDER BY CASE WHEN source.type = 'email_finder' THEN 0 ELSE 1 END
            LIMIT 1
            """
        ),
        {"provider_id": str(uuid4())},
    )
    connection.execute(
        sa.text(
            """
            UPDATE provider_config
            SET config = (
                config::jsonb || jsonb_build_object(
                    'supported_modes', jsonb_build_array('exact_brand')
                )
            )::json
            WHERE type = 'company_search' AND config->>'adapter' = 'apollo'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM provider_config WHERE provider = 'hunter-discover' AND type = 'company_search'"
    )
    op.execute(
        "UPDATE provider_config SET config = (config::jsonb - 'supported_modes')::json "
        "WHERE type = 'company_search' AND config->>'adapter' = 'apollo'"
    )
    op.execute(
        "UPDATE brand SET status = 'pending_review', deleted_at = NULL "
        "WHERE status = 'migrated_candidate'"
    )
    op.drop_index("ix_discovery_candidate_hit_task_id", table_name="discovery_candidate_hit")
    op.drop_index("ix_discovery_candidate_hit_candidate_id", table_name="discovery_candidate_hit")
    op.drop_table("discovery_candidate_hit")
    op.drop_index("ix_discovery_candidate_status", table_name="discovery_candidate")
    op.drop_index("ix_discovery_candidate_dedupe_key", table_name="discovery_candidate")
    op.drop_index("ix_discovery_candidate_normalized_domain", table_name="discovery_candidate")
    op.drop_index("ix_discovery_candidate_domain", table_name="discovery_candidate")
    op.drop_index("ix_discovery_candidate_normalized_name", table_name="discovery_candidate")
    op.drop_index("ix_discovery_candidate_name", table_name="discovery_candidate")
    op.drop_table("discovery_candidate")
