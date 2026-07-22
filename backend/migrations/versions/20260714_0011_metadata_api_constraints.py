"""add metadata API uniqueness constraints

Revision ID: 20260714_0011
Revises: 20260714_0010
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0011"
down_revision: str | None = "20260714_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Preserve the oldest definition when legacy data contains duplicates and
    # move its assignments/values to that definition before adding constraints.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   FIRST_VALUE(id) OVER (
                       PARTITION BY module, name ORDER BY created_at, id
                   ) AS keep_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY module, name ORDER BY created_at, id
                   ) AS row_number
            FROM tag
        )
        UPDATE entity_tag AS assignment
        SET tag_id = ranked.keep_id
        FROM ranked
        WHERE assignment.tag_id = ranked.id AND ranked.row_number > 1
        """
    )
    op.execute(
        """
        DELETE FROM entity_tag
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY entity_type, entity_id, tag_id
                           ORDER BY created_at, id
                       ) AS row_number
                FROM entity_tag
            ) AS duplicates
            WHERE duplicates.row_number > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM tag
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY module, name ORDER BY created_at, id
                       ) AS row_number
                FROM tag
            ) AS duplicates
            WHERE duplicates.row_number > 1
        )
        """
    )

    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   FIRST_VALUE(id) OVER (
                       PARTITION BY module, name ORDER BY created_at, id
                   ) AS keep_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY module, name ORDER BY created_at, id
                   ) AS row_number
            FROM custom_field
        )
        UPDATE custom_value AS custom_value
        SET field_id = ranked.keep_id
        FROM ranked
        WHERE custom_value.field_id = ranked.id AND ranked.row_number > 1
        """
    )
    op.execute(
        """
        DELETE FROM custom_value
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY field_id, entity_type, entity_id
                           ORDER BY updated_at DESC, id
                       ) AS row_number
                FROM custom_value
            ) AS duplicates
            WHERE duplicates.row_number > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM custom_field
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY module, name ORDER BY created_at, id
                       ) AS row_number
                FROM custom_field
            ) AS duplicates
            WHERE duplicates.row_number > 1
        )
        """
    )

    op.create_unique_constraint("uq_tag_module_name", "tag", ["module", "name"])
    op.create_unique_constraint(
        "uq_entity_tag_assignment",
        "entity_tag",
        ["entity_type", "entity_id", "tag_id"],
    )
    op.create_unique_constraint(
        "uq_custom_field_module_name",
        "custom_field",
        ["module", "name"],
    )
    op.create_unique_constraint(
        "uq_custom_value_field_entity",
        "custom_value",
        ["field_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_custom_value_field_entity", "custom_value", type_="unique")
    op.drop_constraint("uq_custom_field_module_name", "custom_field", type_="unique")
    op.drop_constraint("uq_entity_tag_assignment", "entity_tag", type_="unique")
    op.drop_constraint("uq_tag_module_name", "tag", type_="unique")
