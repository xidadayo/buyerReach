from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token, get_user_by_id
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
        from app.authz.context import authorization_context

        ctx = authorization_context(db, user)
        if not ctx.has_permission(self.permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user


def require_permission(permission: str):
    return Depends(RequirePermission(permission))


# ── Organization-scoped task authorization ───────────────────────────────────


def require_task_access(
    db: Session,
    task_id: UUID,
    user: User,
) -> SearchTask:
    """Load a task row, verify organization ownership. 404 when absent or cross-org."""
    from app.authz.context import authorization_context
    from app.authz.policy import load_scoped_entity

    return load_scoped_entity(
        db,
        SearchTask,
        task_id,
        authorization_context(db, user),
        resource="tasks",
        not_found_message="Search task not found",
    )
