"""Slice execution scheduler — picks the next page to run, executes it via the
DiscoverySourceAdapter bridge, persists the result, and decides whether to continue.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.models import (
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    SearchQueryPlan,
    SearchQuerySlice,
    SearchQuerySliceRun,
    SearchTask,
)
from app.modules.services import (
    _candidate_matches_customer,
    _discovery_candidate_raw_data,
    to_dict,
)
from app.providers.discovery import ConfiguredProviderDiscoveryAdapter
from app.providers.local import slugify
from app.query_planning.state_machine import transition_slice_run
from app.shared.models import utc_now


def execute_slice_page(
    db: Session,
    task_id: UUID,
    slice_id: UUID,
    provider: str,
    cursor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one page of one slice on one provider. Returns the SliceRun dict.

    This is the minimum billable unit — one external API call.
    """
    # Load task, plan, slice
    task = db.get(SearchTask, task_id)
    if task is None:
        return {"error": "task_not_found"}

    query_slice = db.get(SearchQuerySlice, slice_id)
    if query_slice is None:
        return {"error": "slice_not_found"}

    plan = db.get(SearchQueryPlan, UUID(query_slice.plan_id))
    if plan is None:
        return {"error": "plan_not_found"}

    # Check cancellation
    if task.status in {"cancelled", "paused"}:
        return {"status": "skipped", "reason": task.status}

    # Build adapter and request plan
    adapter = ConfiguredProviderDiscoveryAdapter(provider_name=provider)
    context = {
        "task_id": str(task.id),
        "organization_id": str(task.organization_id) if task.organization_id else None,
        "pipeline_version": task.pipeline_version,
    }

    request_plan = adapter.plan(
        {
            "countries": query_slice.countries or [],
            "target_concepts": query_slice.target_concepts or [],
            "business_types": query_slice.business_types or [],
            "include_terms": query_slice.include_terms or [],
            "exclude_terms": query_slice.exclude_terms or [],
            "match_mode": query_slice.match_mode or "any",
        },
        context,
    )

    cursor_key = json.dumps(cursor or {"page": 1}, sort_keys=True)
    input_hash_val = hashlib.sha256(
        json.dumps(request_plan, sort_keys=True, default=str).encode()
    ).hexdigest()

    # Idempotency: check for existing completed run
    existing = db.scalar(
        select(SearchQuerySliceRun).where(
            SearchQuerySliceRun.task_id == str(task.id),
            SearchQuerySliceRun.query_slice_id == str(query_slice.id),
            SearchQuerySliceRun.provider == provider,
            SearchQuerySliceRun.operation == "discover",
            SearchQuerySliceRun.input_hash == input_hash_val,
            SearchQuerySliceRun.cursor_key == cursor_key,
        )
    )
    if existing is not None and existing.status == "completed":
        return to_dict(existing)

    # Create or reuse run record
    run = existing or SearchQuerySliceRun(
        id=uuid4(),
        task_id=str(task.id),
        plan_id=str(plan.id),
        query_slice_id=str(query_slice.id),
        plan_version=plan.version,
        provider=provider,
        operation="discover",
        adapter_version="bridge-v1",
        input_hash=input_hash_val,
        cursor_key=cursor_key,
        cursor=cursor or {"page": 1},
        status="queued",
    )

    if existing is None:
        db.add(run)
        db.flush()

    # Transition to leased → running
    if run.status != "completed":
        transition_slice_run(run, "leased")
        run.lease_owner = f"worker-{uuid4().hex[:8]}"
        run.lease_acquired_at = datetime.now(UTC)
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        transition_slice_run(run, "running")
        run.started_at = datetime.now(UTC)
        run.attempts += 1
        db.flush()

    # Execute with caller's db session — never opens its own
    page_cursor: dict[str, Any] | None = (
        cursor
        or (existing.cursor if existing and existing.cursor else None)
        or {"page": 1}
    )
    try:
        page = adapter.execute(request_plan, page_cursor, db=db, task=task)
    except Exception as exc:
        transition_slice_run(run, "retryable")
        run.error_code = "adapter_error"
        run.error_message = str(exc)[:2000]
        run.completed_at = datetime.now(UTC)
        db.flush()
        return to_dict(run)

    # Persist results
    if page.ok and page.candidates:
        _ingest_slice_candidates(db, task, run, page.candidates, page.provider)

    run.call_count = 1
    run.cost = page.cost
    run.vendor_request_id = page.vendor_request_id
    run.consecutive_empty_pages = (
        0 if (page.candidates and len(page.candidates) > 0) else (run.consecutive_empty_pages + 1)
    )

    if page.ok:
        if page.next_cursor:
            transition_slice_run(run, "completed")
            run.normalized_output = {"next_cursor": page.next_cursor}
        else:
            transition_slice_run(run, "exhausted")
            run.normalized_output = {"exhausted": True}
    elif page.error_code in {"rate_limited", "http_429"}:
        transition_slice_run(run, "retryable")
        run.error_code = page.error_code
        run.error_message = page.error_message
        run.next_retry_at = datetime.now(UTC)
    else:
        transition_slice_run(run, "failed")
        run.error_code = page.error_code
        run.error_message = page.error_message

    run.completed_at = datetime.now(UTC)
    db.flush()
    return to_dict(run)


