"""Query-planning service layer — handles API use-cases.

Calls into the state machine, normalization, and repository layers.
All mutations go through a database session from the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.models import (
    SearchQueryPlan,
    SearchQuerySlice,
    SearchQuerySliceRun,
    SearchTask,
)
from app.query_planning.generator import generate_slices_from_intent, plan_summary
from app.query_planning.schemas import (
    QueryPlanUpdate,
    QuerySliceCreate,
    QuerySliceUpdate,
)
from app.query_planning.state_machine import (
    PlanTransitionContext,
    InvalidPlanTransition,
    transition_plan,
)
from app.query_planning.normalization import (
    slice_normalized_hash,
)


# ── Query Plan CRUD ─────────────────────────────────────────────────────────


def get_or_create_draft_plan(
    db: Session,
    task: SearchTask,
    *,
    generator_type: str = "local_rules",
    generator_version: str | None = None,
    preset: str = "balanced",
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Return the existing draft plan for a task, or create one from the task's intent."""
    existing = db.scalar(
        select(SearchQueryPlan)
        .where(
            SearchQueryPlan.task_id == task.id,
            SearchQueryPlan.status == "draft",
        )
        .order_by(SearchQueryPlan.version.desc())
        .limit(1)
    )
    if existing is not None:
        return _plan_to_dict(db, existing)

    # Legacy task without intent — generate a compatibility intent
    intent = task.search_intent or {}
    filters = task.filters or {}
    if not intent.get("target_concepts"):
        from app.pipeline.concepts import intent_from_legacy

        intent_obj = intent_from_legacy(
            filters.get("original_prompt", task.name),
            filters.get("categories", []),
            filters.get("countries", []),
            filters.get("company_types", []),
            filters.get("category_match_mode", "any"),
        )
        intent = intent_obj.model_dump(mode="json")

    slices = generate_slices_from_intent(
        intent, generator_type=generator_type, generator_version=generator_version, preset=preset
    )

    plan = SearchQueryPlan(
        id=uuid4(),
        task_id=task.id,
        organization_id=str(task.organization_id) if task.organization_id else None,
        version=1,
        generator_type=generator_type,
        generator_version=generator_version,
        status="draft",
        target_result_count=task.target_result_count or int(filters.get("brand_limit", 100)),
        candidate_fetch_limit=task.candidate_fetch_limit,
        max_provider_calls=task.max_provider_calls,
        budget_limit=task.budget_limit,
        repeat_mode=task.repeat_mode or "new_only",
        created_by=actor_id,
    )
    db.add(plan)
    db.flush()

    for sl in slices:
        _persist_slice(db, plan, sl)

    return _plan_to_dict(db, plan)


def get_plan(db: Session, task_id: UUID, version: int | None = None) -> dict | None:
    stmt = select(SearchQueryPlan).where(SearchQueryPlan.task_id == task_id)
    if version is not None:
        stmt = stmt.where(SearchQueryPlan.version == version)
    else:
        stmt = stmt.order_by(SearchQueryPlan.version.desc())
    plan = db.scalar(stmt.limit(1))
    return _plan_to_dict(db, plan) if plan else None


