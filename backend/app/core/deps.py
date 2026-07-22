from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token, get_role_permissions, get_user_by_id
from app.modules.models import SearchTask, User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token type must be access")

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    user = get_user_by_id(db, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def _current_user_id(user: User = Depends(get_current_user)) -> UUID:
    return user.id


# ---------------------------------------------------------------------------
# Role / permission guards
# ---------------------------------------------------------------------------


class RequirePermission:
    """FastAPI dependency factory that checks the current user has a permission."""

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        role_name = "viewer"
        role = None
        if user.role_id:
            from app.modules.models import Role
            role = db.get(Role, user.role_id)
            if role:
                role_name = role.name
        permissions = get_role_permissions(role_name)
        if role:
            permissions = permissions | _flatten_permissions(role.permissions or {})
        if "admin:*" not in permissions and self.permission not in permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user


def require_permission(permission: str):
    return Depends(RequirePermission(permission))


def _flatten_permissions(value: dict) -> set[str]:
    permissions: set[str] = set()
    for resource, actions in value.items():
        if isinstance(actions, list):
            permissions.update(f"{resource}:{action}" for action in actions)
        elif isinstance(actions, str):
            permissions.add(f"{resource}:{actions}")
    return permissions


# ── Organization-scoped task authorization ───────────────────────────────────


def require_task_access(
    db: Session,
    task_id: UUID,
    user: User,
) -> SearchTask:
    """Load a task row, verify organization ownership. 404 when absent or cross-org."""
    task = db.get(SearchTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search task not found")
    task_org = str(task.organization_id) if task.organization_id is not None else None
    user_org = str(user.organization_id) if user.organization_id is not None else None
    if task_org is not None and task_org != user_org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search task not found")
    return task
