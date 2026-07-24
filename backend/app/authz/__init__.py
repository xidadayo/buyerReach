"""BuyerReach centralized authorization module.

See docs/organization-authorization-production-plan-v1.md for the authoritative spec.
"""

from app.authz.catalog import (
    PERMISSION_CATALOG,
    PERMISSION_VERSION,
    V1_COMPAT_MAP,
    flatten_permissions,
    permission_display_name,
)
from app.authz.context import (
    AuthorizationContext,
    authorization_context,
)
from app.authz.errors import (
    forbidden,
    not_found,
)
from app.authz.policy import (
    authorize,
    authorize_batch,
    load_scoped_entity,
)
from app.authz.scope import (
    apply_scope,
    scope_condition,
)

__all__ = [
    "PERMISSION_CATALOG",
    "PERMISSION_VERSION",
    "V1_COMPAT_MAP",
    "AuthorizationContext",
    "apply_scope",
    "authorization_context",
    "authorize",
    "authorize_batch",
    "flatten_permissions",
    "forbidden",
    "load_scoped_entity",
    "not_found",
    "permission_display_name",
    "scope_condition",
]
