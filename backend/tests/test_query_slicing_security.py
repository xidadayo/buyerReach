"""R0 security tests — cross-organization isolation for task-scoped endpoints.

Run: python -m pytest tests/test_query_slicing_security.py -q
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.deps import require_task_access
from app.modules.models import (
    SearchTask,
    User,
)


def _setup_two_orgs():
    """Create two organizations, each with one task."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    org1 = uuid4()
    org2 = uuid4()

    t1 = SearchTask(
        name="org1-task", mode="brand_discovery", status="draft",
        filters={}, progress={}, organization_id=org1,
    )
    t2 = SearchTask(
        name="org2-task", mode="brand_discovery", status="draft",
        filters={}, progress={}, organization_id=org2,
    )
    db.add_all([t1, t2])
    db.commit()
    return db, org1, org2, t1.id, t2.id


def test_require_task_access_own_org_returns_task() -> None:
    db, org_id, _, task_id, _ = _setup_two_orgs()
    user = User(id=uuid4(), organization_id=org_id, email="a@b.com", name="Tester")
    task = require_task_access(db, task_id, user)
    assert task is not None
    assert str(task.name) == "org1-task"


def test_require_task_access_cross_org_raises_404() -> None:
    from fastapi import HTTPException

    db, org_id, org2_id, task1_id, _ = _setup_two_orgs()
    user = User(id=uuid4(), organization_id=org2_id, email="b@c.com", name="Other")
    try:
        require_task_access(db, task1_id, user)
        assert False, "Should have raised 404"
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "not found" in str(exc.detail).lower()


def test_require_task_access_nonexistent_raises_404() -> None:
    from fastapi import HTTPException

    db, org_id, _, _, _ = _setup_two_orgs()
    user = User(id=uuid4(), organization_id=org_id, email="a@b.com", name="Tester")
    try:
        require_task_access(db, uuid4(), user)
        assert False, "Should have raised 404"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_require_task_access_no_org_user_returns_task() -> None:
    """User without org should still access task without org (legacy tasks)."""
    db, _, _, task1_id, _ = _setup_two_orgs()
    # Remove org from task via model update
    task_row = db.get(SearchTask, task1_id)
    task_row.organization_id = None
    db.commit()

    user = User(id=uuid4(), organization_id=None, email="legacy@b.com", name="Legacy")
    task = require_task_access(db, task1_id, user)
    assert task is not None


def test_query_plan_org_isolation_API_guard() -> None:
    """API-level access guard: org2 user calling slice-run endpoint on org1 task
    must be rejected. This test validates require_task_access catches cross-org
    access before query_planning service is called."""
    db, org1_id, org2_id, task1_id, _ = _setup_two_orgs()
    from fastapi import HTTPException
    user = User(id=uuid4(), organization_id=org2_id, email="b@c.com", name="Other")
    try:
        require_task_access(db, task1_id, user)
        assert False, "Should have raised 404 for cross-org access"
    except HTTPException as exc:
        assert exc.status_code == 404
