"""Repair legacy group ownership and scope email identity by group.

Revision ID: 20260724_0038
Revises: 20260724_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260724_0038"
down_revision: str | None = "20260724_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_missing_units(conn: sa.Connection, table_name: str, unit_column: str = "department_id") -> None:
    """Put legacy rows into their organisation's root unit.

    Rows without an organisation remain deliberately unassigned: assigning them
    to an arbitrary tenant would be a cross-tenant disclosure. They stay visible
    only to the platform administrator until explicitly assigned.
    """
    conn.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS entity
            SET {unit_column} = root.id
            FROM organization_unit AS root
            WHERE entity.organization_id = root.organization_id
              AND root.parent_id IS NULL
              AND entity.organization_id IS NOT NULL
              AND entity.{unit_column} IS NULL
            """
        )
    )


def _inherit_owner_tenancy(conn: sa.Connection, table_name: str) -> None:
    """Use the record owner as the authoritative legacy tenancy source."""
    conn.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS entity
            SET organization_id = owner.organization_id,
                department_id = owner.organization_unit_id
            FROM "user" AS owner
            WHERE entity.owner_id = owner.id
              AND entity.organization_id IS NULL
              AND owner.organization_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS entity
            SET department_id = owner.organization_unit_id
            FROM "user" AS owner
            WHERE entity.owner_id = owner.id
              AND entity.organization_id = owner.organization_id
              AND entity.department_id IS NULL
              AND owner.organization_unit_id IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Existing users and business records created before strict group isolation
    # inherit the root unit of their own organisation. This makes the migration
    # deterministic and never moves a record into another organisation.
    _set_missing_units(conn, '"user"', "organization_unit_id")
    for table_name in ("search_task", "brand", "contact", "email_address", "batch_import"):
        _inherit_owner_tenancy(conn, table_name)
        _set_missing_units(conn, table_name)

    # Email uniqueness used to be organisation-wide, which caused one group's
    # email record to be silently reused by another group. Scope identity to the
    # organisation unit just like the authorization boundary.
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("email_address") as batch:
            batch.drop_constraint("uq_email_org_address", type_="unique")
            batch.create_unique_constraint(
                "uq_email_org_unit_address",
                ["organization_id", "department_id", "normalized_address"],
            )
    else:
        op.drop_constraint("uq_email_org_address", "email_address", type_="unique")
        op.create_unique_constraint(
            "uq_email_org_unit_address",
            "email_address",
            ["organization_id", "department_id", "normalized_address"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("email_address") as batch:
            batch.drop_constraint("uq_email_org_unit_address", type_="unique")
            batch.create_unique_constraint(
                "uq_email_org_address",
                ["organization_id", "normalized_address"],
            )
    else:
        op.drop_constraint("uq_email_org_unit_address", "email_address", type_="unique")
        op.create_unique_constraint(
            "uq_email_org_address",
            "email_address",
            ["organization_id", "normalized_address"],
        )
