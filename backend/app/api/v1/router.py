import io
import json
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.database import SessionLocal
from app.core.deps import require_permission, require_task_access
from app.modules import services
from app.modules.models import (
    AuditLog,
    BatchImport,
    Blacklist,
    Brand,
    Contact,
    CustomField,
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    DomainEvent,
    EmailAddress,
    ProviderConfig,
    Role,
    SearchQueryPlan,
    SearchTask,
    Tag,
    User,
    VendorCredential,
)
from app.modules.schemas import (
    AITaskPlanRead,
    AITaskPlanRequest,
    BatchImportConfirm,
    BlacklistCreate,
    BrandBatchRequest,
    BrandCreate,
    BrandUpdate,
    ContactCreate,
    ContactBatchRequest,
    ContactUpdate,
    CustomFieldCreate,
    CustomFieldUpdate,
    CustomValueUpsert,
    DedupMergeRequest,
    DiscoveryCandidateReject,
    DiscoveryCandidateApprove,
    DiscoveryCandidateBatchRequest,
    DiscoveryCandidateBulkApprove,
    EmailBatchRequest,
    EmailCreate,
    EmailUpdate,
    EmailReviewRequest,
    EmailVerifyRequest,
    ExportRequest,
    ProviderConfigCreate,
    ProviderConfigUpdate,
    RoleCreate,
    RoleUpdate,
    SearchTaskCreate,
    SearchTaskRead,
    SystemSettingsUpdate,
    TagCreate,
    TagUpdate,
    TargetRetryRequest,
    UserCreate,
    UserUpdate,
    VendorCredentialUpdate,
)
from app.modules.tabular import read_rows
from app.shared.enums import TaskStatus
from app.pipeline.state_machine import transition_task
from app.tasks.celery_app import enrich_candidate_industry_job, execute_search_task_job
from app.query_planning.schemas import (
    ContinueNewRequest,
    ContinueNewResponse,
    QueryPlanLockRequest,
    QueryPlanPreviewRequest,
    QueryPlanPreviewResponse,
    QueryPlanRead,
    QueryPlanUpdate,
    QuerySliceCreate,
    QuerySliceRead,
    QuerySliceUpdate,
)
from app.query_planning.service import (
    add_slice,
    delete_slice,
    get_or_create_draft_plan,
    get_plan,
    list_slice_runs,
    lock_plan,
    preview_plan,
    update_plan,
    update_slice,
)

api_router = APIRouter()


