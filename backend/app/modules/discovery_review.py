"""Task-scoped candidate match review (discovery decision list).

This module owns the "candidate x search task" match facts that back the
review UI: persisting formal Concept Matching V2 results onto
DiscoveryCandidateHit, the idempotent history backfill, filtered/sorted
listings for the global and task-level candidate lists, enrichment-failure
classification, and bulk-approve eligibility. It exists so that
modules/services.py does not grow another review-oriented mega function.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.brand_discovery import CATEGORY_ALIASES
from app.modules.models import DiscoveryCandidate, DiscoveryCandidateHit, SearchTask
from app.pipeline.definition import PIPELINE_V2
from app.shared.models import utc_now

CONCEPT_MATCH_PROMPT_VERSION = "concept-match-2.0.0"
EVIDENCE_SCHEMA_VERSION = "2.0.0"

# Whitelists for user-supplied filter/sort input. Anything outside these sets
# must be rejected by the API layer before reaching a query.
EVALUATION_STATUSES = {"pending", "running", "insufficient_data", "completed", "failed"}
RATINGS = {"A", "B", "C", "D"}
SORTABLE_FIELDS = {"target_relevance_score", "last_seen_at"}
SORT_ORDERS = {"asc", "desc"}

APPROVABLE_STATUSES = {"pending", "enrichment_failed"}


# ---------------------------------------------------------------------------
# Task-scoped evaluation persistence
# ---------------------------------------------------------------------------


def record_task_evaluation(
    db: Session, task: SearchTask, candidate: DiscoveryCandidate
) -> DiscoveryCandidateHit:
    """Persist the formal evaluation of a candidate for one specific task.

    Called alongside the legacy candidate-level mirror writes so that each
    task keeps its own match facts and later searches never overwrite the
    history of earlier ones. Only ever called with a completed versioned
    Concept Matching result (including its deterministic insufficient_data
    outcome); it never fabricates a score.
    """
    hit = db.scalar(
        select(DiscoveryCandidateHit).where(
            DiscoveryCandidateHit.candidate_id == candidate.id,
            DiscoveryCandidateHit.task_id == task.id,
        )
    )
    if hit is None:
        hit = DiscoveryCandidateHit(
            candidate_id=candidate.id,
            task_id=task.id,
            relevance_score=candidate.relevance_score or 0,
            provider=candidate.provider,
        )
        db.add(hit)
    evaluation = candidate.match_evaluation if isinstance(candidate.match_evaluation, dict) else {}
    hit.evaluation_status = candidate.evaluation_status
    hit.target_relevance_score = candidate.target_relevance_score
    hit.relevance_rating = candidate.relevance_rating
    hit.match_evaluation = jsonable_encoder(candidate.match_evaluation or {})
    hit.evaluated_at = utc_now()
    hit.scoring_policy_version = (
        evaluation.get("policy_version") or PIPELINE_V2.scoring_policy_version
    )
    hit.prompt_version = CONCEPT_MATCH_PROMPT_VERSION
    hit.evidence_schema_version = (
        evaluation.get("evidence_schema_version") or EVIDENCE_SCHEMA_VERSION
    )
    db.add(hit)
    return hit


def backfill_hit_evaluations(db: Session, batch_size: int = 500) -> int:
    """Copy the candidate-level evaluation onto the hit of its origin task.

    Rule: only the hit whose task_id equals the candidate's last_task_id (the
    task that produced the current formal evaluation) may receive it. Hits are
    never touched twice (evaluated_at IS NULL guard), so the function is
    idempotent, resumable and safe to re-run after a partial failure. Batches
    advance by id cursor so skipped hits can never starve later batches.
    Returns the number of hits updated in this run.
    """
    updated = 0
    cursor: UUID | None = None
    while True:
        statement = (
            select(DiscoveryCandidateHit)
            .where(DiscoveryCandidateHit.evaluated_at.is_(None))
            .order_by(DiscoveryCandidateHit.id)
            .limit(batch_size)
        )
        if cursor is not None:
            statement = statement.where(DiscoveryCandidateHit.id > cursor)
        hits = db.scalars(statement).all()
        if not hits:
            return updated
        for hit in hits:
            cursor = hit.id
            candidate = db.get(DiscoveryCandidate, hit.candidate_id)
            if (
                candidate is None
                or candidate.evaluation_status is None
                or candidate.last_task_id is None
                or str(candidate.last_task_id) != str(hit.task_id)
            ):
                # Not the hit of the task that produced the evaluation, or no
                # evaluation exists: leave the hit unevaluated. Never guess or
                # reassign history.
                continue
            evaluation = (
                candidate.match_evaluation if isinstance(candidate.match_evaluation, dict) else {}
            )
            hit.evaluation_status = candidate.evaluation_status
            hit.target_relevance_score = candidate.target_relevance_score
            hit.relevance_rating = candidate.relevance_rating
            hit.match_evaluation = jsonable_encoder(candidate.match_evaluation or {})
            hit.evaluated_at = candidate.industry_enriched_at or candidate.updated_at or utc_now()
            hit.scoring_policy_version = evaluation.get("policy_version")
            hit.prompt_version = CONCEPT_MATCH_PROMPT_VERSION if evaluation else None
            hit.evidence_schema_version = evaluation.get("evidence_schema_version")
            db.add(hit)
            updated += 1
        db.flush()


# ---------------------------------------------------------------------------
# Enrichment failure classification
# ---------------------------------------------------------------------------

_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+|authorization\s*:\s*\S+|token\s*[:=]\s*\S+)"
)
_RESET_PATTERN = re.compile(r"reset at ([^;.]+)", re.IGNORECASE)

FAILURE_MESSAGES = {
    "quota_exhausted": "Provider 配额已用尽，可等待配额重置后重试",
    "provider_not_configured": "Provider 未配置或未启用，请在系统设置中检查后重试",
    "ai_or_network": "AI 服务或网络异常，可直接重试",
    "website_unreachable": "官网无法访问，请确认官网地址有效后重试",
    "insufficient_evidence": "官网与 Provider 均未提供足够的行业证据",
    "unknown": "行业补充失败，可重试",
}


def redact_text(value: str | None, limit: int = 2000) -> str | None:
    """Remove credential-shaped fragments before text reaches logs/UI detail."""
    if value is None:
        return None
    return _SECRET_PATTERN.sub("[REDACTED]", str(value))[:limit]


def classify_enrichment_error(error: str | None) -> dict[str, Any]:
    """Map a stored enrichment error to a short, actionable, safe summary."""
    if not error:
        return {"code": "unknown", "message": FAILURE_MESSAGES["unknown"], "reset_at": None}
    segments = [segment.strip() for segment in str(error).split(";") if segment.strip()]
    joined = " ".join(segments)
    reset_match = _RESET_PATTERN.search(joined)
    reset_at = reset_match.group(1).strip() if reset_match else None

    def has(*needles: str) -> bool:
        lowered = joined.casefold()
        return any(needle.casefold() in lowered for needle in needles)

    if has("quota unavailable", "no remaining quota", "rate limit"):
        code = "quota_exhausted"
    elif has("is unavailable", "not configured", "missing_quota_auth"):
        code = "provider_not_configured"
    elif has(
        "AI website classification",
        "AI Hunter classification",
        "tls",
        "ssl",
        "timed out",
        "timeout",
    ):
        code = "ai_or_network"
    elif has("official website:"):
        code = "website_unreachable"
    elif has("not provide enough", "no usable category", "insufficient"):
        code = "insufficient_evidence"
    else:
        code = "unknown"
    return {"code": code, "message": FAILURE_MESSAGES[code], "reset_at": reset_at}


# ---------------------------------------------------------------------------
# Filtered / sorted listings
# ---------------------------------------------------------------------------


def _apply_candidate_filters(
    statement,
    *,
    status: str | None = None,
    task_id: UUID | None = None,
    evaluation_status: str | None = None,
    rating: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    organization_id: UUID | None = None,
):
    if status:
        statement = statement.where(DiscoveryCandidate.status == status)
    if task_id is not None:
        statement = statement.where(
            select(DiscoveryCandidateHit.id)
            .where(
                DiscoveryCandidateHit.candidate_id == DiscoveryCandidate.id,
                DiscoveryCandidateHit.task_id == task_id,
            )
            .exists()
        )
    if organization_id is not None:
        statement = statement.where(
            select(DiscoveryCandidateHit.id)
            .join(SearchTask, SearchTask.id == DiscoveryCandidateHit.task_id)
            .where(
                DiscoveryCandidateHit.candidate_id == DiscoveryCandidate.id,
                SearchTask.organization_id == organization_id,
            )
            .exists()
        )
    if evaluation_status:
        statement = statement.where(DiscoveryCandidate.evaluation_status == evaluation_status)
    if rating:
        statement = statement.where(DiscoveryCandidate.relevance_rating == rating)
    if min_score is not None:
        statement = statement.where(DiscoveryCandidate.target_relevance_score >= min_score)
    if max_score is not None:
        statement = statement.where(DiscoveryCandidate.target_relevance_score <= max_score)
    return statement


def _candidate_sort(statement, sort_by: str, sort_order: str):
    """NULL relevance scores always sort after real scores, both directions."""
    if sort_by == "target_relevance_score":
        column = DiscoveryCandidate.target_relevance_score
        nulls = column.is_(None)
        ordered = column.desc() if sort_order == "desc" else column.asc()
        return statement.order_by(nulls.asc(), ordered, DiscoveryCandidate.last_seen_at.desc())
    column = DiscoveryCandidate.last_seen_at
    return statement.order_by(
        column.desc() if sort_order == "desc" else column.asc(),
        DiscoveryCandidate.created_at.desc(),
    )


def candidate_public_dict(candidate: DiscoveryCandidate) -> dict:
    """Serialize a candidate with a classified, credential-free failure summary."""
    data = {}
    for column in candidate.__table__.columns:
        value = getattr(candidate, column.name)
        data[column.name] = jsonable_encoder(value) if value is not None else None
    data["industry_enrichment_error"] = redact_text(data.get("industry_enrichment_error"))
    data["enrichment_failure"] = classify_enrichment_error(
        candidate.industry_enrichment_error
        if candidate.industry_enrichment_status == "failed"
        else None
    )
    return data


def quick_relevance_hint(candidate: DiscoveryCandidate, task: SearchTask | None) -> dict:
    """Return a deliberately simple, non-billable relevance hint for list triage."""
    if task is None:
        return {"level": "unknown", "label": "待判断", "reason": "缺少来源搜索条件"}
    if candidate.target_relevance_score is not None:
        return _score_relevance_hint(int(candidate.target_relevance_score))
    filters = task.filters if isinstance(task.filters, dict) else {}
    categories = [
        str(value).strip().casefold()
        for value in filters.get("categories", [])
        if str(value).strip()
    ]
    if not categories:
        return {"level": "unknown", "label": "待判断", "reason": "搜索任务未设置品类"}
    target_terms: list[str] = []
    for category in categories:
        target_terms.append(category)
        target_terms.extend(CATEGORY_ALIASES.get(category, ()))
        for token in re.findall(r"[\w-]+", category):
            target_terms.extend(CATEGORY_ALIASES.get(token, ()))
    text = " ".join(
        str(value or "") for value in (candidate.name, candidate.industry, candidate.domain)
    ).casefold()
    english_terms = [
        term.casefold() for term in target_terms if str(term).isascii() and len(str(term)) >= 3
    ]
    if any(term in text for term in english_terms):
        return {"level": "high", "label": "较相关", "reason": "公司名称或行业命中目标品类"}
    if candidate.industry:
        broad_terms = ("fashion", "apparel", "accessor", "leather", "retail", "consumer goods")
        if any(term in text for term in broad_terms):
            return {"level": "medium", "label": "可能相关", "reason": "行业相近，建议查看官网"}
        return {"level": "low", "label": "可能无关", "reason": f"当前行业：{candidate.industry}"}
    return {
        "level": "medium",
        "label": "可能相关",
        "reason": "由目标搜索条件返回，建议查看官网确认主营产品",
    }


def _score_relevance_hint(score: int) -> dict:
    level = "high" if score >= 65 else "medium" if score >= 40 else "low"
    labels = {"high": "较相关", "medium": "可能相关", "low": "可能无关"}
    return {"level": level, "label": labels[level], "reason": f"正式匹配 {score}%"}


def list_candidates(
    db: Session,
    page: int,
    page_size: int,
    *,
    status: str | None = None,
    task_id: UUID | None = None,
    evaluation_status: str | None = None,
    rating: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
    organization_id: UUID | None = None,
) -> dict:
    statement = _apply_candidate_filters(
        select(DiscoveryCandidate),
        status=status,
        task_id=task_id,
        evaluation_status=evaluation_status,
        rating=rating,
        min_score=min_score,
        max_score=max_score,
        organization_id=organization_id,
    )
    statement = _candidate_sort(statement, sort_by, sort_order)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    tasks = {
        item.id: item
        for item in db.scalars(
            select(SearchTask).where(
                SearchTask.id.in_([item.last_task_id for item in items if item.last_task_id])
            )
        )
    }
    serialized = []
    for item in items:
        data = candidate_public_dict(item)
        source_task = tasks.get(item.last_task_id)
        data["search_context"] = task_match_context(source_task) if source_task else None
        data["relevance_hint"] = quick_relevance_hint(item, source_task)
        serialized.append(data)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": serialized,
    }


def _hit_sort(statement, sort_by: str, sort_order: str):
    if sort_by == "target_relevance_score":
        column = DiscoveryCandidateHit.target_relevance_score
        nulls = column.is_(None)
        ordered = column.desc() if sort_order == "desc" else column.asc()
        return statement.order_by(nulls.asc(), ordered, DiscoveryCandidateHit.created_at.desc())
    column = DiscoveryCandidate.last_seen_at
    return statement.order_by(
        column.desc() if sort_order == "desc" else column.asc(),
        DiscoveryCandidateHit.created_at.desc(),
    )


def task_match_context(task: SearchTask) -> dict[str, Any]:
    """User-facing description of what this search was looking for."""
    intent = task.search_intent if isinstance(task.search_intent, dict) else {}
    concepts = (
        intent.get("target_concepts") if isinstance(intent.get("target_concepts"), list) else []
    )
    filters = task.filters if isinstance(task.filters, dict) else {}
    return {
        "task_id": str(task.id),
        "task_name": task.name,
        "mode": task.mode,
        "task_status": task.status,
        "pipeline_version": task.pipeline_version,
        "original_prompt": intent.get("original_prompt"),
        "target_concepts": [
            {
                "id": item.get("id"),
                "source_text": item.get("source_text"),
                "normalized_label": item.get("normalized_label"),
                "concept_type": item.get("concept_type"),
            }
            for item in concepts
            if isinstance(item, dict)
        ],
        "categories": filters.get("categories") or [],
        "countries": filters.get("countries") or [],
    }


def list_task_candidates(
    db: Session,
    task: SearchTask,
    page: int,
    page_size: int,
    *,
    status: str | None = None,
    evaluation_status: str | None = None,
    rating: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    sort_by: str = "target_relevance_score",
    sort_order: str = "desc",
) -> dict:
    """Task-scoped candidate list: hits are the match facts for THIS task."""
    statement = (
        select(DiscoveryCandidateHit, DiscoveryCandidate)
        .join(DiscoveryCandidate, DiscoveryCandidate.id == DiscoveryCandidateHit.candidate_id)
        .where(DiscoveryCandidateHit.task_id == task.id)
    )
    if status:
        statement = statement.where(DiscoveryCandidate.status == status)
    if evaluation_status:
        statement = statement.where(DiscoveryCandidateHit.evaluation_status == evaluation_status)
    if rating:
        statement = statement.where(DiscoveryCandidateHit.relevance_rating == rating)
    if min_score is not None:
        statement = statement.where(DiscoveryCandidateHit.target_relevance_score >= min_score)
    if max_score is not None:
        statement = statement.where(DiscoveryCandidateHit.target_relevance_score <= max_score)
    statement = _hit_sort(statement, sort_by, sort_order)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for hit, candidate in rows:
        data = candidate_public_dict(candidate)
        # Task-level facts override the candidate-level compatibility mirror so
        # this view always shows THIS task's evaluation, never a later one.
        data["evaluation_status"] = hit.evaluation_status or "pending"
        data["target_relevance_score"] = hit.target_relevance_score
        data["relevance_rating"] = hit.relevance_rating
        data["match_evaluation"] = jsonable_encoder(hit.match_evaluation or {})
        data["evaluated_at"] = jsonable_encoder(hit.evaluated_at) if hit.evaluated_at else None
        data["scoring_policy_version"] = hit.scoring_policy_version
        data["prompt_version"] = hit.prompt_version
        data["evidence_schema_version"] = hit.evidence_schema_version
        data["search_context"] = task_match_context(task)
        data["relevance_hint"] = (
            _score_relevance_hint(int(hit.target_relevance_score))
            if hit.target_relevance_score is not None
            else quick_relevance_hint(candidate, task)
        )
        items.append(data)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "task": task_match_context(task),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Bulk approve eligibility
# ---------------------------------------------------------------------------


def bulk_approve_blocker(
    candidate: DiscoveryCandidate | None,
    hit: DiscoveryCandidateHit | None = None,
) -> tuple[str, str] | None:
    """Return (reason_code, message) when a candidate may not be bulk-approved."""
    if candidate is None:
        return ("NOT_FOUND", "候选不存在")
    if candidate.status not in APPROVABLE_STATUSES:
        return ("INVALID_STATUS", "候选当前状态不允许精准丰富")
    if hit is None:
        return ("TASK_MATCH_NOT_FOUND", "候选不属于指定搜索任务")
    if not (candidate.domain or candidate.website):
        return ("MISSING_WEBSITE", "候选缺少官网域名")
    return None