def _ingest_slice_candidates(
    db: Session,
    task: SearchTask,
    run: SearchQuerySliceRun,
    candidates: list[dict[str, Any]],
    provider_name: str,
) -> None:
    """Ingest normalized candidates from a single slice page.

    Deduplicates across the task's existing candidate pool, records source hits
    with slice provenance, and updates run counts.
    """
    raw_count = len(candidates)
    new_count = 0
    duplicate_count = 0
    filtered_count = 0

    for payload in candidates:
        name = str(payload.get("brand_name") or "").strip()
        if not name:
            filtered_count += 1
            continue

        domain = (
            payload.get("domain")
            or (payload.get("website") or "").replace("https://", "").replace("http://", "").rstrip("/")
        )
        domain = domain.strip() if domain else None

        normalized_name = slugify(name)
        country = str(payload.get("country") or "").strip() or None
        dedupe_key = (
            f"domain:{domain}" if domain
            else f"name:{normalized_name}|country:{str(country or '').casefold()}"
        )

        # Check blacklist and customer matching
        if domain and _candidate_matches_customer(db, normalized_name, domain, country):
            filtered_count += 1
            continue

        candidate = db.scalar(
            select(DiscoveryCandidate).where(DiscoveryCandidate.dedupe_key == dedupe_key)
        )
        now = utc_now()

        if candidate is None:
            raw_data = _discovery_candidate_raw_data(payload)
            candidate = DiscoveryCandidate(
                name=name,
                normalized_name=normalized_name,
                domain=domain,
                normalized_domain=domain,
                dedupe_key=dedupe_key,
                website=payload.get("website") or (f"https://{domain}" if domain else None),
                country=country,
                industry=payload.get("category"),
                relevance_score=0,
                provider=provider_name,
                raw_data=raw_data,
                status="pending",
                seen_count=1,
                first_seen_at=now,
                last_seen_at=now,
                last_task_id=task.id,
            )
            db.add(candidate)
            db.flush()
            new_count += 1
        else:
            # Refresh seen-at and count, don't duplicate
            candidate.last_seen_at = now
            candidate.last_task_id = task.id
            candidate.seen_count = (candidate.seen_count or 0) + 1
            duplicate_count += 1

        # Record source hit with slice provenance
        existing_hit = db.scalar(
            select(DiscoveryCandidateHit).where(
                DiscoveryCandidateHit.candidate_id == candidate.id,
                DiscoveryCandidateHit.task_id == task.id,
            )
        )
        if existing_hit is None:
            db.add(
                DiscoveryCandidateHit(
                    candidate_id=candidate.id,
                    task_id=task.id,
                    relevance_score=0,
                    provider=provider_name,
                    plan_id=run.plan_id,
                    query_slice_id=run.query_slice_id,
                    slice_run_id=run.id,
                    source_record_id=payload.get("domain") or payload.get("brand_name"),
                    observed_at=now,
                )
            )

    run.raw_count = raw_count
    run.new_count = new_count
    run.duplicate_count = duplicate_count
    run.filtered_count = filtered_count
