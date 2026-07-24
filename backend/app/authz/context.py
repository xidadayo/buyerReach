"""AuthorizationContext — the single source of truth for a user's permissions and scope."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.authz.catalog import (
    DATA_SCOPED_RESOURCES,
    V1_COMPAT_MAP,
    flatten_permissions,
)
from app.core.security import get_role_permissions


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    organization_id: UUID | None
    organization_unit_id: UUID | None
    permissions: frozenset[str]
    data_scopes: dict[str, str]  # resource → scope level
    permission_version: int = 1

    def has_permission(self, operation: str) -> bool:
        """Check if this context has a specific operation permission."""
        if "admin:*" in self.permissions:
            return True
        return operation in self.permissions

    def data_scope_for(self, resource: str) -> str:
        """Return the data scope for a resource. Defaults to 'self'."""
        return self.data_scopes.get(resource, "self")


def authorization_context(db: Session, user) -> AuthorizationContext:
    """Build the AuthorizationContext for the current user.

    Combines: hardcoded role permissions + database-stored custom permissions.
    """
    from app.modules.models import Role

    role_name = "viewer"
    permissions: set[str] = set(get_role_permissions(role_name))
    data_scopes: dict[str, str] = {}

    role = db.get(Role, user.role_id) if user.role_id else None
    if role is not None and (
        role.status != "active"
        or (role.organization_id is not None and role.organization_id != user.organization_id)
    ):
        role = None
        permissions = set()
    if role is not None:
        role_name = role.name
        # Merge hardcoded permissions
        legacy_permissions = get_role_permissions(role_name)
        permissions = set()
        for permission in legacy_permissions:
            permissions.add(permission)
            permissions.update(V1_COMPAT_MAP.get(permission, []))
        # Merge database-stored permissions (v1 format)
        if isinstance(role.permissions, dict):
            permissions = permissions | flatten_permissions(role.permissions)
        # Load data scopes from role
        if isinstance(role.data_scopes, dict):
            data_scopes = role.data_scopes
        # Missing scope configuration fails closed. An administrator can widen
        # it explicitly after reviewing the role.
        if not data_scopes:
            for resource in DATA_SCOPED_RESOURCES:
                data_scopes[resource] = "self"
    else:
        for resource in DATA_SCOPED_RESOURCES:
            data_scopes[resource] = "self"

    return AuthorizationContext(
        user_id=user.id,
        organization_id=user.organization_id,
        organization_unit_id=getattr(user, "organization_unit_id", None),
        permissions=frozenset(permissions),
        data_scopes=data_scopes,
        permission_version=getattr(role, "permission_version", 1) if role else 1,
    )


def is_super_admin(ctx: AuthorizationContext) -> bool:
    """True if the context has the platform-level admin wildcard."""
    return "admin:*" in ctx.permissions
