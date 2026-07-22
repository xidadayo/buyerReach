from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.security import can_assign_role, can_manage_user
from app.modules.models import Role, User
from app.modules.schemas import UserCreate
from app.modules.services import create_user, list_roles, list_users


def database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_user_manager_cannot_see_or_assign_more_privileged_role() -> None:
    with database() as db:
        admin_role = Role(name="admin", permissions={"admin": ["*"]})
        manager_role = Role(
            name="sales_manager",
            permissions={"users": ["read", "write"], "tasks": ["read"]},
        )
        viewer_role = Role(name="limited_viewer", permissions={"tasks": ["read"]})
        db.add_all([admin_role, manager_role, viewer_role])
        db.flush()

        admin = User(
            name="Admin",
            email="admin@example.com",
            password_hash="unused",
            role_id=admin_role.id,
        )
        manager = User(
            name="Manager",
            email="manager@example.com",
            password_hash="unused",
            role_id=manager_role.id,
        )
        viewer = User(
            name="Viewer",
            email="viewer@example.com",
            password_hash="unused",
            role_id=viewer_role.id,
        )
        db.add_all([admin, manager, viewer])
        db.flush()

        result = list_users(db, 1, 50, actor=manager)
        assert {item["email"] for item in result["items"]} == {
            "manager@example.com",
            "viewer@example.com",
        }
        assert can_manage_user(db, manager, admin) is False
        assert can_assign_role(db, manager, admin_role.id) is False
        assert can_assign_role(db, manager, viewer_role.id) is True


def test_admin_can_see_every_user_and_role() -> None:
    with database() as db:
        admin_role = Role(name="admin", permissions={"admin": ["*"]})
        viewer_role = Role(name="limited_viewer", permissions={"tasks": ["read"]})
        db.add_all([admin_role, viewer_role])
        db.flush()
        admin = User(
            name="Admin",
            email="admin@example.com",
            password_hash="unused",
            role_id=admin_role.id,
        )
        viewer = User(
            name="Viewer",
            email="viewer@example.com",
            password_hash="unused",
            role_id=viewer_role.id,
        )
        db.add_all([admin, viewer])
        db.flush()

        assert list_users(db, 1, 50, actor=admin)["total"] == 2
        assert list_roles(db, 1, 50, actor=admin)["total"] == 2


def test_created_user_inherits_creator_organization() -> None:
    from uuid import uuid4

    organization_id = uuid4()
    with database() as db:
        created = create_user(
            db,
            UserCreate(
                name="Sales",
                email="sales@example.com",
                password="strong-password",
            ),
            organization_id=organization_id,
        )
        assert created.organization_id == organization_id
