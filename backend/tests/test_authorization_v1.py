from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.authz.context import AuthorizationContext, authorization_context
from app.authz.hierarchy import descendant_ids
from app.authz.policy import authorize_batch, load_scoped_entity
from app.authz.scope import apply_scope
from app.core.database import Base
from app.modules.models import Organization, OrganizationUnit, SearchTask, User
from app.modules.services import assign_business_data


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _ctx(user_id, org_id, unit_id, scope: str) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=user_id,
        organization_id=org_id,
        organization_unit_id=unit_id,
        permissions=frozenset({"tasks:read"}),
        data_scopes={"tasks": scope},
    )


def _tree(db: Session):
    org = Organization(name="Tenant")
    other_org = Organization(name="Other")
    db.add_all([org, other_org])
    db.flush()
    root = OrganizationUnit(
        organization_id=org.id, name="Root", code="root", unit_type="company", path="/", depth=0
    )
    db.add(root)
    db.flush()
    team = OrganizationUnit(
        organization_id=org.id,
        parent_id=root.id,
        name="Team",
        code="team",
        path=f"/{root.id}/",
        depth=1,
    )
    sibling = OrganizationUnit(
        organization_id=org.id,
        parent_id=root.id,
        name="Sibling",
        code="sibling",
        path=f"/{root.id}/",
        depth=1,
    )
    db.add_all([team, sibling])
    db.flush()
    return org, other_org, root, team, sibling


def test_descendant_ids_uses_uuid_ancestor_path(db: Session) -> None:
    _, _, root, team, sibling = _tree(db)
    assert set(descendant_ids(db, str(root.id))) == {str(root.id), str(team.id), str(sibling.id)}
    assert descendant_ids(db, str(team.id)) == [str(team.id)]


@pytest.mark.parametrize("scope,expected", [("self", 1), ("unit", 1), ("unit_and_children", 3), ("organization", 3)])
def test_apply_scope_matrix(db: Session, scope: str, expected: int) -> None:
    org, other_org, root, team, sibling = _tree(db)
    actor_id = uuid4()
    rows = [
        SearchTask(name="mine", mode="exact_brand", organization_id=org.id, department_id=root.id, owner_id=actor_id),
        SearchTask(name="child", mode="exact_brand", organization_id=org.id, department_id=team.id, owner_id=uuid4()),
        SearchTask(name="sibling", mode="exact_brand", organization_id=org.id, department_id=sibling.id, owner_id=uuid4()),
        SearchTask(name="other", mode="exact_brand", organization_id=other_org.id, owner_id=uuid4()),
    ]
    db.add_all(rows)
    db.commit()
    statement = apply_scope(select(SearchTask), SearchTask, db, _ctx(actor_id, org.id, root.id, scope), "tasks")
    assert len(list(db.scalars(statement))) == expected


def test_entity_and_batch_authorization_fail_closed(db: Session) -> None:
    org, other_org, root, _, _ = _tree(db)
    actor_id = uuid4()
    own = SearchTask(name="own", mode="exact_brand", organization_id=org.id, department_id=root.id, owner_id=actor_id)
    foreign = SearchTask(name="foreign", mode="exact_brand", organization_id=other_org.id, owner_id=uuid4())
    db.add_all([own, foreign])
    db.commit()
    ctx = _ctx(actor_id, org.id, root.id, "organization")
    assert load_scoped_entity(db, SearchTask, own.id, ctx, resource="tasks") is own
    with pytest.raises(HTTPException) as exc_info:
        authorize_batch(db, SearchTask, [own.id, foreign.id], ctx, resource="tasks")
    assert exc_info.value.status_code == 404


def test_all_scope_is_tenant_bound_for_non_platform_role(db: Session) -> None:
    org, other_org, root, _, _ = _tree(db)
    actor_id = uuid4()
    db.add_all(
        [
            SearchTask(name="own", mode="exact_brand", organization_id=org.id, owner_id=actor_id),
            SearchTask(name="foreign", mode="exact_brand", organization_id=other_org.id, owner_id=uuid4()),
        ]
    )
    db.commit()
    statement = apply_scope(select(SearchTask), SearchTask, db, _ctx(actor_id, org.id, root.id, "all"), "tasks")
    assert [task.name for task in db.scalars(statement)] == ["own"]


def test_missing_role_scope_fails_closed_to_self(db: Session) -> None:
    org, _, root, _, _ = _tree(db)
    actor = User(
        organization_id=org.id,
        organization_unit_id=root.id,
        email="member@example.com",
        name="Member",
        password_hash="unused",
    )
    db.add(actor)
    db.commit()
    ctx = authorization_context(db, actor)
    assert ctx.data_scope_for("tasks") == "self"
    assert ctx.data_scope_for("brands") == "self"


def test_assignment_requires_active_owner_in_target_unit(db: Session) -> None:
    org, _, root, team, sibling = _tree(db)
    actor = User(
        organization_id=org.id,
        organization_unit_id=root.id,
        email="admin@example.com",
        name="Admin",
        password_hash="unused",
    )
    valid_owner = User(
        organization_id=org.id,
        organization_unit_id=team.id,
        email="owner@example.com",
        name="Owner",
        password_hash="unused",
    )
    wrong_owner = User(
        organization_id=org.id,
        organization_unit_id=sibling.id,
        email="wrong@example.com",
        name="Wrong",
        password_hash="unused",
    )
    task = SearchTask(
        name="move",
        mode="exact_brand",
        organization_id=org.id,
        department_id=root.id,
        owner_id=actor.id,
    )
    db.add_all([actor, valid_owner, wrong_owner, task])
    db.flush()

    with pytest.raises(ValueError, match="target unit"):
        assign_business_data(
            db,
            resource="tasks",
            entities=[task],
            target_unit=team,
            target_owner=wrong_owner,
            actor=actor,
            reason="wrong unit",
        )

    result = assign_business_data(
        db,
        resource="tasks",
        entities=[task],
        target_unit=team,
        target_owner=valid_owner,
        actor=actor,
        reason="handoff",
    )
    assert result["assigned"] == 1
    assert task.department_id == team.id
    assert task.owner_id == valid_owner.id
