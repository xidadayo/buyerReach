"""DataScope → SQLAlchemy filter compilation.

Translates `self | unit | unit_and_children | organization | all` into WHERE clauses.
"""

from sqlalchemy import Column, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BinaryExpression

from app.authz.context import AuthorizationContext


def scope_condition(
    db: Session,
    ctx: AuthorizationContext,
    resource: str,
    owner_column: Column,
    org_column: Column | None = None,
    unit_column: Column | None = None,
) -> BinaryExpression | None:
    """Return a SQLAlchemy filter condition for the given resource scope.

    Returns None when the scope is 'all' (no restriction) or ctx is super_admin.
    """
    from sqlalchemy import true as sql_true

    if "admin:*" in ctx.permissions:
        return sql_true()

    scope = ctx.data_scope_for(resource)

    if scope == "all":
        # Cross-tenant access is reserved for the explicit platform administrator.
        return org_column == ctx.organization_id if org_column is not None and ctx.organization_id else owner_column == ctx.user_id

    if scope == "organization":
        if org_column is not None and ctx.organization_id is not None:
            return org_column == ctx.organization_id
        return owner_column == ctx.user_id

    if scope == "self":
        return owner_column == ctx.user_id

    if scope == "unit":
        if unit_column is not None and ctx.organization_unit_id:
            return unit_column == ctx.organization_unit_id
        if owner_column is not None:
            return owner_column == ctx.user_id

    if scope == "unit_and_children":
        if unit_column is not None and ctx.organization_unit_id:
            from app.authz.hierarchy import descendant_ids

            from uuid import UUID

            unit_ids = [UUID(value) for value in descendant_ids(db, str(ctx.organization_unit_id))]
            return unit_column.in_(unit_ids)
        if owner_column is not None:
            return owner_column == ctx.user_id

    # Default: self
    return owner_column == ctx.user_id


def apply_scope(
    statement,
    model_class,
    db: Session,
    ctx: AuthorizationContext,
    resource: str,
    *,
    owner_column_name: str = "owner_id",
    org_column_name: str = "organization_id",
    unit_column_name: str = "department_id",
    include_shared: bool = False,
) -> object:
    """Apply data-scope filtering to an existing SQLAlchemy statement.

    Returns the statement with an additional WHERE clause (or unchanged for 'all').
    """
    owner_col = getattr(model_class, owner_column_name, None)
    org_col = getattr(model_class, org_column_name, None)
    unit_col = getattr(model_class, unit_column_name, None)

    if owner_col is None and org_col is None:
        raise ValueError(
            f"{model_class.__name__} cannot be scoped for {resource}: "
            "missing owner and organization columns"
        )

    condition = scope_condition(
        db, ctx, resource,
        owner_column=owner_col,
        org_column=org_col,
        unit_column=unit_col,
    )

    if condition is not None:
        # sql_true() means no restriction needed
        from sqlalchemy import true as sql_true
        try:
            if isinstance(condition, type(sql_true())):
                return statement
        except Exception:
            pass
        if include_shared and resource in {"tasks", "brands", "contacts", "emails"} and ctx.organization_unit_id:
            # Share grants are read-only visibility additions. Mutation paths
            # intentionally keep the default include_shared=False.
            from app.modules.models import DataShareGrant

            share_ids = select(DataShareGrant.entity_id).where(
                DataShareGrant.resource == resource,
                DataShareGrant.organization_id == ctx.organization_id,
                DataShareGrant.target_unit_id == ctx.organization_unit_id,
                DataShareGrant.permission == "read",
                DataShareGrant.revoked_at.is_(None),
            )
            statement = statement.where(or_(condition, getattr(model_class, "id").in_(share_ids)))
        else:
            statement = statement.where(condition)

    return statement
