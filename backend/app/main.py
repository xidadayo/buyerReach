from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from app.api.v1.auth import auth_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, engine


def _seed_defaults() -> None:
    """Create a default organization, admin role, and admin user on first boot."""
    from app.core.security import ROLE_PERMISSIONS, hash_password
    from app.modules.models import Organization, Role, User

    with SessionLocal() as db:
        # Organization
        org = db.scalar(select(Organization).where(Organization.name == "Default"))
        if org is None:
            org = Organization(name="Default", status="active")
            db.add(org)
            db.flush()

        roles: dict[str, Role] = {}
        for role_name, permissions in ROLE_PERMISSIONS.items():
            role = db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name)
                db.add(role)
                db.flush()
            role.permissions = _group_permissions(permissions)
            roles[role_name] = role

        # Default admin user
        admin_email = "admin@buyerreach.local"
        admin = db.scalar(select(User).where(User.email == admin_email))
        if admin is None:
            admin = User(
                organization_id=org.id,
                role_id=roles["admin"].id,
                email=admin_email,
                name="Admin",
                password_hash=hash_password("admin123"),
                status="active",
            )
            db.add(admin)

        db.commit()


def _validate_runtime_settings() -> None:
    if settings.app_env.lower() != "production":
        return
    insecure = {
        "JWT_SECRET": settings.jwt_secret == "change-me-in-production" or settings.jwt_secret == "change-me",
        "ENCRYPTION_KEY": settings.encryption_key == "change-me-32-byte-minimum" or settings.encryption_key == "change-me",
    }
    missing = [name for name, is_insecure in insecure.items() if is_insecure]
    if missing:
        raise RuntimeError(f"Production settings are insecure: {', '.join(missing)}")


def _group_permissions(permissions: set[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for permission in sorted(permissions):
        if ":" not in permission:
            continue
        resource, action = permission.split(":", 1)
        grouped.setdefault(resource, []).append(action)
    return grouped


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_runtime_settings()
    _seed_defaults()
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0"}


@app.get("/ready")
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
