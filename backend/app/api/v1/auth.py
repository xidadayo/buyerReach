from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.models import User
from app.modules.services import audit

auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email:
            raise ValueError("Email must contain @")
        return email


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserProfile(BaseModel):
    id: str
    email: str
    name: str
    role: str | None
    organization_name: str | None
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@auth_router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = authenticate_user(db, payload.email, payload.password)
    if user is None:
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    role_name = "viewer"
    if user.role_id:
        from app.modules.models import Role
        role = db.get(Role, user.role_id)
        if role:
            role_name = role.name

    org_name = None
    if user.organization_id:
        from app.modules.models import Organization
        org = db.get(Organization, user.organization_id)
        if org:
            org_name = org.name

    extra = {
        "email": user.email,
        "name": user.name,
        "role": role_name,
        "organization_id": str(user.organization_id) if user.organization_id else None,
    }
    access_token = create_access_token(str(user.id), extra)
    refresh_token = create_refresh_token(str(user.id))

    audit(db, "auth.login", "user", str(user.id))
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": role_name,
            "organization_name": org_name,
            "status": user.status,
        },
    }


@auth_router.post("/refresh", response_model=RefreshResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    from jose import JWTError

    try:
        claims = decode_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    if claims.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token type must be refresh")

    user_id = claims.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    from app.core.security import get_user_by_id
    user = get_user_by_id(db, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    role_name = "viewer"
    if user.role_id:
        from app.modules.models import Role
        role = db.get(Role, user.role_id)
        if role:
            role_name = role.name

    extra = {
        "email": user.email,
        "name": user.name,
        "role": role_name,
        "organization_id": str(user.organization_id) if user.organization_id else None,
    }
    access_token = create_access_token(str(user.id), extra)

    return {"access_token": access_token, "token_type": "bearer"}


@auth_router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    role_name = "viewer"
    if user.role_id:
        from app.modules.models import Role
        role = db.get(Role, user.role_id)
        if role:
            role_name = role.name

    org_name = None
    if user.organization_id:
        from app.modules.models import Organization
        org = db.get(Organization, user.organization_id)
        if org:
            org_name = org.name

    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": role_name,
        "organization_name": org_name,
        "status": user.status,
    }


@auth_router.post("/change-password")
def change_password(payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    user.password_hash = hash_password(payload.new_password)
    db.commit()

    audit(db, "auth.change_password", "user", str(user.id))
    return {"message": "Password changed successfully"}