@api_router.get("/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# AI Task Coordinator
# ---------------------------------------------------------------------------


@api_router.post("/ai/task-plans", response_model=AITaskPlanRead)
def create_ai_task_plan(
    payload: AITaskPlanRequest,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return services.plan_ai_task(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Query Slicing — Query Plan / Slice / Slice Run API
# ---------------------------------------------------------------------------


@api_router.post(
    "/search-task-plans/preview",
    response_model=QueryPlanPreviewResponse,
)
def preview_query_plan(
    payload: QueryPlanPreviewRequest,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    """Generate a preview plan without creating a task."""
    try:
        result = preview_plan(
            db,
            payload.prompt,
            target_result_count=payload.target_result_count,
            countries=payload.countries,
            preset=payload.preset,
        )
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@api_router.get(
    "/search-tasks/{task_id}/query-plans",
    response_model=QueryPlanRead,
)
def get_task_query_plans(
    task_id: UUID,
    version: int | None = None,
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = get_plan(db, task_id, version=version)
    if plan is None:
        # Auto-create draft plan from intent
        plan = get_or_create_draft_plan(db, task, actor_id=str(user.id))
        db.commit()
    return plan


@api_router.patch(
    "/search-tasks/{task_id}/query-plans/{version}",
    response_model=QueryPlanRead,
)
def update_query_plan(
    task_id: UUID,
    version: int,
    payload: QueryPlanUpdate,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = db.scalar(
        select(SearchQueryPlan).where(
            SearchQueryPlan.task_id == str(task_id),
            SearchQueryPlan.version == version,
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Query plan version not found")

    try:
        result = update_plan(db, plan, payload, actor_id=str(user.id))
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post(
    "/search-tasks/{task_id}/query-plans/{version}/lock",
    response_model=QueryPlanRead,
    status_code=status.HTTP_200_OK,
)
def lock_query_plan(
    task_id: UUID,
    version: int,
    payload: QueryPlanLockRequest,
    user: User = require_permission("tasks:execute"),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = db.scalar(
        select(SearchQueryPlan).where(
            SearchQueryPlan.task_id == str(task_id),
            SearchQueryPlan.version == version,
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Query plan version not found")

    # Optimistic concurrency check
    plan_updated = plan.updated_at
    if plan_updated is not None:
        plan_ts = plan_updated.replace(microsecond=0)
        payload_ts = payload.updated_at.replace(microsecond=0)
        if plan_ts != payload_ts:
            raise HTTPException(
                status_code=409,
                detail="The plan was modified by another session. Please refresh and try again.",
            )

    try:
        result = lock_plan(
            db, plan, task,
            actor_id=str(user.id),
            updated_at_expected=payload.updated_at,
        )
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post(
    "/search-tasks/{task_id}/query-plans/{version}/slices",
    response_model=QuerySliceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_query_slice(
    task_id: UUID,
    version: int,
    payload: QuerySliceCreate,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = db.scalar(
        select(SearchQueryPlan).where(
            SearchQueryPlan.task_id == str(task_id),
            SearchQueryPlan.version == version,
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Query plan version not found")

    try:
        result = add_slice(db, plan, payload)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.patch(
    "/search-tasks/{task_id}/query-plans/{version}/slices/{slice_id}",
    response_model=QuerySliceRead,
)
def update_query_slice(
    task_id: UUID,
    version: int,
    slice_id: UUID,
    payload: QuerySliceUpdate,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = db.scalar(
        select(SearchQueryPlan).where(
            SearchQueryPlan.task_id == str(task_id),
            SearchQueryPlan.version == version,
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Query plan version not found")

    from app.modules.models import SearchQuerySlice

    slice_obj = db.scalar(
        select(SearchQuerySlice).where(
            SearchQuerySlice.id == str(slice_id),
            SearchQuerySlice.plan_id == str(plan.id),
        )
    )
    if slice_obj is None:
        raise HTTPException(status_code=404, detail="Slice not found")

    try:
        result = update_slice(db, plan, slice_obj, payload)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.delete(
    "/search-tasks/{task_id}/query-plans/{version}/slices/{slice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_query_slice(
    task_id: UUID,
    version: int,
    slice_id: UUID,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> None:
    task = db.get(SearchTask, task_id)
    if task is None or str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")

    plan = db.scalar(
        select(SearchQueryPlan).where(
            SearchQueryPlan.task_id == str(task_id),
            SearchQueryPlan.version == version,
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Query plan version not found")

    from app.modules.models import SearchQuerySlice

    slice_obj = db.scalar(
        select(SearchQuerySlice).where(
            SearchQuerySlice.id == str(slice_id),
            SearchQuerySlice.plan_id == str(plan.id),
        )
    )
    if slice_obj is None:
        raise HTTPException(status_code=404, detail="Slice not found")

    try:
        delete_slice(db, plan, slice_obj)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.get(
    "/search-tasks/{task_id}/query-slice-runs",
)
def get_slice_runs(
    task_id: UUID,
    slice_id: UUID | None = None,
    run_status: str | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    require_task_access(db, task_id, user)
    return list_slice_runs(
        db, task_id, slice_id=slice_id, status=run_status, page=page, page_size=page_size
    )


@api_router.post(
    "/search-tasks/{task_id}/continue-new",
    response_model=ContinueNewResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def continue_new(
    task_id: UUID,
    payload: ContinueNewRequest,
    user: User = require_permission("tasks:execute"),
    db: Session = Depends(get_db),
) -> dict:
    """Create a new plan version for continued execution without losing history."""
    task = require_task_access(db, task_id, user)

    existing_plan = db.scalar(
        select(SearchQueryPlan)
        .where(SearchQueryPlan.task_id == task_id)
        .order_by(SearchQueryPlan.version.desc())
        .limit(1)
    )
    if existing_plan is None:
        raise HTTPException(status_code=400, detail="No existing plan; create a plan first")

    # Supersede the old locked plan
    if existing_plan.status == "locked":
        existing_plan.status = "superseded"

    max_version = db.scalar(
        select(func.max(SearchQueryPlan.version)).where(
            SearchQueryPlan.task_id == task_id
        )
    ) or 0
    new_version = max_version + 1
    new_plan = SearchQueryPlan(
        id=uuid4(),
        task_id=task.id,
        organization_id=str(task.organization_id) if task.organization_id else None,
        version=new_version,
        generator_type="user",
        generator_version=None,
        status="draft",
        target_result_count=payload.target_result_count if hasattr(payload, 'target_result_count') else existing_plan.target_result_count,
        candidate_fetch_limit=existing_plan.candidate_fetch_limit,
        max_provider_calls=existing_plan.max_provider_calls,
        budget_limit=existing_plan.budget_limit,
        repeat_mode=payload.repeat_mode,
        created_by=str(user.id),
    )
    db.add(new_plan)
    services.emit(
        db,
        "query_plan.generated",
        {"task_id": str(task.id), "plan_id": str(new_plan.id), "version": new_version},
    )
    db.commit()
    return {
        "plan_id": new_plan.id,
        "task_id": task_id,
        "version": new_version,
        "status": "draft",
    }


# ---------------------------------------------------------------------------
# Vendor Capabilities
# ---------------------------------------------------------------------------


@api_router.get("/vendor-capabilities")
def get_vendor_capabilities(
    task_mode: Literal["brand_discovery", "exact_brand"] = Query(default="exact_brand"),
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return which vendors are available, enabled, and what stages they support."""
    from app.modules.models import VendorCredential

    credentials = {
        item.vendor: item
        for item in db.scalars(select(VendorCredential)).all()
        if item.enabled and item.encrypted_api_key
    }

    vendor_defs: dict[str, dict] = {
        "apollo": {
            "vendor": "apollo",
            "display_name": "Apollo",
            "stages": ["company_search", "contact_search", "contact_enrichment"],
            "email_method": "from_contact_search",
        },
        "hunter": {
            "vendor": "hunter",
            "display_name": "Hunter",
            "stages": ["company_search", "contact_search", "email_finder", "email_verifier"],
            "email_method": "email_finder_domain_search",
        },
    }

    result = []
    for vendor_key, info in vendor_defs.items():
        cred = credentials.get(vendor_key)
        enabled = cred is not None
        available = enabled and cred.last_test_ok is not False
        reason = (
            None
            if available
            else "Connection test failed"
            if enabled
            else "API key not configured or vendor disabled"
        )
        result.append({
            "vendor": info["vendor"],
            "display_name": info["display_name"],
            "enabled": enabled,
            "available": available,
            "unavailable_reason": reason,
            "supported_stages": info["stages"],
            "email_method": info["email_method"],
            "supported_task_modes": [task_mode],
        })
    return result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@api_router.get("/dashboard")
def get_dashboard(
    user: User = require_permission("tasks:read"), db: Session = Depends(get_db)
) -> dict:
    return services.dashboard(db)


# ---------------------------------------------------------------------------
# Search Tasks
# ---------------------------------------------------------------------------


@api_router.get("/search-tasks")
def list_search_tasks(
    user: User = require_permission("tasks:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_search_tasks(db, page, page_size, user.organization_id)


@api_router.post(
    "/search-tasks", response_model=SearchTaskRead, status_code=status.HTTP_201_CREATED
)
def create_search_task(
    payload: SearchTaskCreate,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> SearchTask:
    if payload.mode != "excel_import" and not payload.selected_vendors:
        raise HTTPException(
            status_code=422,
            detail="Select Apollo, Hunter, or both before creating the task",
        )
    task = services.create_search_task(
        db, payload, organization_id=user.organization_id, owner_id=user.id
    )
    db.commit()
    db.refresh(task)
    return task


@api_router.post(
    "/search-tasks/{task_id}/start",
    response_model=SearchTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_search_task(
    task_id: UUID, user: User = require_permission("tasks:execute"), db: Session = Depends(get_db)
) -> SearchTask:
    try:
        current = db.get(SearchTask, task_id)
        should_enqueue = current is not None and current.status not in {
            TaskStatus.queued,
            TaskStatus.running,
            TaskStatus.completed,
        }
        task = services.queue_search_task(db, task_id)
        db.commit()
        if should_enqueue:
            execute_search_task_job.delay(str(task.id))
        db.refresh(task)
        return task
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        task = db.get(SearchTask, task_id)
        if task:
            transition_task(task, TaskStatus.failed)
            task.error_message = f"Task queue unavailable: {exc}"
            db.commit()
        raise HTTPException(status_code=503, detail="Task queue is unavailable") from exc


@api_router.get("/search-tasks/{task_id}", response_model=SearchTaskRead)
def get_search_task(
    task_id: UUID, user: User = require_permission("tasks:read"), db: Session = Depends(get_db)
) -> SearchTask:
    return require_task_access(db, task_id, user)


@api_router.get("/search-tasks/{task_id}/discovery-candidates")
def list_task_discovery_candidates(
    task_id: UUID,
    candidate_status: str | None = Query(default=None, alias="status"),
    evaluation_status: Literal["pending", "running", "insufficient_data", "completed", "failed"] | None = Query(default=None),
    rating: Literal["A", "B", "C", "D"] | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    max_score: int | None = Query(default=None, ge=0, le=100),
    sort_by: Literal["last_seen_at", "target_relevance_score"] = Query(default="target_relevance_score"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    """Task-scoped candidate list: DiscoveryCandidateHit is the match fact source."""
    from app.modules import discovery_review

    task = db.get(SearchTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Search task not found")
    if str(user.organization_id or "") != str(task.organization_id or ""):
        raise HTTPException(status_code=404, detail="Search task not found")
    return discovery_review.list_task_candidates(
        db,
        task,
        page,
        page_size,
        status=candidate_status,
        evaluation_status=evaluation_status,
        rating=rating,
        min_score=min_score,
        max_score=max_score,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@api_router.get("/search-tasks/{task_id}/items")
def list_task_items(
    task_id: UUID,
    user: User = require_permission("tasks:read"),
    page: int = 1,
    page_size: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    require_task_access(db, task_id, user)
    return services.list_task_items(db, task_id, page, page_size)


@api_router.get("/search-tasks/{task_id}/checkpoints")
def list_task_checkpoints(
    task_id: UUID,
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    require_task_access(db, task_id, user)
    return services.list_task_checkpoints(db, task_id)


@api_router.get("/search-tasks/{task_id}/events")
def stream_task_events(
    task_id: UUID,
    after_sequence: int = Query(0, ge=0),
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # Verify access once before establishing the stream
    require_task_access(db, task_id, user)
    org_id = str(user.organization_id or "")
    task_id_str = str(task_id)

    def generate():
        # Re-verify on each reconnection batch
        with SessionLocal() as verify_db:
            task_row = verify_db.get(SearchTask, task_id_str)
            if task_row is None or (
                task_row.organization_id is not None
                and str(task_row.organization_id) != org_id
            ):
                yield "event: error\ndata: {\"error\":\"not_found\"}\n\n"
                return

        cursor = after_sequence
        idle_cycles = 0
        while idle_cycles < 150:
            with SessionLocal() as event_db:
                # Re-verify org access on each poll cycle
                task_row = event_db.get(SearchTask, task_id_str)
                if task_row is None or (
                    task_row.organization_id is not None
                    and str(task_row.organization_id) != org_id
                ):
                    yield "event: error\ndata: {\"error\":\"not_found\"}\n\n"
                    return

                events = event_db.scalars(
                    select(DomainEvent)
                    .where(
                        DomainEvent.aggregate_type == "search_task",
                        DomainEvent.aggregate_id == task_id_str,
                        DomainEvent.sequence > cursor,
                    )
                    .order_by(DomainEvent.sequence.asc())
                    .limit(100)
                ).all()
                if events:
                    idle_cycles = 0
                    for event in events:
                        cursor = int(event.sequence or cursor)
                        body = {
                            "event_id": str(event.id),
                            "sequence": cursor,
                            "event_type": event.event_type or event.event_name,
                            "aggregate_type": event.aggregate_type,
                            "aggregate_id": event.aggregate_id,
                            "candidate_id": event.candidate_id,
                            "schema_version": event.schema_version,
                            "payload": event.payload or {},
                            "created_at": event.created_at.isoformat(),
                        }
                        yield f"id: {event.id}\nevent: message\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
                else:
                    idle_cycles += 1
                    yield ": keepalive\n\n"
            time.sleep(2)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_router.post("/search-tasks/{task_id}/pause", response_model=SearchTaskRead)
def pause_search_task(
    task_id: UUID, user: User = require_permission("tasks:execute"), db: Session = Depends(get_db)
) -> SearchTask:
    try:
        task = services.pause_search_task(db, task_id)
        db.commit()
        db.refresh(task)
        return task
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post(
    "/search-tasks/{task_id}/resume",
    response_model=SearchTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_search_task(
    task_id: UUID, user: User = require_permission("tasks:execute"), db: Session = Depends(get_db)
) -> SearchTask:
    try:
        task = services.queue_search_task(db, task_id)
        db.commit()
        execute_search_task_job.delay(str(task.id))
        db.refresh(task)
        return task
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post("/search-tasks/{task_id}/cancel", response_model=SearchTaskRead)
def cancel_search_task(
    task_id: UUID, user: User = require_permission("tasks:execute"), db: Session = Depends(get_db)
) -> SearchTask:
    try:
        task = services.cancel_search_task(db, task_id)
        db.commit()
        db.refresh(task)
        return task
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post(
    "/search-tasks/{task_id}/copy",
    response_model=SearchTaskRead,
    status_code=status.HTTP_201_CREATED,
)
def copy_search_task(
    task_id: UUID, user: User = require_permission("tasks:write"), db: Session = Depends(get_db)
) -> SearchTask:
    try:
        task = services.copy_search_task(db, task_id)
        db.commit()
        db.refresh(task)
        return task
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Discovery Candidates
# ---------------------------------------------------------------------------


@api_router.get("/discovery-candidates")
def list_discovery_candidates(
    candidate_status: str | None = Query(default=None, alias="status"),
    task_id: UUID | None = Query(default=None),
    evaluation_status: Literal["pending", "running", "insufficient_data", "completed", "failed"] | None = Query(default=None),
    rating: Literal["A", "B", "C", "D"] | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    max_score: int | None = Query(default=None, ge=0, le=100),
    sort_by: Literal["last_seen_at", "target_relevance_score"] = Query(default="last_seen_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = require_permission("brands:read"),
    db: Session = Depends(get_db),
) -> dict:
    from app.modules import discovery_review

    return discovery_review.list_candidates(
        db,
        page,
        page_size,
        status=candidate_status,
        task_id=task_id,
        evaluation_status=evaluation_status,
        rating=rating,
        min_score=min_score,
        max_score=max_score,
        sort_by=sort_by,
        sort_order=sort_order,
        organization_id=user.organization_id,
    )


@api_router.post(
    "/discovery-candidates/{candidate_id}/approve", status_code=status.HTTP_202_ACCEPTED
)
def approve_discovery_candidate(
    candidate_id: UUID,
    payload: DiscoveryCandidateApprove,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    source_task = db.get(SearchTask, payload.task_id)
    if source_task is None or str(user.organization_id or "") != str(
        source_task.organization_id or ""
    ):
        raise HTTPException(status_code=404, detail="Search task not found")
    candidate = db.scalar(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.id == candidate_id)
        .with_for_update()
    )
    hit = db.scalar(
        select(DiscoveryCandidateHit)
        .where(
            DiscoveryCandidateHit.candidate_id == candidate_id,
            DiscoveryCandidateHit.task_id == source_task.id,
        )
        .with_for_update()
    )
    from app.modules import discovery_review

    blocker = discovery_review.bulk_approve_blocker(candidate, hit)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选品牌不存在")
    if blocker is not None:
        code, message = blocker
        raise HTTPException(status_code=409, detail={"reason_code": code, "message": message})
    try:
        task = services.approve_discovery_candidate(
            db,
            candidate,
            target_titles=payload.target_titles,
            contacts_limit_per_brand=payload.contacts_limit_per_brand,
            source_task=source_task,
        )
        services.queue_search_task(db, task.id)
        db.commit()
        execute_search_task_job.delay(str(task.id))
        return {
            "candidate_id": str(candidate.id),
            "status": candidate.status,
            "task_id": str(task.id),
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="无法提交精准品牌丰富任务") from exc


@api_router.post("/discovery-candidates/{candidate_id}/reject")
def reject_discovery_candidate(
    candidate_id: UUID,
    payload: DiscoveryCandidateReject,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    candidate = db.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选品牌不存在")
    try:
        services.reject_discovery_candidate(db, candidate, payload.reason)
        db.commit()
        db.refresh(candidate)
        return services.to_dict(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post(
    "/discovery-candidates/{candidate_id}/enrich-industry", status_code=status.HTTP_202_ACCEPTED
)
def enrich_discovery_candidate_industry(
    candidate_id: UUID,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    candidate = db.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        services.queue_candidate_industry_enrichment(db, candidate)
        db.commit()
        enrich_candidate_industry_job.delay(str(candidate.id))
        return {"candidate_id": str(candidate.id), "status": "queued"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post("/discovery-candidates/bulk-enrich-industry", status_code=status.HTTP_202_ACCEPTED)
def bulk_enrich_discovery_candidate_industry(
    payload: DiscoveryCandidateBatchRequest,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    candidates = list(
        db.scalars(select(DiscoveryCandidate).where(DiscoveryCandidate.id.in_(payload.ids)))
    )
    queued: list[str] = []
    skipped: list[str] = []
    for candidate in candidates:
        try:
            services.queue_candidate_industry_enrichment(db, candidate)
            queued.append(str(candidate.id))
        except ValueError:
            skipped.append(str(candidate.id))
    db.commit()
    for candidate_id in queued:
        enrich_candidate_industry_job.delay(candidate_id)
    return {"queued": len(queued), "skipped": len(skipped), "candidate_ids": queued}


@api_router.post("/discovery-candidates/bulk-approve")
def bulk_approve_discovery_candidates(
    payload: DiscoveryCandidateBulkApprove,
    user: User = require_permission("brands:write"),
    _executor: User = require_permission("tasks:execute"),
    db: Session = Depends(get_db),
) -> dict:
    """Batch-start user-selected precise enrichment candidates.

    Each candidate is processed in its own transaction so one failure never
    rolls back other records. Celery enqueue happens only after the database
    commit; if enqueue itself fails, the durably queued task is still picked
    up by the worker recovery scan. Repeating the same request is safe: the
    candidate status machine rejects already-enriching candidates. Relevance
    evaluation is advisory and is not an enrichment prerequisite.
    """
    from app.modules import discovery_review

    source_task = db.get(SearchTask, payload.task_id)
    if source_task is None or str(user.organization_id or "") != str(
        source_task.organization_id or ""
    ):
        raise HTTPException(status_code=404, detail="Search task not found")

    items: list[dict] = []
    approved = skipped = failed = 0
    for candidate_id in payload.candidate_ids:
        candidate = db.scalar(
            select(DiscoveryCandidate)
            .where(DiscoveryCandidate.id == candidate_id)
            .with_for_update()
        )
        hit = db.scalar(
            select(DiscoveryCandidateHit)
            .where(
                DiscoveryCandidateHit.candidate_id == candidate_id,
                DiscoveryCandidateHit.task_id == source_task.id,
            )
            .with_for_update()
        )
        blocker = discovery_review.bulk_approve_blocker(candidate, hit)
        if blocker is not None:
            code, message = blocker
            skipped += 1
            items.append({
                "candidate_id": str(candidate_id),
                "status": "skipped",
                "reason_code": code,
                "message": message,
            })
            db.rollback()
            continue
        try:
            task = services.approve_discovery_candidate(
                db,
                candidate,
                target_titles=payload.target_titles,
                contacts_limit_per_brand=payload.contacts_limit_per_brand,
                source_task=source_task,
            )
            services.queue_search_task(db, task.id)
            db.commit()
        except ValueError as exc:
            db.rollback()
            skipped += 1
            items.append({
                "candidate_id": str(candidate_id),
                "status": "skipped",
                "reason_code": "INVALID_STATUS",
                "message": str(exc)[:200],
            })
            continue
        except Exception:
            db.rollback()
            failed += 1
            items.append({
                "candidate_id": str(candidate_id),
                "status": "failed",
                "reason_code": "TASK_CREATE_FAILED",
                "message": "创建精准丰富任务失败，请重试",
            })
            continue
        try:
            execute_search_task_job.delay(str(task.id))
        except Exception:
            # The task row is durably queued; the worker recovery scan
            # re-enqueues it. Enqueue failure must not fail the record.
            pass
        approved += 1
        items.append({
            "candidate_id": str(candidate_id),
            "status": "approved",
            "task_id": str(task.id),
        })
    return {"approved": approved, "skipped": skipped, "failed": failed, "items": items}


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


@api_router.get("/brands")
def list_brands(
    user: User = require_permission("brands:read"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    return services.list_brands(db, page, page_size)


@api_router.get("/brands/hierarchy")
def list_brand_hierarchy(
    user: User = require_permission("brands:read"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    return services.list_brand_hierarchy(db, page, page_size)


@api_router.post("/brands", status_code=status.HTTP_201_CREATED)
def create_brand(
    payload: BrandCreate,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    brand = services.create_brand(db, payload)
    db.commit()
    db.refresh(brand)
    return services.to_dict(brand)


@api_router.get("/brands/{brand_id}")
def get_brand(
    brand_id: UUID, user: User = require_permission("brands:read"), db: Session = Depends(get_db)
) -> dict:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    return services.brand_detail(db, brand)


@api_router.patch("/brands/{brand_id}")
def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    services.update_brand(db, brand, payload)
    db.commit()
    db.refresh(brand)
    return services.to_dict(brand)


@api_router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_brand(
    brand_id: UUID, user: User = require_permission("brands:write"), db: Session = Depends(get_db)
) -> None:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    services.bulk_archive_brands(db, [brand_id])
    db.commit()


@api_router.post("/brands/bulk-archive")
def bulk_archive_brands(
    payload: BrandBatchRequest,
    user: User = require_permission("brands:write"),
    db: Session = Depends(get_db),
) -> dict:
    result = services.bulk_archive_brands(db, payload.ids)
    db.commit()
    return result


@api_router.post("/brands/{brand_id}/enrich", status_code=status.HTTP_202_ACCEPTED)
def enrich_brand(
    brand_id: UUID, user: User = require_permission("brands:write"), db: Session = Depends(get_db)
) -> dict:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    provider = services.enabled_provider(db, "company_search")
    if provider is None:
        raise HTTPException(
            status_code=409, detail="No enabled company_search Provider is configured"
        )
    services.emit(
        db, "brand.enrich_requested", {"brand_id": brand_id, "provider": provider.provider}
    )
    services.audit(db, "brand.enrich_requested", "brand", str(brand_id))
    db.commit()
    return {"brand_id": str(brand_id), "status": "queued", "provider": provider.provider}


@api_router.post("/brands/{brand_id}/approve-discovery", status_code=status.HTTP_202_ACCEPTED)
def approve_brand_discovery(
    brand_id: UUID, user: User = require_permission("brands:write"), db: Session = Depends(get_db)
) -> dict:
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    try:
        task = services.approve_discovery_brand(db, brand)
        services.queue_search_task(db, task.id)
        db.commit()
        execute_search_task_job.delay(str(task.id))
        return {"brand_id": str(brand.id), "status": brand.status, "task_id": str(task.id)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Unable to queue enrichment task") from exc


@api_router.post("/brands/{brand_id}/parse-website", status_code=status.HTTP_202_ACCEPTED)
def parse_brand_website(
    brand_id: UUID, user: User = require_permission("brands:write"), db: Session = Depends(get_db)
) -> dict:
    """Manually trigger website parsing for a single brand."""
    brand = db.get(Brand, brand_id)
    if brand is None or brand.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Brand not found")
    if not brand.primary_website:
        raise HTTPException(status_code=400, detail="Brand has no website URL")

    result = services.parse_brand_website(db, brand)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@api_router.get("/contacts")
def list_contacts(
    user: User = require_permission("contacts:read"),
    page: int = 1,
    page_size: int = 50,
    search: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> dict:
    return services.list_contacts(
        db, page, page_size, search=search, organization_id=user.organization_id
    )


@api_router.post("/contacts", status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreate,
    user: User = require_permission("contacts:write"),
    db: Session = Depends(get_db),
) -> dict:
    contact = services.create_contact(db, payload, organization_id=user.organization_id)
    db.commit()
    db.refresh(contact)
    return services.to_dict(contact)


@api_router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: UUID,
    payload: ContactUpdate,
    user: User = require_permission("contacts:write"),
    db: Session = Depends(get_db),
) -> dict:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Contact not found")
    services.update_contact(db, contact, payload)
    db.commit()
    db.refresh(contact)
    return services.to_dict(contact)


@api_router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_contact(
    contact_id: UUID,
    user: User = require_permission("contacts:write"),
    db: Session = Depends(get_db),
) -> None:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Contact not found")
    services.bulk_archive_contacts(db, [contact_id])
    db.commit()


@api_router.post("/contacts/bulk-archive")
def bulk_archive_contacts(
    payload: ContactBatchRequest,
    user: User = require_permission("contacts:write"),
    db: Session = Depends(get_db),
) -> dict:
    result = services.bulk_archive_contacts(db, payload.ids)
    db.commit()
    return result


@api_router.post("/contacts/export")
def export_selected_contacts(
    payload: ContactBatchRequest,
    user: User = require_permission("export:execute"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    content, count = services.export_selected_contacts_csv(db, payload.ids)
    db.commit()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="buyerreach-contacts-selected.csv"',
            "X-Exported-Count": str(count),
        },
    )


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


@api_router.get("/emails")
def list_emails(
    user: User = require_permission("emails:read"),
    page: int = 1,
    page_size: int = 50,
    contact_id: UUID | None = None,
    brand_id: UUID | None = None,
    authenticity_level: str | None = None,
    pool: str | None = None,
    min_confidence: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_emails(
        db,
        page,
        page_size,
        contact_id=contact_id,
        authenticity_level=authenticity_level,
        pool=pool,
        min_confidence=min_confidence,
        brand_id=brand_id,
    )


@api_router.get("/emails/{email_id}/authenticity")
def get_email_authenticity(
    email_id: UUID,
    user: User = require_permission("emails:read"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return services.email_authenticity_detail(db, email_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/emails", status_code=status.HTTP_201_CREATED)
def create_email(
    payload: EmailCreate,
    user: User = require_permission("emails:write"),
    db: Session = Depends(get_db),
) -> dict:
    email = services.create_email(db, payload)
    db.commit()
    db.refresh(email)
    return services.to_dict(email)


@api_router.patch("/emails/{email_id}")
def update_email(
    email_id: UUID,
    payload: EmailUpdate,
    user: User = require_permission("emails:write"),
    db: Session = Depends(get_db),
) -> dict:
    email = db.get(EmailAddress, email_id)
    if email is None or email.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Email not found")
    services.update_email(db, email, payload)
    db.commit()
    db.refresh(email)
    return services.to_dict(email)


@api_router.delete("/emails/{email_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_email(
    email_id: UUID, user: User = require_permission("emails:write"), db: Session = Depends(get_db)
) -> None:
    email = db.get(EmailAddress, email_id)
    if email is None or email.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Email not found")
    services.archive_entity(db, email, "email")
    db.commit()


@api_router.post("/emails/bulk-archive")
def bulk_archive_emails(
    payload: EmailBatchRequest,
    user: User = require_permission("emails:write"),
    db: Session = Depends(get_db),
) -> dict:
    result = services.bulk_archive_emails(db, payload.ids)
    db.commit()
    return result


@api_router.post("/emails/export")
def export_selected_emails(
    payload: EmailBatchRequest,
    user: User = require_permission("export:execute"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    content, count = services.export_selected_emails_csv(db, payload.ids)
    db.commit()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="buyerreach-emails-selected.csv"',
            "X-Exported-Count": str(count),
        },
    )


@api_router.post("/emails/verify", status_code=status.HTTP_202_ACCEPTED)
def verify_email(
    payload: EmailVerifyRequest,
    user: User = require_permission("emails:verify"),
    db: Session = Depends(get_db),
) -> dict:
    if payload.email_id is None:
        if payload.address is None:
            raise HTTPException(status_code=400, detail="email_id or address is required")
        email = services.create_email(db, EmailCreate(address=payload.address))
    else:
        email = db.get(EmailAddress, payload.email_id)
        if email is None or email.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Email not found")
    try:
        verified = services.verify_email(db, email.id)
        db.commit()
        db.refresh(verified)
        return services.to_dict(verified)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/emails/{email_id}/review")
def review_email(
    email_id: UUID,
    payload: EmailReviewRequest,
    user: User = require_permission("emails:write"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        email = services.review_email(db, email_id, payload.decision, payload.reason)
        db.commit()
        db.refresh(email)
        return services.to_dict(email)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Task Retry
# ---------------------------------------------------------------------------


@api_router.post("/search-tasks/{task_id}/retry-failed", status_code=status.HTTP_202_ACCEPTED)
def retry_failed_task_items(
    task_id: UUID, user: User = require_permission("tasks:execute"), db: Session = Depends(get_db)
) -> dict:
    try:
        retried = services.retry_failed_task_items(db, task_id)
        db.commit()
        execute_search_task_job.delay(str(task_id))
        return {"task_id": str(task_id), "retried": retried}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@api_router.post("/dedup/check")
def dedup_check(
    user: User = require_permission("dedup:execute"), db: Session = Depends(get_db)
) -> dict:
    return services.dedup_check(db)


@api_router.post("/dedup/merge")
def dedup_merge(
    payload: DedupMergeRequest,
    user: User = require_permission("dedup:execute"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = services.merge_duplicates(db, payload)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------


@api_router.post("/imports", status_code=status.HTTP_201_CREATED)
async def import_file(
    entity_type: str = Form(...),
    file: UploadFile = File(...),
    field_mapping: str | None = Form(None),
    user: User = require_permission("import:execute"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        rows = read_rows(file.filename or "upload.csv", await file.read())
        mapping = json.loads(field_mapping) if field_mapping else None
        if mapping is not None and not isinstance(mapping, dict):
            raise ValueError("field_mapping must be a JSON object")
        result = services.import_rows(db, entity_type, rows, mapping)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.post("/imports/preview")
async def preview_import_file(
    file: UploadFile = File(...),
    user: User = require_permission("import:execute"),
) -> dict:
    try:
        rows = read_rows(file.filename or "upload.csv", await file.read())
        return {
            "headers": list(rows[0].keys()) if rows else [],
            "preview": rows[:20],
            "total_rows": len(rows),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Batch Exact Brand ────────────────────────────────────────────────────────


@api_router.get("/batch-exact-brand/capabilities")
async def batch_exact_brand_capabilities(
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    """Expose the rollout state so clients can hide disabled entry points."""
    from app.modules.batch_exact_brand import is_batch_exact_brand_enabled

    return {"enabled": is_batch_exact_brand_enabled(db)}


@api_router.get("/batch-exact-brand/template")
async def download_batch_template(
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Download the versioned CSV template for batch exact brand import."""
    from app.modules.batch_exact_brand import generate_template_csv, is_batch_exact_brand_enabled

    if not is_batch_exact_brand_enabled(db):
        raise HTTPException(status_code=404, detail="批量精准品牌功能当前未启用")

    content = generate_template_csv()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=exact-brand-import-v1.csv",
        },
    )


@api_router.post("/batch-exact-brand/preview")
async def preview_batch_import(
    file: UploadFile = File(...),
    user: User = require_permission("import:execute"),
    db: Session = Depends(get_db),
) -> dict:
    """Parse and validate an uploaded file, returning a read-only preview.

    No SearchTask, Target, or Vendor calls are made.
    """
    from app.modules.batch_exact_brand import build_preview, is_batch_exact_brand_enabled

    if not is_batch_exact_brand_enabled(db):
        raise HTTPException(status_code=404, detail="批量精准品牌功能当前未启用")

    try:
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件大小超过 10 MB 限制")

        result = build_preview(file.filename or "upload.csv", content)
        if "error" in result:
            err = result["error"]
            raise HTTPException(status_code=400, detail=err.get("message", str(err)))

        services.audit(
            db,
            "batch_exact_brand.preview",
            "batch_import",
            result["file_hash"],
            after={"filename": file.filename, "total_rows": result["total_rows"]},
        )
        db.commit()
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"预览解析失败: {str(exc)}") from exc


@api_router.post("/batch-exact-brand/imports", status_code=status.HTTP_201_CREATED)
async def create_batch_import(
    file: UploadFile = File(...),
    user: User = require_permission("import:execute"),
    db: Session = Depends(get_db),
) -> dict:
    """Upload a file, parse it, and persist a BatchImport record."""
    from app.modules.batch_exact_brand import (
        build_preview,
        create_batch_import,
        is_batch_exact_brand_enabled,
    )

    if not is_batch_exact_brand_enabled(db):
        raise HTTPException(status_code=404, detail="批量精准品牌功能当前未启用")

    try:
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件大小超过 10 MB 限制")

        preview = build_preview(file.filename or "upload.csv", content)
        if "error" in preview:
            err = preview["error"]
            raise HTTPException(status_code=400, detail=err.get("message", str(err)))

        batch = create_batch_import(
            db,
            filename=file.filename or "upload.csv",
            file_hash=preview["file_hash"],
            parsed_rows=preview["rows"],
            organization_id=user.organization_id,
            created_by=user.id,
        )
        services.audit(
            db,
            "batch_exact_brand.upload",
            "batch_import",
            str(batch.id),
            after={"filename": batch.filename, "total_rows": batch.total_rows},
        )
        db.commit()

        return {
            "id": str(batch.id),
            "filename": batch.filename,
            "status": batch.status,
            "total_rows": batch.total_rows,
            "valid_rows": batch.valid_rows,
            "warning_rows": batch.warning_rows,
            "invalid_rows": batch.invalid_rows,
            "duplicate_rows": batch.duplicate_rows,
            "error_summary": batch.error_summary,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"导入创建失败: {str(exc)}") from exc


@api_router.get("/batch-exact-brand/imports/{batch_id}")
async def get_batch_import_detail(
    batch_id: UUID,
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
) -> dict:
    """Get batch import detail with aggregated target statistics."""
    from app.modules.batch_exact_brand import get_batch_detail

    batch = db.get(BatchImport, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    # Org isolation
    if batch.organization_id is not None and str(batch.organization_id) != str(user.organization_id):
        raise HTTPException(status_code=404, detail="导入批次不存在")

    try:
        return get_batch_detail(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/batch-exact-brand/imports/{batch_id}/confirm")
async def confirm_batch_import(
    batch_id: UUID,
    payload: BatchImportConfirm,
    user: User = require_permission("tasks:write"),
    db: Session = Depends(get_db),
) -> dict:
    """Confirm a batch import — creates parent SearchTask, Targets, TaskVendorPlan.

    Idempotent: repeated calls return the same parent task without creating duplicates.
    """
    from app.modules.batch_exact_brand import confirm_batch_import

    batch = db.get(BatchImport, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    if batch.organization_id is not None and str(batch.organization_id) != str(user.organization_id):
        raise HTTPException(status_code=404, detail="导入批次不存在")

    try:
        result = confirm_batch_import(
            db,
            batch_id=batch_id,
            config=payload,
            organization_id=user.organization_id,
            user_id=user.id,
        )
        db.commit()

        parent_task = result["parent_task"]
        targets = result["targets"]

        return {
            "batch_id": str(result["batch"].id),
            "parent_task_id": str(parent_task.id),
            "parent_task_name": parent_task.name,
            "target_count": len(targets),
            "vendors": payload.selected_vendors,
            "already_confirmed": result["already_confirmed"],
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"确认失败: {str(exc)}") from exc


@api_router.get("/search-tasks/{task_id}/targets")
async def list_task_targets(
    task_id: UUID,
    user: User = require_permission("tasks:read"),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
) -> dict:
    """List ExactBrandTargets for a parent task."""
    from app.modules.batch_exact_brand import get_targets_for_task

    require_task_access(db, task_id, user)
    return get_targets_for_task(db, task_id, page=page, page_size=page_size, status_filter=status)


@api_router.post("/search-tasks/{task_id}/targets/retry")
async def retry_failed_targets(
    task_id: UUID,
    payload: TargetRetryRequest,
    user: User = require_permission("tasks:execute"),
    db: Session = Depends(get_db),
) -> dict:
    """Retry specific failed/retryable targets."""
    from app.modules.batch_exact_brand import retry_targets

    task = require_task_access(db, task_id, user)
    if task.mode != "batch_exact_brand":
        raise HTTPException(status_code=400, detail="此操作仅支持批量精准品牌任务")

    try:
        count = retry_targets(db, task_id=task_id, target_ids=payload.target_ids, user_id=user.id)
        db.commit()
        return {"retried": count}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api_router.get("/search-tasks/{task_id}/targets/errors.csv")
async def export_target_errors_csv(
    task_id: UUID,
    user: User = require_permission("export:execute"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export error rows as downloadable CSV."""
    from app.modules.batch_exact_brand import export_target_errors

    require_task_access(db, task_id, user)
    content = export_target_errors(db, task_id)
    services.audit(db, "batch_exact_brand.export_errors", "search_task", str(task_id))
    db.commit()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="batch-errors-{task_id}.csv"',
        },
    )


@api_router.get("/search-tasks/{task_id}/targets/export.csv")
async def export_reliable_emails_csv(
    task_id: UUID,
    user: User = require_permission("export:execute"),
    db: Session = Depends(get_db),
    scope: str = Query("verified", pattern="^(verified|reviewable|all)$"),
) -> StreamingResponse:
    """Export reliable emails across all completed targets."""
    from app.modules.batch_exact_brand import export_reliable_emails

    require_task_access(db, task_id, user)
    content = export_reliable_emails(db, task_id, scope=scope)
    services.audit(
        db,
        "batch_exact_brand.export_emails",
        "search_task",
        str(task_id),
        after={"scope": scope},
    )
    db.commit()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="reliable-emails-{task_id}.csv"',
        },
    )


@api_router.post("/exports")
def export_file(
    payload: ExportRequest,
    user: User = require_permission("export:execute"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    content = services.export_csv(db, payload.entity_type, payload.filters)
    db.commit()
    filename = f"buyerreach-{payload.entity_type}.csv"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Provider Configs
# ---------------------------------------------------------------------------


@api_router.get("/vendor-credentials")
def list_vendor_credentials(
    user: User = require_permission("providers:read"), db: Session = Depends(get_db)
) -> list[dict]:
    return services.list_vendor_credentials(db)


@api_router.patch("/vendor-credentials/{vendor}")
def update_vendor_credential(
    vendor: str,
    payload: VendorCredentialUpdate,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> dict:
    credential = (
        db.query(VendorCredential).filter(VendorCredential.vendor == vendor.lower()).one_or_none()
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="Vendor credential not found")
    services.update_vendor_credential(db, credential, payload)
    db.commit()
    db.refresh(credential)
    return services.vendor_credential_public(credential)


@api_router.post("/vendor-credentials/{vendor}/test")
def test_vendor_credential(
    vendor: str,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> dict:
    credential = (
        db.query(VendorCredential).filter(VendorCredential.vendor == vendor.lower()).one_or_none()
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="Vendor credential not found")
    result = services.test_vendor_credential(db, credential)
    db.commit()
    return result


@api_router.get("/provider-configs")
def list_provider_configs(
    user: User = require_permission("providers:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_provider_configs(db, page, page_size)


@api_router.post("/provider-configs", status_code=status.HTTP_201_CREATED)
def create_provider_config(
    payload: ProviderConfigCreate,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> dict:
    provider = services.create_provider_config(db, payload)
    db.commit()
    db.refresh(provider)
    return services.provider_public(provider)


@api_router.patch("/provider-configs/{provider_id}")
def update_provider_config(
    provider_id: UUID,
    payload: ProviderConfigUpdate,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> dict:
    provider = db.get(ProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    services.update_provider_config(db, provider, payload)
    db.commit()
    db.refresh(provider)
    return services.provider_public(provider)


@api_router.post("/provider-configs/{provider_id}/test")
def test_provider_config(
    provider_id: UUID,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> dict:
    provider = db.get(ProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    result = services.test_provider(db, provider)
    db.commit()
    return result


@api_router.delete("/provider-configs/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_config(
    provider_id: UUID,
    user: User = require_permission("providers:write"),
    db: Session = Depends(get_db),
) -> None:
    provider = db.get(ProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    services.audit(db, "provider_config.delete", "provider_config", str(provider.id))
    db.delete(provider)
    db.commit()


# ---------------------------------------------------------------------------
# Configuration Center
# ---------------------------------------------------------------------------


@api_router.get("/system-settings")
def get_system_settings(
    user: User = require_permission("settings:read"), db: Session = Depends(get_db)
) -> dict:
    return services.get_system_settings(db)


@api_router.patch("/system-settings")
def update_system_settings(
    payload: SystemSettingsUpdate,
    user: User = require_permission("settings:write"),
    db: Session = Depends(get_db),
) -> dict:
    result = services.update_system_settings(db, payload)
    db.commit()
    return result


@api_router.get("/roles")
def list_roles(
    user: User = require_permission("roles:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_roles(db, page, page_size, actor=user)


@api_router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    user: User = require_permission("roles:write"),
    db: Session = Depends(get_db),
) -> dict:
    from app.core.security import flatten_permissions, permissions_dominate, get_user_permissions

    if not permissions_dominate(get_user_permissions(db, user), flatten_permissions(payload.permissions)):
        raise HTTPException(status_code=403, detail="Cannot create a role with higher permissions")
    try:
        role = services.create_role(db, payload)
        db.commit()
        db.refresh(role)
        return services.to_dict(role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.patch("/roles/{role_id}")
def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    user: User = require_permission("roles:write"),
    db: Session = Depends(get_db),
) -> dict:
    from app.core.security import get_user_permissions, get_role_effective_permissions, flatten_permissions, permissions_dominate

    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    actor_permissions = get_user_permissions(db, user)
    if not permissions_dominate(actor_permissions, get_role_effective_permissions(db, role.id)):
        raise HTTPException(status_code=404, detail="Role not found")
    if not permissions_dominate(actor_permissions, flatten_permissions(payload.permissions)):
        raise HTTPException(status_code=403, detail="Cannot grant permissions higher than your own")
    services.update_role(db, role, payload)
    db.commit()
    db.refresh(role)
    return services.to_dict(role)


@api_router.get("/users")
def list_users(
    user: User = require_permission("users:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_users(db, page, page_size, actor=user)


@api_router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    user: User = require_permission("users:write"),
    db: Session = Depends(get_db),
) -> dict:
    from app.core.security import can_assign_role

    if not can_assign_role(db, user, payload.role_id):
        raise HTTPException(status_code=403, detail="Cannot assign a role with higher permissions")
    try:
        created = services.create_user(db, payload, organization_id=user.organization_id)
        db.commit()
        db.refresh(created)
        result = services.to_dict(created)
        result.pop("password_hash", None)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.patch("/users/{user_id}")
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    user: User = require_permission("users:write"),
    db: Session = Depends(get_db),
) -> dict:
    from app.core.security import can_assign_role, can_manage_user

    target = db.get(User, user_id)
    if target is None or target.deleted_at is not None or not can_manage_user(db, user, target):
        raise HTTPException(status_code=404, detail="User not found")
    if "role_id" in payload.model_fields_set and not can_assign_role(db, user, payload.role_id):
        raise HTTPException(status_code=403, detail="Cannot assign a role with higher permissions")
    services.update_user(db, target, payload)
    db.commit()
    db.refresh(target)
    result = services.to_dict(target)
    result.pop("password_hash", None)
    return result


@api_router.get("/tags")
def list_tags(
    module: Literal["brands", "contacts", "emails"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    user: User = require_permission("tags:read"),
    db: Session = Depends(get_db),
) -> dict:
    return services.list_tags(db, module, page, page_size)


@api_router.post("/tags", status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate, user: User = require_permission("tags:write"), db: Session = Depends(get_db)
) -> dict:
    try:
        tag = services.create_tag(db, payload)
        db.commit()
        db.refresh(tag)
        return services.to_dict(tag)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.get("/tags/{tag_id}")
def get_tag(
    tag_id: UUID,
    user: User = require_permission("tags:read"),
    db: Session = Depends(get_db),
) -> dict:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return services.to_dict(tag)


@api_router.patch("/tags/{tag_id}")
def update_tag(
    tag_id: UUID,
    payload: TagUpdate,
    user: User = require_permission("tags:write"),
    db: Session = Depends(get_db),
) -> dict:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    try:
        services.update_tag(db, tag, payload)
        db.commit()
        db.refresh(tag)
        return services.to_dict(tag)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tag_id: UUID,
    user: User = require_permission("tags:write"),
    db: Session = Depends(get_db),
) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    services.delete_tag(db, tag)
    db.commit()


@api_router.get("/custom-fields")
def list_custom_fields(
    module: Literal["brands", "contacts", "emails"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    user: User = require_permission("custom_fields:read"),
    db: Session = Depends(get_db),
) -> dict:
    return services.list_custom_fields(db, module, page, page_size)


@api_router.post("/custom-fields", status_code=status.HTTP_201_CREATED)
def create_custom_field(
    payload: CustomFieldCreate,
    user: User = require_permission("custom_fields:write"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        field = services.create_custom_field(db, payload)
        db.commit()
        db.refresh(field)
        return services.to_dict(field)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.get("/custom-fields/{field_id}")
def get_custom_field(
    field_id: UUID,
    user: User = require_permission("custom_fields:read"),
    db: Session = Depends(get_db),
) -> dict:
    field = db.get(CustomField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    return services.to_dict(field)


@api_router.patch("/custom-fields/{field_id}")
def update_custom_field(
    field_id: UUID,
    payload: CustomFieldUpdate,
    user: User = require_permission("custom_fields:write"),
    db: Session = Depends(get_db),
) -> dict:
    field = db.get(CustomField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    try:
        services.update_custom_field(db, field, payload)
        db.commit()
        db.refresh(field)
        return services.to_dict(field)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.delete("/custom-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_field(
    field_id: UUID,
    user: User = require_permission("custom_fields:write"),
    db: Session = Depends(get_db),
) -> None:
    field = db.get(CustomField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    services.delete_custom_field(db, field)
    db.commit()


@api_router.get("/entities/{entity_type}/{entity_id}/tags")
def list_entity_tags(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    user: User = require_permission("tags:read"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return services.list_entity_tags(db, entity_type, entity_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.put("/entities/{entity_type}/{entity_id}/tags/{tag_id}")
def assign_entity_tag(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    tag_id: UUID,
    user: User = require_permission("tags:write"),
    db: Session = Depends(get_db),
) -> dict:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    try:
        assignment = services.assign_entity_tag(db, entity_type, entity_id, tag)
        db.commit()
        result = services.to_dict(tag)
        result["assignment_id"] = str(assignment.id)
        result["entity_type"] = entity_type
        result["entity_id"] = str(entity_id)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.delete(
    "/entities/{entity_type}/{entity_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_entity_tag(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    tag_id: UUID,
    user: User = require_permission("tags:write"),
    db: Session = Depends(get_db),
) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    try:
        services.remove_entity_tag(db, entity_type, entity_id, tag)
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.get("/entities/{entity_type}/{entity_id}/custom-values")
def list_entity_custom_values(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    user: User = require_permission("custom_fields:read"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return services.list_entity_custom_values(db, entity_type, entity_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.put("/entities/{entity_type}/{entity_id}/custom-values/{field_id}")
def upsert_entity_custom_value(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    field_id: UUID,
    payload: CustomValueUpsert,
    user: User = require_permission("custom_fields:write"),
    db: Session = Depends(get_db),
) -> dict:
    field = db.get(CustomField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    try:
        value = services.upsert_custom_value(db, entity_type, entity_id, field, payload.value)
        db.commit()
        db.refresh(value)
        return services.custom_value_item(field, value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api_router.delete(
    "/entities/{entity_type}/{entity_id}/custom-values/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_entity_custom_value(
    entity_type: Literal["brands", "contacts", "emails"],
    entity_id: UUID,
    field_id: UUID,
    user: User = require_permission("custom_fields:write"),
    db: Session = Depends(get_db),
) -> None:
    field = db.get(CustomField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="Custom field not found")
    try:
        services.delete_custom_value(db, entity_type, entity_id, field)
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


@api_router.get("/blacklist")
def list_blacklist(
    user: User = require_permission("blacklist:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_page(db, Blacklist, page, page_size)


@api_router.post("/blacklist", status_code=status.HTTP_201_CREATED)
def create_blacklist(
    payload: BlacklistCreate,
    user: User = require_permission("blacklist:write"),
    db: Session = Depends(get_db),
) -> dict:
    item = services.create_blacklist(db, payload)
    db.commit()
    db.refresh(item)
    return services.to_dict(item)


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------


@api_router.get("/audit-logs")
def list_audit_logs(
    user: User = require_permission("audit:read"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    return services.list_page(db, AuditLog, page, page_size)
