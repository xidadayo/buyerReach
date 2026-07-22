"""State machines for QueryPlan, QuerySlice, and SliceRun.

Every mutation must go through these transition functions so that auditing,
Outbox emission, and guard checks are enforced in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ── Query Plan ──────────────────────────────────────────────────────────────

PLAN_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"review", "locked", "superseded"},
    "review": {"locked", "superseded"},
    "locked": {"superseded"},
    "superseded": set(),
}


class InvalidPlanTransition(ValueError):
    pass


@dataclass(frozen=True)
class PlanTransitionContext:
    actor_id: str | None = None
    force: bool = False


def transition_plan(
    plan: Any,
    target: str,
    context: PlanTransitionContext | None = None,
) -> bool:
    source = str(plan.status)
    if source == target:
        return False
    allowed = PLAN_TRANSITIONS.get(source, set())
    if target not in allowed:
        raise InvalidPlanTransition(f"Illegal plan transition: {source} -> {target}")
    plan.status = target
    return True


# ── Query Slice (within a plan) ─────────────────────────────────────────────

SLICE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"enabled", "disabled", "deleted"},
    "enabled": {"disabled", "deleted"},
    "disabled": {"enabled", "deleted"},
    "deleted": set(),
}


class InvalidSliceTransition(ValueError):
    pass


def transition_slice(slice_obj: Any, target: str) -> bool:
    source = str(slice_obj.status or "draft")
    if source == target:
        return False
    if target not in SLICE_TRANSITIONS.get(source, set()):
        raise InvalidSliceTransition(f"Illegal slice transition: {source} -> {target}")
    slice_obj.status = target
    return True


# ── Slice Run ───────────────────────────────────────────────────────────────

SLICE_RUN_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"leased", "cancelled"},
    "leased": {"running", "retryable", "cancelled"},
    "running": {"completed", "exhausted", "retryable", "failed", "cancelled"},
    "retryable": {"queued", "cancelled"},
    "completed": set(),
    "exhausted": set(),
    "failed": {"retryable", "cancelled"},
    "cancelled": set(),
}


class InvalidSliceRunTransition(ValueError):
    pass


def transition_slice_run(run: Any, target: str) -> bool:
    source = str(run.status)
    if source == target:
        return False
    if target not in SLICE_RUN_TRANSITIONS.get(source, set()):
        raise InvalidSliceRunTransition(f"Illegal slice-run transition: {source} -> {target}")
    run.status = target
    return True
