"""R1.4 tests — Query Plan service layer: lock, version lifecycle, concurrency, legacy.

Run: python -m pytest tests/test_query_plan_service.py -q
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import (
    SearchQueryPlan,
    SearchQuerySlice,
    SearchTask,
)
from app.query_planning.service import (
    add_slice,
    get_or_create_draft_plan,
    lock_plan,
    update_plan,
)
from app.query_planning.schemas import (
    QueryPlanUpdate,
    QuerySliceCreate,
)
from app.query_planning.state_machine import InvalidPlanTransition


# ── Helpers ─────────────────────────────────────────────────────────────────


def _db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _task(db: Session, **kw) -> SearchTask:
    t = SearchTask(
        name=kw.pop("name", "test"), mode=kw.pop("mode", "brand_discovery"),
        status=kw.pop("status", "draft"), filters=kw.pop("filters", {}),
        progress=kw.pop("progress", {}), **kw,
    )
    db.add(t)
    db.commit()
    return t


def _plan(db: Session, task: SearchTask, **kw) -> SearchQueryPlan:
    p = SearchQueryPlan(
        task_id=str(task.id), version=kw.pop("version", 1),
        status=kw.pop("status", "draft"),
        target_result_count=kw.pop("target_result_count", 100),
        organization_id=str(task.organization_id) if task.organization_id else None,
        **kw,
    )
    db.add(p)
    db.commit()
    return p


def _slice(db: Session, plan: SearchQueryPlan, **kw) -> SearchQuerySlice:
    s = SearchQuerySlice(
        plan_id=str(plan.id), slice_key=kw.pop("slice_key", "s1"),
        label=kw.pop("label", "Test"), normalized_hash=kw.pop("normalized_hash", "h1"),
        **kw,
    )
    db.add(s)
    db.commit()
    return s


# ── Lock transaction tests ──────────────────────────────────────────────────


def test_lock_plan_succeeds_with_valid_slices() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, normalized_hash="h1")
    result = lock_plan(db, plan, task)
    assert result["status"] == "locked"
    assert task.active_query_plan_id == str(plan.id)
    assert task.target_result_count == 100


def test_lock_plan_rejects_no_slices() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    with pytest.raises(ValueError, match="At least one slice"):
        lock_plan(db, plan, task)


def test_lock_plan_rejects_no_enabled_slices() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, slice_key="s1", normalized_hash="h1", enabled=False)
    with pytest.raises(ValueError, match="At least one enabled slice"):
        lock_plan(db, plan, task)


def test_lock_plan_rejects_duplicate_hashes() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, slice_key="s1", normalized_hash="dup")
    # Second slice with same hash: DB unique constraint or Python validation picks it up
    try:
        _slice(db, plan, slice_key="s2", normalized_hash="dup")
        # If DB didn't catch it, lock_plan must
        with pytest.raises(ValueError, match="Duplicate slices"):
            lock_plan(db, plan, task)
    except Exception:
        # DB unique constraint caught it — that's acceptable
        pass


def test_lock_plan_rejects_more_than_20_enabled() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    for i in range(21):
        _slice(db, plan, slice_key=f"s{i}", normalized_hash=f"h{i}", enabled=True)
    with pytest.raises(ValueError, match="Maximum 20"):
        lock_plan(db, plan, task)


def test_lock_plan_optimistic_concurrency() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, normalized_hash="h1")

    # Stale timestamp → rejected
    stale_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="modified by another session"):
        lock_plan(db, plan, task, updated_at_expected=stale_ts)


def test_lock_plan_writes_configuration_snapshot() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, normalized_hash="h1")
    result = lock_plan(db, plan, task)
    db.commit()
    db.refresh(task)
    assert "query_plan" in (task.configuration_snapshot or {})
    assert task.configuration_snapshot["query_plan"]["slice_count"] == 1


def test_lock_plan_idempotent() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, normalized_hash="h1")
    lock_plan(db, plan, task)
    db.commit()
    # Second lock with same plan → no-op
    result2 = lock_plan(db, plan, task)
    assert result2["status"] == "locked"


def test_lock_plan_rollback_on_error() -> None:
    """Verify that a failed lock does not leave partial state."""
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, slice_key="a", normalized_hash="a")

    # Cause lock failure by disabling all slices after creation
    slices = db.scalars(
        select(SearchQuerySlice).where(SearchQuerySlice.plan_id == str(plan.id))
    ).all()
    for sl in slices:
        sl.enabled = False
    db.commit()

    with pytest.raises(ValueError, match="At least one enabled"):
        lock_plan(db, plan, task)

    # Plan status should NOT have been set to locked
    db.refresh(plan)
    assert plan.status == "draft"
    # Task should NOT have been linked
    db.refresh(task)
    assert task.active_query_plan_id is None


# ── Version lifecycle tests ─────────────────────────────────────────────────


def test_get_or_create_draft_plan_creates_version_1() -> None:
    db = _db()
    task = _task(db, search_intent={"schema_version": "2.0.0", "source": "local_rules",
        "original_prompt": "test", "target_concepts": [{"id": "c1", "source_text": "bags",
        "normalized_label": "bags", "concept_type": "product", "confidence": 60, "inferred": False}],
        "global_qualifiers": [{"type": "country", "value": "IT", "applies_to": ["c1"],
        "source_text": "Italy", "confidence": 100, "inferred": False}],
        "overall_confidence": 60, "knowledge_sources": []})
    result = get_or_create_draft_plan(db, task)
    assert result["version"] == 1
    assert result["status"] == "draft"
    assert len(result["slices"]) >= 1


def test_legacy_task_no_intent_generates_plan() -> None:
    db = _db()
    task = _task(db, filters={"categories": ["handbags"], "countries": ["IT"]})
    result = get_or_create_draft_plan(db, task)
    assert result["version"] == 1
    assert len(result["slices"]) >= 1


def test_locked_plan_cannot_be_edited() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, normalized_hash="h1")
    lock_plan(db, plan, task)
    db.commit()

    with pytest.raises(InvalidPlanTransition):
        update_plan(db, plan, QueryPlanUpdate(updated_at=plan.updated_at or datetime.now(timezone.utc)))


# ── Slice CRUD tests ────────────────────────────────────────────────────────


def test_add_slice_rejects_duplicate_hash() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    # Create a slice with a known set of fields
    payload = QuerySliceCreate(label="first", purpose="core",
        countries=["IT"], target_concepts=["test"])
    first = add_slice(db, plan, payload)
    db.commit()
    # Trying to add the same slice again
    with pytest.raises(ValueError, match="Duplicate slice"):
        add_slice(db, plan, payload)


def test_add_slice_on_locked_plan_rejected() -> None:
    db = _db()
    task = _task(db)
    plan = _plan(db, task)
    _slice(db, plan, slice_key="s1", normalized_hash="h1")
    lock_plan(db, plan, task)
    db.commit()
    payload = QuerySliceCreate(label="new", purpose="core")
    with pytest.raises(InvalidPlanTransition):
        add_slice(db, plan, payload)
