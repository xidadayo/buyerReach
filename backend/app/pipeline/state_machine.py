import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.modules.models import DiscoveryCandidate

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"filtering", "rejected", "cancelled", "enriching"},
    "filtering": {"evidence_pending", "rejected", "retryable", "cancelled"},
    "evidence_pending": {"scoring", "retryable", "cancelled"},
    "scoring": {"review", "qualified", "rejected", "retryable", "cancelled"},
    "review": {"qualified", "rejected", "cancelled", "enriching"},
    "qualified": {"enriching", "cancelled"},
    "enriching": {"promoted", "enrichment_failed", "retryable", "cancelled"},
    "enrichment_failed": {"enriching", "rejected", "cancelled"},
    "retryable": {"filtering", "evidence_pending", "scoring", "enriching", "cancelled"},
    "promoted": set(),
    "rejected": set(),
    "cancelled": set(),
}

TASK_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"queued", "running", "cancelled"},
    # `pending` was emitted by early V1 builds; retain one compatibility window.
    "pending": {"running", "cancelled"},
    "queued": {"running", "paused", "cancelled", "failed"},
    "running": {"completed", "partial", "paused", "cancelled", "failed", "queued"},
    "paused": {"queued", "cancelled"},
    "partial": {"queued", "cancelled"},
    "failed": {"queued", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class TransitionContext:
    idempotency_key: str
    attempts: int = 0
    max_attempts: int = 3
    budget_remaining: float | None = None
    incurs_cost: bool = False
    task_cancelled: bool = False


def transition_candidate(
    db: Session,
    candidate: DiscoveryCandidate,
    target: str,
    context: TransitionContext,
    **changes: Any,
) -> bool:
    marker = (
        candidate.raw_data.get("last_transition_key")
        if isinstance(candidate.raw_data, dict)
        else None
    )
    if marker == context.idempotency_key:
        return False
    source = str(candidate.status)
    if target not in TRANSITIONS.get(source, set()):
        raise InvalidTransition(f"Illegal candidate transition: {source} -> {target}")
    if context.task_cancelled and target != "cancelled":
        raise InvalidTransition("Cancelled tasks cannot advance candidates")
    if target == "retryable" and context.attempts >= context.max_attempts:
        raise InvalidTransition("Retry limit exhausted")
    if context.incurs_cost and (context.budget_remaining is None or context.budget_remaining <= 0):
        raise InvalidTransition("Budget exhausted")
    candidate.status = target
    for key, value in changes.items():
        setattr(candidate, key, value)
    candidate.raw_data = {
        **(candidate.raw_data or {}),
        "last_transition_key": context.idempotency_key,
        "last_transition_hash": hashlib.sha256(f"{source}:{target}".encode()).hexdigest(),
    }
    db.add(candidate)
    return True


def transition_task(task: Any, target: str, *, idempotent: bool = True) -> bool:
    source = str(task.status)
    if source == target and idempotent:
        return False
    if target not in TASK_TRANSITIONS.get(source, set()):
        raise InvalidTransition(f"Illegal task transition: {source} -> {target}")
    task.status = target
    return True
