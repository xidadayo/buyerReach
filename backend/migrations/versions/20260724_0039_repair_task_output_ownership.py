"""Repair task output created by workers running before group propagation.

Revision ID: 20260724_0039
Revises: 20260724_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260724_0039"
down_revision: str | None = "20260724_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # TaskItem is the durable provenance link between a search task and every
    # discovered entity. Only fill missing ownership; never overwrite a manual
    # assignment made after task completion.
    conn.execute(
        sa.text(
            """
            UPDATE brand AS brand
            SET organization_id = task.organization_id,
                department_id = task.department_id,
                owner_id = task.owner_id
            FROM task_item AS item
            JOIN search_task AS task ON task.id = item.task_id
            WHERE item.entity_type = 'brand'
              AND item.entity_id = brand.id::text
              AND brand.organization_id IS NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE contact AS contact
            SET organization_id = brand.organization_id,
                department_id = brand.department_id,
                owner_id = COALESCE(contact.owner_id, brand.owner_id)
            FROM contact_position AS position
            JOIN brand ON brand.id = position.brand_id
            WHERE position.contact_id = contact.id
              AND contact.department_id IS NULL
              AND brand.department_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE email_address AS email
            SET organization_id = contact.organization_id,
                department_id = contact.department_id,
                owner_id = COALESCE(email.owner_id, contact.owner_id)
            FROM contact
            WHERE email.contact_id = contact.id
              AND email.department_id IS NULL
              AND contact.department_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE email_address AS email
            SET organization_id = brand.organization_id,
                department_id = brand.department_id,
                owner_id = COALESCE(email.owner_id, brand.owner_id)
            FROM brand
            WHERE email.brand_id = brand.id
              AND email.contact_id IS NULL
              AND email.department_id IS NULL
              AND brand.department_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    # Ownership repairs are intentionally retained: they prevent data leakage.
    pass