def update_plan(
    db: Session,
    plan: SearchQueryPlan,
    payload: QueryPlanUpdate,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Apply an optimistic-concurrency update. Raises on conflict."""
    if plan.status not in {"draft", "review"}:
        raise InvalidPlanTransition("Only draft/review plans can be edited")

    # Optimistic concurrency
    plan_updated = plan.updated_at
    if plan_updated is not None and payload.updated_at is not None:
        plan_ts = plan_updated.replace(microsecond=0)
        payload_ts = payload.updated_at.replace(microsecond=0)
        if plan_ts != payload_ts:
            raise ValueError(
                "The plan was modified by another session. Please refresh and re-apply your changes."
            )

    if payload.target_result_count is not None:
        plan.target_result_count = payload.target_result_count
    if payload.candidate_fetch_limit is not None:
        plan.candidate_fetch_limit = payload.candidate_fetch_limit
    if payload.max_provider_calls is not None:
        plan.max_provider_calls = payload.max_provider_calls
    if payload.budget_limit is not None:
        plan.budget_limit = payload.budget_limit
    if payload.repeat_mode is not None:
        plan.repeat_mode = payload.repeat_mode
    return _plan_to_dict(db, plan)


def lock_plan(
    db: Session,
    plan: SearchQueryPlan,
    task: SearchTask,
    *,
    actor_id: str | None = None,
    updated_at_expected: datetime | None = None,
) -> dict[str, Any]:
    """Lock a plan, freeze configuration, and update TaskVendorPlan — all in the
    caller's transaction. Caller commits once; any failure rolls back everything.

    ``updated_at_expected`` is the optimistic-concurrency sentinel from the client.
    """
    if plan.status == "locked":
        return _plan_to_dict(db, plan)

    # ── Optimistic concurrency check ─────────────────────────────────────
    if updated_at_expected is not None and plan.updated_at is not None:
        plan_ts = plan.updated_at.replace(microsecond=0)
        client_ts = updated_at_expected.replace(microsecond=0)
        if plan_ts != client_ts:
            raise ValueError(
                "The plan was modified by another session. "
                "Please refresh and re-apply your changes."
            )

    # ── Validate slices ──────────────────────────────────────────────────
    plan_id_str = str(plan.id)
    slices = db.scalars(
        select(SearchQuerySlice).where(SearchQuerySlice.plan_id == plan_id_str)
    ).all()

    if not slices:
        raise ValueError("At least one slice is required to lock the plan.")

    enabled_slices = [sl for sl in slices if sl.enabled]
    if not enabled_slices:
        raise ValueError("At least one enabled slice is required to lock the plan.")

    # Duplicate detection
    hashes = [sl.normalized_hash for sl in slices]
    if len(hashes) != len(set(hashes)):
        raise ValueError("Duplicate slices detected. Remove or merge duplicates before locking.")

    # Count cap
    if len(enabled_slices) > 20:
        raise ValueError(f"Maximum 20 enabled slices allowed; found {len(enabled_slices)}.")

    # ── Transition plan → locked ─────────────────────────────────────────
    from app.pipeline.configuration import capture_configuration
    from app.modules.services import ensure_task_vendor_plan, emit

    transition_plan(plan, "locked", PlanTransitionContext(actor_id=actor_id))
    plan.locked_by = actor_id
    plan.locked_at = datetime.now(UTC)

    # ── Freeze configuration snapshot (no secrets) ───────────────────────
    # capture_configuration reads system settings; we call it inside the lock
    # transaction so the snapshot is atomically consistent.
    _, config_snapshot = capture_configuration(db)

    # Add plan metadata to the snapshot
    plan_config = {
        "query_plan_id": str(plan.id),
        "query_plan_version": plan.version,
        "schema_version": plan.schema_version,
        "generator_type": plan.generator_type,
        "generator_version": plan.generator_version,
        "target_result_count": plan.target_result_count,
        "candidate_fetch_limit": plan.candidate_fetch_limit,
        "max_provider_calls": plan.max_provider_calls,
        "budget_limit": plan.budget_limit,
        "repeat_mode": plan.repeat_mode,
        "filter_policy": plan.filter_policy or {},
        "source_policy": plan.source_policy or {},
        "slice_count": len(enabled_slices),
        "locked_at": plan.locked_at.isoformat() if plan.locked_at else None,
    }
    config_snapshot["query_plan"] = plan_config

    task.configuration_snapshot = {
        **(task.configuration_snapshot or {}),
        **config_snapshot,
    }

    # ── Freeze TaskVendorPlan ────────────────────────────────────────────
    ensure_task_vendor_plan(db, task)

    # ── Link plan to task ────────────────────────────────────────────────
    task.active_query_plan_id = str(plan.id)
    task.target_result_count = plan.target_result_count
    task.candidate_fetch_limit = plan.candidate_fetch_limit
    task.max_provider_calls = plan.max_provider_calls
    task.repeat_mode = plan.repeat_mode

    # ── Outbox event within the transaction ──────────────────────────────
    emit(
        db,
        "query_plan.locked",
        {
            "task_id": str(task.id),
            "plan_id": str(plan.id),
            "plan_version": plan.version,
            "slice_count": len(enabled_slices),
            "target_result_count": plan.target_result_count,
        },
    )

    return _plan_to_dict(db, plan)


# ── Slice CRUD ──────────────────────────────────────────────────────────────


def add_slice(
    db: Session,
    plan: SearchQueryPlan,
    payload: QuerySliceCreate,
) -> dict[str, Any]:
    if plan.status not in {"draft", "review"}:
        raise InvalidPlanTransition("Cannot modify slices on a locked plan")

    existing_keys = {
        sl.slice_key
        for sl in db.scalars(
            select(SearchQuerySlice).where(SearchQuerySlice.plan_id == str(plan.id))
        ).all()
    }
    # Generate a unique slice_key
    base_key = f"slice-{payload.purpose}"
    key = base_key
    suffix = 1
    while key in existing_keys:
        key = f"{base_key}-{suffix}"
        suffix += 1

    nhash = slice_normalized_hash(
        countries=payload.countries,
        target_concepts=payload.target_concepts,
        business_types=payload.business_types,
        include_terms=payload.include_terms,
        exclude_terms=payload.exclude_terms,
        match_mode=payload.match_mode,
        purpose=payload.purpose,
    )

    # Check for duplicate hash
    existing_hash = db.scalar(
        select(SearchQuerySlice).where(
            SearchQuerySlice.plan_id == str(plan.id),
            SearchQuerySlice.normalized_hash == nhash,
        )
    )
    if existing_hash is not None:
        raise ValueError(f"Duplicate slice: an identical query direction already exists (slice {existing_hash.slice_key})")

    slice_obj = SearchQuerySlice(
        id=uuid4(),
        plan_id=str(plan.id),
        slice_key=key,
        label=payload.label,
        purpose=payload.purpose,
        target_concept_ids=payload.target_concept_ids,
        countries=payload.countries,
        target_concepts=payload.target_concepts,
        business_types=payload.business_types,
        include_terms=payload.include_terms,
        exclude_terms=payload.exclude_terms,
        match_mode=payload.match_mode,
        priority=payload.priority,
        enabled=payload.enabled,
        origin="user_added",
        reason=payload.reason,
        target_count=payload.target_count,
        candidate_limit=payload.candidate_limit,
        normalized_hash=nhash,
        version=1,
    )
    db.add(slice_obj)
    db.flush()
    return _slice_to_dict(slice_obj)


def update_slice(
    db: Session,
    plan: SearchQueryPlan,
    slice_obj: SearchQuerySlice,
    payload: QuerySliceUpdate,
) -> dict[str, Any]:
    if plan.status not in {"draft", "review"}:
        raise InvalidPlanTransition("Cannot modify slices on a locked plan")

    changed = False
    for field in (
        "label", "purpose", "target_concept_ids", "countries",
        "target_concepts", "business_types", "include_terms",
        "exclude_terms", "match_mode", "priority",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(slice_obj, field, val)
            changed = True
    if payload.enabled is not None:
        slice_obj.enabled = payload.enabled
        changed = True
    if payload.reason is not None:
        slice_obj.reason = payload.reason
        changed = True
    if payload.target_count is not None:
        slice_obj.target_count = payload.target_count
        changed = True
    if payload.candidate_limit is not None:
        slice_obj.candidate_limit = payload.candidate_limit
        changed = True

    if changed:
        slice_obj.origin = "user_modified"
        slice_obj.version += 1
        # Recompute hash
        slice_obj.normalized_hash = slice_normalized_hash(
            countries=slice_obj.countries or [],
            target_concepts=slice_obj.target_concepts or [],
            business_types=slice_obj.business_types or [],
            include_terms=slice_obj.include_terms or [],
            exclude_terms=slice_obj.exclude_terms or [],
            match_mode=slice_obj.match_mode or "any",
            purpose=slice_obj.purpose or "core",
        )

    db.flush()
    return _slice_to_dict(slice_obj)


def delete_slice(
    db: Session,
    plan: SearchQueryPlan,
    slice_obj: SearchQuerySlice,
) -> None:
    if plan.status not in {"draft", "review"}:
        raise InvalidPlanTransition("Cannot delete slices on a locked plan")
    db.delete(slice_obj)


# ── Preview ─────────────────────────────────────────────────────────────────


def preview_plan(
    db: Session,
    prompt: str,
    target_result_count: int = 100,
    countries: list[str] | None = None,
    preset: str = "balanced",
) -> dict[str, Any]:
    """Generate a preview plan without creating a task."""
    from app.pipeline.concepts import intent_from_legacy

    categories = [prompt]  # legacy path treats prompt as category
    intent_obj = intent_from_legacy(
        prompt,
        categories,
        countries or [],
        [],
        "any",
    )

    intent = intent_obj.model_dump(mode="json")
    slices = generate_slices_from_intent(
        intent,
        generator_type="local_rules",
        preset=preset,
    )

    summary = plan_summary(intent, slices)
    estimated_calls = sum(
        3 if sl["purpose"] in {"core", "synonym"} else 2
        for sl in slices
        if sl["enabled"]
    )

    warnings: list[str] = []
    if intent_obj.overall_confidence < 60:
        warnings.append("AI analysis is unavailable; the system generated a basic plan from your input keywords.")
    if not countries:
        warnings.append("No country specified. Provider results may include irrelevant regions.")

    return {
        "intent": intent,
        "slices": slices,
        "summary": summary,
        "estimated_provider_calls": estimated_calls,
        "warnings": warnings,
        "requires_confirmation": True if warnings else False,
        "source": "local_rules",
    }


# ── Slice Runs ──────────────────────────────────────────────────────────────


def list_slice_runs(
    db: Session,
    task_id: UUID,
    slice_id: UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    from app.modules.services import page_result, to_dict

    stmt = select(SearchQuerySliceRun).where(SearchQuerySliceRun.task_id == str(task_id))
    if slice_id is not None:
        stmt = stmt.where(SearchQuerySliceRun.query_slice_id == str(slice_id))
    if status is not None:
        stmt = stmt.where(SearchQuerySliceRun.status == status)

    total = (
        db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    )
    runs = db.scalars(
        stmt.order_by(SearchQuerySliceRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return page_result(
        total, page, page_size,
        [to_dict(r) for r in runs],
    )


# ── Private helpers ─────────────────────────────────────────────────────────


def _plan_to_dict(db: Session, plan: SearchQueryPlan) -> dict[str, Any]:
    from app.modules.services import to_dict

    result = to_dict(plan)
    slices = db.scalars(
        select(SearchQuerySlice)
        .where(SearchQuerySlice.plan_id == str(plan.id))
        .order_by(SearchQuerySlice.priority, SearchQuerySlice.created_at)
    ).all()
    result["slices"] = [to_dict(sl) for sl in slices]
    return result


def _slice_to_dict(slice_obj: SearchQuerySlice) -> dict[str, Any]:
    from app.modules.services import to_dict

    return to_dict(slice_obj)


def _persist_slice(
    db: Session,
    plan: SearchQueryPlan,
    sl: dict[str, Any],
) -> SearchQuerySlice:
    obj = SearchQuerySlice(
        id=uuid4(),
        plan_id=str(plan.id),
        slice_key=sl["slice_key"],
        label=sl["label"][:255],
        purpose=sl.get("purpose", "core"),
        target_concept_ids=sl.get("target_concept_ids", []),
        countries=sl.get("countries", []),
        target_concepts=sl.get("target_concepts", []),
        business_types=sl.get("business_types", []),
        include_terms=sl.get("include_terms", []),
        exclude_terms=sl.get("exclude_terms", []),
        match_mode=sl.get("match_mode", "any"),
        priority=sl.get("priority", 0),
        enabled=sl.get("enabled", True),
        origin=sl.get("origin", "generated"),
        reason=sl.get("reason"),
        target_count=sl.get("target_count"),
        candidate_limit=sl.get("candidate_limit"),
        normalized_hash=sl["normalized_hash"],
        version=1,
    )
    db.add(obj)
    return obj
