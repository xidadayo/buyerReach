"""Add organization hierarchy and versioned authorization fields.

Revision ID: 20260722_0036
Revises: 20260722_0035
"""

from collections.abc import Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0036"
down_revision: str | None = "20260722_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "organization_unit" not in tables:
        op.create_table(
            "organization_unit",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("organization_unit.id")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("code", sa.String(120), nullable=False),
            sa.Column("unit_type", sa.String(40), nullable=False, server_default="department"),
            sa.Column("manager_user_id", sa.Uuid(), sa.ForeignKey("user.id")),
            sa.Column("path", sa.String(1000), nullable=False, server_default="/"),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(40), nullable=False, server_default="active"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", sa.Uuid()),
            sa.Column("updated_by", sa.Uuid()),
            sa.UniqueConstraint("organization_id", "parent_id", "name", name="uq_org_unit_org_parent_name"),
            sa.UniqueConstraint("organization_id", "code", name="uq_org_unit_org_code"),
        )
        for name, columns in (
            ("ix_org_unit_org", ["organization_id"]),
            ("ix_org_unit_parent", ["parent_id"]),
            ("ix_org_unit_path", ["path"]),
            ("ix_org_unit_status", ["status"]),
        ):
            op.create_index(name, "organization_unit", columns)

    inspector = sa.inspect(conn)
    role_columns = _columns(inspector, "role")
    role_additions = (
        ("organization_id", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"))),
        ("is_system", sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("status", sa.Column("status", sa.String(40), nullable=False, server_default="active")),
        ("permission_version", sa.Column("permission_version", sa.Integer(), nullable=False, server_default="1")),
        ("data_scopes", sa.Column("data_scopes", sa.JSON())),
    )
    for name, column in role_additions:
        if name not in role_columns:
            op.add_column("role", column)
    inspector = sa.inspect(conn)
    for constraint in inspector.get_unique_constraints("role"):
        if constraint.get("column_names") == ["name"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "role", type_="unique")
    inspector = sa.inspect(conn)
    if not any(
        constraint.get("name") == "uq_role_org_name"
        for constraint in inspector.get_unique_constraints("role")
    ):
        op.create_unique_constraint("uq_role_org_name", "role", ["organization_id", "name"])
    if "ix_role_org" not in _indexes(inspector, "role"):
        op.create_index("ix_role_org", "role", ["organization_id"])

    if "organization_unit_id" not in _columns(inspector, "user"):
        op.add_column(
            "user",
            sa.Column("organization_unit_id", sa.Uuid(), sa.ForeignKey("organization_unit.id")),
        )
    inspector = sa.inspect(conn)
    if "ix_user_org_unit" not in _indexes(inspector, "user"):
        op.create_index("ix_user_org_unit", "user", ["organization_unit_id"])

    batch_columns = _columns(inspector, "batch_import")
    if "department_id" not in batch_columns:
        op.add_column(
            "batch_import",
            sa.Column("department_id", sa.Uuid(), sa.ForeignKey("organization_unit.id")),
        )
    if "owner_id" not in batch_columns:
        op.add_column(
            "batch_import", sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id"))
        )
    inspector = sa.inspect(conn)
    for name, column in (
        ("ix_batch_import_department_id", "department_id"),
        ("ix_batch_import_owner_id", "owner_id"),
    ):
        if name not in _indexes(inspector, "batch_import"):
            op.create_index(name, "batch_import", [column])

    audit_columns = _columns(inspector, "audit_log")
    for name in ("organization_id", "organization_unit_id"):
        if name not in audit_columns:
            op.add_column("audit_log", sa.Column(name, sa.Uuid()))
    inspector = sa.inspect(conn)
    for name, column in (
        ("ix_audit_log_org", "organization_id"),
        ("ix_audit_log_org_unit", "organization_unit_id"),
        ("ix_audit_log_actor", "actor_id"),
    ):
        if name not in _indexes(inspector, "audit_log"):
            op.create_index(name, "audit_log", [column])

    metadata = sa.MetaData()
    organization = sa.Table("organization", metadata, autoload_with=conn)
    unit = sa.Table("organization_unit", metadata, autoload_with=conn)
    user = sa.Table("user", metadata, autoload_with=conn)
    roots: dict[str, object] = {}
    roots_created = 0
    for org in conn.execute(sa.select(organization.c.id, organization.c.name)):
        root_id = conn.scalar(
            sa.select(unit.c.id).where(
                unit.c.organization_id == org.id,
                unit.c.parent_id.is_(None),
            ).limit(1)
        )
        if root_id is None:
            root_id = uuid4()
            conn.execute(
                unit.insert().values(
                    id=root_id,
                    organization_id=org.id,
                    name="总部",
                    code="headquarters",
                    unit_type="company",
                    path="/",
                    depth=0,
                    status="active",
                    version=1,
                )
            )
            roots_created += 1
        roots[str(org.id)] = root_id

    users_backfilled = 0
    user_columns = _columns(sa.inspect(conn), "user")
    selected = [user.c.id, user.c.organization_id, user.c.organization_unit_id]
    if "department_id" in user_columns:
        selected.append(user.c.department_id)
    for row in conn.execute(sa.select(*selected)):
        if row.organization_unit_id is not None or row.organization_id is None:
            continue
        root_id = roots.get(str(row.organization_id))
        if root_id is not None:
            conn.execute(
                user.update().where(user.c.id == row.id).values(organization_unit_id=root_id)
            )
            users_backfilled += 1

    default_scopes = {
        resource: "organization"
        for resource in ("tasks", "brands", "contacts", "emails", "imports")
    }
    role = sa.Table("role", metadata, autoload_with=conn)
    from app.authz.catalog import flatten_permissions

    for role_row in conn.execute(sa.select(role.c.id, role.c.permissions)):
        if not isinstance(role_row.permissions, dict):
            continue
        converted: dict[str, list[str]] = {}
        for permission in flatten_permissions(role_row.permissions):
            if permission == "admin:*":
                converted.setdefault("admin", []).append("*")
                continue
            resource, operation = permission.split(":", 1)
            converted.setdefault(resource, []).append(operation)
        converted = {key: sorted(set(values)) for key, values in converted.items()}
        conn.execute(
            role.update().where(role.c.id == role_row.id).values(permissions=converted)
        )
    conn.execute(
        role.update()
        .where(role.c.name.in_(["admin", "operator", "viewer"]))
        .values(is_system=True, permission_version=1)
    )
    conn.execute(
        role.update()
        .where(role.c.data_scopes.is_(None))
        .values(data_scopes=default_scopes)
    )

    business_backfilled = 0
    for table_name in ("search_task", "brand", "contact", "email_address", "batch_import"):
        if table_name not in set(sa.inspect(conn).get_table_names()):
            continue
        table = sa.Table(table_name, metadata, autoload_with=conn)
        columns = {column.name for column in table.columns}
        if not {"organization_id", "department_id"}.issubset(columns):
            continue
        for org_id, root_id in roots.items():
            result = conn.execute(
                table.update()
                .where(
                    table.c.organization_id == UUID(org_id),
                    table.c.department_id.is_(None),
                )
                .values(department_id=root_id)
            )
            business_backfilled += result.rowcount or 0

    print(f"[0036] root_units_created={roots_created}")
    print(f"[0036] users_backfilled={users_backfilled}")
    print(f"[0036] business_records_backfilled={business_backfilled}")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "audit_log" in inspector.get_table_names():
        indexes = _indexes(inspector, "audit_log")
        for name in ("ix_audit_log_actor", "ix_audit_log_org_unit", "ix_audit_log_org"):
            if name in indexes:
                op.drop_index(name, table_name="audit_log")
        columns = _columns(sa.inspect(conn), "audit_log")
        for name in ("organization_unit_id", "organization_id"):
            if name in columns:
                op.drop_column("audit_log", name)

    if "ix_user_org_unit" in _indexes(sa.inspect(conn), "user"):
        op.drop_index("ix_user_org_unit", table_name="user")
    if "organization_unit_id" in _columns(sa.inspect(conn), "user"):
        op.drop_column("user", "organization_unit_id")

    batch_indexes = _indexes(sa.inspect(conn), "batch_import")
    for name in ("ix_batch_import_owner_id", "ix_batch_import_department_id"):
        if name in batch_indexes:
            op.drop_index(name, table_name="batch_import")
    batch_columns = _columns(sa.inspect(conn), "batch_import")
    for name in ("owner_id", "department_id"):
        if name in batch_columns:
            op.drop_column("batch_import", name)

    if "ix_role_org" in _indexes(sa.inspect(conn), "role"):
        op.drop_index("ix_role_org", table_name="role")
    if any(
        constraint.get("name") == "uq_role_org_name"
        for constraint in sa.inspect(conn).get_unique_constraints("role")
    ):
        op.drop_constraint("uq_role_org_name", "role", type_="unique")
    role_columns = _columns(sa.inspect(conn), "role")
    for name in ("data_scopes", "permission_version", "status", "is_system", "organization_id"):
        if name in role_columns:
            op.drop_column("role", name)

    if "organization_unit" in sa.inspect(conn).get_table_names():
        op.drop_table("organization_unit")
