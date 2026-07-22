"""Phase A tests — Query Plan / Slice models, state machines, normalization, and locking.

Run: python -m pytest tests/test_query_plan_models.py -q
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import (
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    SearchQueryPlan,
    SearchQuerySlice,
    SearchQuerySliceRun,
    SearchTask,
)
from app.query_planning.generator import generate_slices_from_intent, plan_summary
from app.query_planning.normalization import (
    normalize_country,
    normalize_list,
    normalize_text,
    slice_normalized_hash,
)
from app.query_planning.state_machine import (
    InvalidPlanTransition,
    InvalidSliceRunTransition,
    transition_plan,
    transition_slice,
    transition_slice_run,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_plan(status: str = "draft") -> SearchQueryPlan:
    return SearchQueryPlan(
        id=str(uuid4()), task_id=str(uuid4()), status=status, version=1,
    )


def _make_slice(status: str = "draft") -> SearchQuerySlice:
    return SearchQuerySlice(
        id=str(uuid4()), plan_id=str(uuid4()), slice_key="s1",
        label="Test", normalized_hash="abc", status=status,
    )


def _make_run(status: str = "queued") -> SearchQuerySliceRun:
    return SearchQuerySliceRun(
        id=str(uuid4()), task_id=str(uuid4()), plan_id=str(uuid4()),
        query_slice_id=str(uuid4()), plan_version=1,
        provider="test", operation="discover",
        input_hash="abc", cursor_key="page-1", status=status,
    )


def _make_intent(
    target_concepts: list[dict],
    countries: list[str] | None = None,
    business_types: list[str] | None = None,
) -> dict:
    """Build a minimal valid SearchIntent dict, adding required defaults."""
    for tc in target_concepts:
        tc.setdefault("confidence", 60)
        tc.setdefault("inferred", False)
        tc.setdefault("relation_scope", ["exact", "synonym", "child", "descendant"])
        tc.setdefault("max_hierarchy_distance", 2)
        tc.setdefault("minimum_evidence_level", "company_product")
        tc.setdefault("included_concepts", [])
        tc.setdefault("excluded_concepts", [])
        tc.setdefault("required_contexts", [])
        tc.setdefault("excluded_contexts", [])
        tc.setdefault("inherited_qualifiers", [])

    qualifiers = []
    for cid in [tc["id"] for tc in target_concepts]:
        for c in (countries or []):
            qualifiers.append({
                "type": "country", "value": c,
                "applies_to": [cid], "source_text": c,
                "confidence": 100, "inferred": False,
            })

    return {
        "schema_version": "2.0.0",
        "original_prompt": "test",
        "source": "local_rules",
        "target_concepts": target_concepts,
        "global_qualifiers": qualifiers,
        "target_industries": [],
        "excluded_industries": [],
        "business_types": business_types or [],
        "category_match_mode": "any",
        "ambiguities": [],
        "overall_confidence": 60,
        "knowledge_sources": [],
    }


# ── Normalization ───────────────────────────────────────────────────────────


def test_normalize_country_iso() -> None:
    assert normalize_country("IT") == "it"
    assert normalize_country("  fr  ") == "fr"
    assert normalize_country("united kingdom") == "gb"
    assert normalize_country("germany") == "de"
    assert normalize_country("") is None
    assert normalize_country("  ") is None


def test_normalize_text() -> None:
    assert normalize_text("Hello") == "hello"
    assert normalize_text("  World  ") == "world"


def test_normalize_list_deduplicates_and_sorts() -> None:
    assert normalize_list(["B", "a", "b", " A "]) == ["a", "b"]


def test_slice_normalized_hash_stable() -> None:
    h1 = slice_normalized_hash(
        countries=["IT", "FR"], target_concepts=["handbag"],
        business_types=["brand"], include_terms=[], exclude_terms=[],
        match_mode="any", purpose="core",
    )
    h2 = slice_normalized_hash(
        countries=["FR", "IT"], target_concepts=["handbag"],
        business_types=["brand"], include_terms=[], exclude_terms=[],
        match_mode="any", purpose="core",
    )
    assert h1 == h2


def test_slice_normalized_hash_different() -> None:
    h1 = slice_normalized_hash(
        countries=["IT"], target_concepts=["handbag"], business_types=[],
        include_terms=[], exclude_terms=[], match_mode="any", purpose="core",
    )
    h2 = slice_normalized_hash(
        countries=["IT"], target_concepts=["shoes"], business_types=[],
        include_terms=[], exclude_terms=[], match_mode="any", purpose="core",
    )
    assert h1 != h2


# ── State machine ───────────────────────────────────────────────────────────


def test_plan_state_transitions() -> None:
    plan = _make_plan("draft")
    assert transition_plan(plan, "review")
    assert plan.status == "review"
    assert transition_plan(plan, "locked")
    assert plan.status == "locked"
    assert transition_plan(plan, "superseded")
    assert plan.status == "superseded"
    assert transition_plan(plan, "superseded") is False


def test_plan_invalid_transition() -> None:
    plan = _make_plan("draft")
    with pytest.raises(InvalidPlanTransition):
        transition_plan(plan, "completed")


def test_plan_locked_to_draft_blocked() -> None:
    plan = _make_plan("locked")
    with pytest.raises(InvalidPlanTransition):
        transition_plan(plan, "draft")


def test_slice_state_transitions() -> None:
    sl = _make_slice("draft")
    assert transition_slice(sl, "enabled")
    assert sl.status == "enabled"
    assert transition_slice(sl, "disabled")
    assert sl.status == "disabled"
    assert transition_slice(sl, "enabled")
    assert sl.status == "enabled"
    assert transition_slice(sl, "deleted")
    assert sl.status == "deleted"
    assert transition_slice(sl, "deleted") is False


def test_slice_run_state_transitions() -> None:
    run = _make_run("queued")
    assert transition_slice_run(run, "leased")
    assert transition_slice_run(run, "running")
    assert transition_slice_run(run, "completed")
    assert transition_slice_run(run, "completed") is False


def test_slice_run_invalid_transition() -> None:
    run = _make_run("queued")
    with pytest.raises(InvalidSliceRunTransition):
        transition_slice_run(run, "completed")


def test_slice_run_retryable_loop() -> None:
    run = _make_run("queued")
    assert transition_slice_run(run, "leased")
    assert transition_slice_run(run, "running")
    assert transition_slice_run(run, "retryable")
    assert transition_slice_run(run, "queued")
    assert transition_slice_run(run, "leased")
    assert transition_slice_run(run, "cancelled")  # leased -> cancelled allowed
    assert transition_slice_run(run, "cancelled") is False  # terminal


# ── Slice generation ────────────────────────────────────────────────────────


def test_generate_slices_minimum() -> None:
    intent = _make_intent(
        target_concepts=[{
            "id": "c1", "source_text": "handbags",
            "normalized_label": "handbags", "concept_type": "product",
        }],
        countries=["IT"],
    )
    slices = generate_slices_from_intent(intent, preset="precision")
    assert len(slices) >= 1
    core = [s for s in slices if s["purpose"] == "core"]
    assert len(core) >= 1
    assert core[0]["countries"] == ["IT"]


def test_generate_slices_balanced_generates_synonyms() -> None:
    intent = _make_intent(
        target_concepts=[{
            "id": "c1", "source_text": "handbags",
            "normalized_label": "handbags", "concept_type": "product",
            "included_concepts": ["purses", "clutches"],
        }],
        countries=["IT"],
    )
    slices = generate_slices_from_intent(intent, preset="balanced")
    synonyms = [s for s in slices if s["purpose"] == "synonym"]
    assert len(synonyms) >= 1


def test_generate_slices_volume_generates_more() -> None:
    intent = _make_intent(
        target_concepts=[{
            "id": "c1", "source_text": "handbags",
            "normalized_label": "handbags", "concept_type": "product",
            "included_concepts": ["purses"],
        }],
        countries=["FR"],
        business_types=["brand", "retailer"],
    )
    slices = generate_slices_from_intent(intent, preset="volume")
    assert len(slices) >= 4


def test_generate_slices_capped_at_20() -> None:
    intent = _make_intent(
        target_concepts=[
            {
                "id": f"c{i}", "source_text": f"category{i}",
                "normalized_label": f"category{i}", "concept_type": "product",
                "included_concepts": [f"s{i}a", f"s{i}b", f"s{i}c"],
            }
            for i in range(5)
        ],
        countries=["IT"],
        business_types=["brand", "retailer", "importer"],
    )
    slices = generate_slices_from_intent(intent, preset="volume")
    assert len(slices) <= 20


def test_plan_summary() -> None:
    intent = _make_intent(
        target_concepts=[{
            "id": "c1", "source_text": "handbags",
            "normalized_label": "handbags", "concept_type": "product",
        }],
        countries=["IT"],
    )
    slices = generate_slices_from_intent(intent, preset="precision")
    summary = plan_summary(intent, slices)
    assert "handbags" in summary or "Handbags" in summary
    assert "1" in summary  # 1 country


# ── Model persistence (in-memory SQLite) ────────────────────────────────────


def test_query_plan_model_creation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = SearchTask(
            name="test", mode="brand_discovery", status="draft",
            filters={}, progress={},
        )
        db.add(task)
        db.commit()

        plan = SearchQueryPlan(
            task_id=str(task.id), version=1, status="draft",
            target_result_count=100,
        )
        db.add(plan)
        db.commit()

        assert plan.id is not None
        assert plan.status == "draft"
        assert plan.target_result_count == 100


def test_search_task_additive_fields() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = SearchTask(
            name="test", mode="brand_discovery", status="draft",
            filters={}, progress={},
            target_result_count=200,
            candidate_fetch_limit=500,
            repeat_mode="new_only",
            queue_reason="waiting_for_slot",
            active_slice_count=0,
            waiting_slice_count=3,
            completed_slice_count=7,
        )
        db.add(task)
        db.commit()
        assert task.target_result_count == 200
        assert task.repeat_mode == "new_only"
        assert task.completed_slice_count == 7


def test_discovery_candidate_hit_additive_source_fields() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        candidate = DiscoveryCandidate(
            name="Test Brand", normalized_name="test brand",
            dedupe_key="domain:test.com", provider="hunter",
            first_seen_at=text("datetime('now')"),
            last_seen_at=text("datetime('now')"),
        )
        db.add(candidate)
        db.commit()

        hit = DiscoveryCandidateHit(
            candidate_id=candidate.id,
            task_id=uuid4(),
            provider="hunter",
        )
        db.add(hit)
        db.commit()
        assert hit.source_evidence == {}
        # Verify additive nullable columns exist and accept None
        assert hit.plan_id is None
        assert hit.query_slice_id is None
        assert hit.slice_run_id is None
        assert hit.source_record_id is None
