"""Centralized authorization decision entry points.

All API routes and service functions MUST use these instead of ad-hoc checks.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz.context import AuthorizationContext
from app.authz.errors import forbidden, not_found
from app.authz.scope import scope_condition


def authorize(ctx: AuthorizationContext, operation: str) -> None:
    """Raise 403 if the context does not have the given operation permission."""
    if not ctx.has_permission(operation):
        raise forbidden(f"缺少权限: {operation}")


def load_scoped_entity(
    db: Session,
    model_class,
    entity_id: UUID,
    ctx: AuthorizationContext,
    *,
    resource: str,
    owner_column_name: str = "owner_id",
    org_column_name: str = "organization_id",
    unit_column_name: str = "department_id",
    not_found_message: str = "记录不存在",
) -> object:
    """Load a single entity, verifying organization ownership and data scope.

    Returns the entity or raises 404 (never 403, to prevent info leaks).
    """
    entity = db.get(model_class, entity_id)
    if entity is None:
        raise not_found(not_found_message)

    # Check organization boundary
    org_attr = getattr(entity, org_column_name, None)
    entity_org = str(org_attr) if org_attr is not None else None
    ctx_org = str(ctx.organization_id) if ctx.organization_id is not None else None
    is_platform_admin = "admin:*" in ctx.permissions
    if not is_platform_admin and (entity_org is None or ctx_org is None or entity_org != ctx_org):
        raise not_found(not_found_message)

    # Check data scope
    scope = ctx.data_scope_for(resource)
    if scope == "all" and is_platform_admin:
        return entity
    if scope == "organization":
        return entity

    # For self/unit/unit_and_children: check owner or unit
    if scope == "self":
        owner_attr = getattr(entity, owner_column_name, None)
        if owner_attr is None or str(owner_attr) != str(ctx.user_id):
            raise not_found(not_found_message)
    elif scope in ("unit", "unit_and_children"):
        unit_attr = getattr(entity, unit_column_name, None)
        if unit_attr is not None and ctx.organization_unit_id is not None:
            if scope == "unit":
                if str(unit_attr) != str(ctx.organization_unit_id):
                    raise not_found(not_found_message)
            elif scope == "unit_and_children":
                from app.authz.hierarchy import descendant_ids
                allowed = descendant_ids(db, str(ctx.organization_unit_id))
                if str(unit_attr) not in allowed:
                    raise not_found(not_found_message)
        else:
            # Fallback to owner check
            owner_attr = getattr(entity, owner_column_name, None)
            if owner_attr is None or str(owner_attr) != str(ctx.user_id):
                raise not_found(not_found_message)

    return entity


def authorize_batch(
    db: Session,
    model_class,
    entity_ids: list[UUID],
    ctx: AuthorizationContext,
    *,
    resource: str,
    owner_column_name: str = "owner_id",
    org_column_name: str = "organization_id",
    unit_column_name: str = "department_id",
) -> list:
    """Verify ALL entity_ids are within scope. Returns the loaded entities.

    Raises 404 if any ID is outside scope (batch reject — no silent skip).
    """
    owner_col = getattr(model_class, owner_column_name)
    org_col = getattr(model_class, org_column_name)

    if not entity_ids:
        return []

    id_col = getattr(model_class, "id")
    stmt = select(model_class).where(id_col.in_(entity_ids))

    # Apply data scope
    condition = scope_condition(
        db, ctx, resource,
        owner_column=owner_col,
        org_column=org_col,
        unit_column=getattr(model_class, unit_column_name, None),
    )
    if condition is not None:
        stmt = stmt.where(condition)

    entities = list(db.scalars(stmt).all())

    # Batch reject: if any requested ID is missing, reject entire batch
    found_ids = {str(getattr(e, "id")) for e in entities}
    for eid in entity_ids:
        if str(eid) not in found_ids:
            raise not_found(f"记录不存在或无权限访问: {eid}")

    return entities
