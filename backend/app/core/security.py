from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.models import User

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ---------------------------------------------------------------------------
# Roles & permissions
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        # Full access to everything
        "admin:*",
        "brands:read", "brands:write", "brands:export",
        "contacts:read", "contacts:write", "contacts:export",
        "emails:read", "emails:write", "emails:verify", "emails:export",
        "tasks:read", "tasks:write", "tasks:execute",
        "providers:read", "providers:write",
        "audit:read",
        "import:execute", "export:execute",
        "dedup:execute",
        "blacklist:read", "blacklist:write",
        "settings:read", "settings:write",
        "roles:read", "roles:write",
        "users:read", "users:write",
        "tags:read", "tags:write",
        "custom_fields:read", "custom_fields:write",
    },
    "operator": {
        "brands:read", "brands:write", "brands:export",
        "contacts:read", "contacts:write", "contacts:export",
        "emails:read", "emails:write", "emails:verify", "emails:export",
        "tasks:read", "tasks:write", "tasks:execute",
        "import:execute", "export:execute",
        "dedup:execute",
        "settings:read",
        "tags:read", "tags:write",
        "custom_fields:read", "custom_fields:write",
    },
    "viewer": {
        "brands:read",
        "contacts:read",
        "emails:read",
        "tasks:read",
        "tags:read",
        "custom_fields:read",
    },
}


def get_role_permissions(role_name: str) -> set[str]:
    return ROLE_PERMISSIONS.get(role_name, set())


def has_permission(role_name: str, permission: str) -> bool:
    perms = get_role_permissions(role_name)
    if "admin:*" in perms:
        return True
    return permission in perms


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_refresh_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token.  Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    from sqlalchemy import select

    user = db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    if user is None:
        return None
    now = datetime.now(timezone.utc)
    if user.locked_until and user.locked_until > now:
        return None
    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_login_attempts = 0
        return None
    user.failed_login_attempts = 0
    user.locked_until = None
    return user


def get_user_by_id(db: Session, user_id: str) -> User | None:
    from sqlalchemy import select

    return db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
