"""Default ordinary system roles to same-unit data isolation.

Revision ID: 20260724_0037
Revises: 20260722_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0037"
down_revision: str | None = "20260722_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    role = sa.table(
        "role",
        sa.column("name", sa.String()),
        sa.column("is_system", sa.Boolean()),
        sa.column("data_scopes", sa.JSON()),
    )
    unit_scopes = {
        resource: "unit"
        for resource in ("tasks", "brands", "contacts", "emails", "imports")
    }
    conn.execute(
        role.update()
        .where(role.c.is_system.is_(True), role.c.name.in_(["operator", "viewer"]))
        .values(data_scopes=unit_scopes)
    )


def downgrade() -> None:
    # Keep the safer explicit scopes; the previous application accepts them.
    pass
