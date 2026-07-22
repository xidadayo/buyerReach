"""Task-scoped candidate match review: hits, listings, bulk approve, failures."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.v1.router import bulk_approve_discovery_candidates, list_task_discovery_candidates
from app.core.database import Base
from app.core.deps import RequirePermission
from app.modules import services
from app.modules.discovery_review import (
    backfill_hit_evaluations,
    bulk_approve_blocker,
    candidate_public_dict,
    classify_enrichment_error,
    list_candidates,
    quick_relevance_hint,
    record_task_evaluation,
    redact_text,
)
from app.modules.models import (
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    Role,
    SearchTask,
    User,
)
from app.modules.schemas import DiscoveryCandidateBulkApprove
from app.pipeline.concepts import SearchIntent, TargetConcept
from app.shared.models import utc_now


def database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_task(db: Session, name: str = "bags US", pipeline_version: str = "2.0.0") -> SearchTask:
    concept = TargetConcept(source_text="bags", normalized_label="bags", confidence=90)
    intent = SearchIntent(
        original_prompt="find bag brands",
        source="manual",
        target_concepts=[concept],
        overall_confidence=90,
    )
    task = SearchTask(
        name=name,
        mode="brand_discovery",
        status="completed",
        filters={"categories": ["bags"], "countries": ["US"]},
        progress={},
        configuration_snapshot={},
        pipeline_version=pipeline_version,
        search_intent=intent.model_dump(mode="json"),
    )
    db.add(task)
    db.flush()
    return task


def make_candidate(
    db: Session,
    name: str = "Acme",
    task: SearchTask | None = None,
    **overrides,
) -> DiscoveryCandidate:
    candidate = DiscoveryCandidate(
        name=name,
        normalized_name=name.casefold(),
        dedupe_key=f"{name.casefold()}:{uuid4()}",
        provider="test",
        status="pending",
        domain=f"{name.casefold()}.com",
        website=f"https://{name.casefold()}.com",
        country="US",
        industry="Luggage",
        industry_source="official_website_ai",
        industry_confidence=90,
        raw_data={},
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        last_task_id=task.id if task else None,
    )
    for key, value in overrides.items():
        setattr(candidate, key, value)
    db.add(candidate)
    db.flush()
    return candidate


def make_hit(db: Session, candidate: DiscoveryCandidate, task: SearchTask) -> DiscoveryCandidateHit:
    hit = DiscoveryCandidateHit(
        candidate_id=candidate.id, task_id=task.id, relevance_score=0, provider="test"
    )
    db.add(hit)
    db.flush()
    return hit


def evaluated_candidate(
    db: Session, candidate: DiscoveryCandidate, task: SearchTask, score: int = 92, rating: str = "A"
) -> DiscoveryCandidate:
    candidate.evaluation_status = "completed"
    candidate.target_relevance_score = score
    candidate.relevance_rating = rating
    candidate.match_evaluation = {
        "evaluation_status": "completed",
        "target_relevance_score": score,
        "rating": rating,
        "matched_concepts": [],
        "conflicting_concepts": [],
        "dimension_scores": {"product_fit": 40},
        "penalties": [],
        "reason_codes": ["bounded_concept_scope"],
        "policy_version": "relevance-2.0.0",
        "evidence_schema_version": "2.0.0",
    }
    db.flush()
    return candidate


# ---------------------------------------------------------------------------
# 1-2. Formal evaluation persists onto the correct per-task hit
# ---------------------------------------------------------------------------


def test_formal_evaluation_writes_task_hit() -> None:
    with database() as db:
        task = make_task(db)
        candidate = make_candidate(db, task=task)
        make_hit(db, candidate, task)
        evaluated_candidate(db, candidate, task, score=88, rating="B")
        record_task_evaluation(db, task, candidate)
        db.flush()
        hit = db.scalar(select(DiscoveryCandidateHit))
        assert hit.evaluation_status == "completed"
        assert hit.target_relevance_score == 88
        assert hit.relevance_rating == "B"
        assert hit.match_evaluation["policy_version"] == "relevance-2.0.0"
        assert hit.evaluated_at is not None
        assert hit.scoring_policy_version == "relevance-2.0.0"
        assert hit.prompt_version == "concept-match-2.0.0"
        assert hit.evidence_schema_version == "2.0.0"


def test_rescore_path_writes_hit_via_real_evaluation() -> None:
    """The production write path (_rescore_enriched_candidate) updates the hit."""
    from app.modules import industry_enrichment

    concept = TargetConcept(source_text="bags", normalized_label="bags", confidence=90)
    matches = [
        {
            "target_concept_id": concept.id,
            "company_concept": "bags",
            "relationship": "exact",
            "hierarchy_distance": 0,
            "context_compatible": True,
            "evidence_level": "official_website",
            "confidence": 95,
            "evidence_refs": [],
        }
    ]
    original = industry_enrichment.match_company_concepts
    industry_enrichment.match_company_concepts = lambda *_args, **_kwargs: matches
    try:
        with database() as db:
            task = make_task(db)
            # Rebuild the task intent with the known concept id.
            intent = SearchIntent(
                original_prompt="find bag brands",
                source="manual",
                target_concepts=[concept],
                overall_confidence=90,
            )
            task.search_intent = intent.model_dump(mode="json")
            candidate = make_candidate(db, task=task)
            make_hit(db, candidate, task)
            services._rescore_enriched_candidate(db, candidate, {"enabled": False})
            db.flush()
            hit = db.scalar(select(DiscoveryCandidateHit))
            assert candidate.evaluation_status == "completed"
            assert candidate.target_relevance_score == 100
            assert hit.target_relevance_score == candidate.target_relevance_score
            assert hit.relevance_rating == candidate.relevance_rating == "A"
            assert hit.evaluation_status == "completed"
    finally:
        industry_enrichment.match_company_concepts = original


def test_same_candidate_two_tasks_do_not_overwrite() -> None:
    with database() as db:
        task_a = make_task(db, name="search A")
        task_b = make_task(db, name="search B")
        candidate = make_candidate(db, task=task_a)
        hit_a = make_hit(db, candidate, task_a)
        hit_b = make_hit(db, candidate, task_b)

        evaluated_candidate(db, candidate, task_a, score=91, rating="A")
        record_task_evaluation(db, task_a, candidate)
        # Later search evaluates the same brand differently (different intent).
        candidate.last_task_id = task_b.id
        evaluated_candidate(db, candidate, task_b, score=30, rating="D")
        record_task_evaluation(db, task_b, candidate)
        db.flush()

        db.refresh(hit_a)
        db.refresh(hit_b)
        assert hit_a.target_relevance_score == 91 and hit_a.relevance_rating == "A"
        assert hit_b.target_relevance_score == 30 and hit_b.relevance_rating == "D"


# ---------------------------------------------------------------------------
# 3-5. Task-level listing, NULL-last sorting, filters
# ---------------------------------------------------------------------------


def test_task_level_list_returns_only_that_tasks_facts() -> None:
    with database() as db:
        task_a = make_task(db, name="search A")
        task_b = make_task(db, name="search B")
        candidate = make_candidate(db, task=task_b)
        hit_a = make_hit(db, candidate, task_a)
        evaluated_candidate(db, candidate, task_a, score=77, rating="B")
        record_task_evaluation(db, task_a, candidate)
        # The compatibility mirror now points at task B with a different score.
        candidate.last_task_id = task_b.id
        candidate.target_relevance_score = 10
        candidate.relevance_rating = "D"
        db.flush()

        user = User(email="u@x.com", name="u")
        result = list_task_discovery_candidates(
            task_a.id,
            candidate_status=None,
            evaluation_status=None,
            rating=None,
            min_score=None,
            max_score=None,
            sort_by="target_relevance_score",
            sort_order="desc",
            page=1,
            page_size=50,
            user=user,
            db=db,
        )
        assert result["total"] == 1
        item = result["items"][0]
        assert item["target_relevance_score"] == 77
        assert item["relevance_rating"] == "B"
        assert result["task"]["task_name"] == "search A"
        assert result["task"]["target_concepts"][0]["normalized_label"] == "bags"

        result_b = list_task_discovery_candidates(
            task_b.id,
            candidate_status=None,
            evaluation_status=None,
            rating=None,
            min_score=None,
            max_score=None,
            sort_by="target_relevance_score",
            sort_order="desc",
            page=1,
            page_size=50,
            user=user,
            db=db,
        )
        assert result_b["total"] == 0
        db.expire(hit_a)


def test_null_scores_always_sort_after_scored() -> None:
    with database() as db:
        task = make_task(db)
        scored_low = evaluated_candidate(db, make_candidate(db, "Low", task=task), task, 5, "D")
        unscored = make_candidate(db, "Unscored", task=task)
        scored_high = evaluated_candidate(db, make_candidate(db, "High", task=task), task, 95, "A")
        db.flush()

        for order in ("asc", "desc"):
            result = list_candidates(db, 1, 50, sort_by="target_relevance_score", sort_order=order)
            names = [item["name"] for item in result["items"]]
            assert names[-1] == "Unscored"
        desc_names = [
            item["name"]
            for item in list_candidates(
                db, 1, 50, sort_by="target_relevance_score", sort_order="desc"
            )["items"]
        ]
        assert desc_names == ["High", "Low", "Unscored"]
        assert scored_low.id != scored_high.id and unscored.target_relevance_score is None


def test_rating_status_and_score_filters() -> None:
    with database() as db:
        task = make_task(db)
        evaluated_candidate(db, make_candidate(db, "A1", task=task), task, 90, "A")
        evaluated_candidate(db, make_candidate(db, "B1", task=task), task, 70, "B")
        weak = evaluated_candidate(db, make_candidate(db, "D1", task=task), task, 20, "D")
        weak.evaluation_status = "failed"
        weak.target_relevance_score = None
        weak.relevance_rating = None
        db.flush()

        only_a = list_candidates(db, 1, 50, rating="A")
        assert [item["name"] for item in only_a["items"]] == ["A1"]
        failed = list_candidates(db, 1, 50, evaluation_status="failed")
        assert [item["name"] for item in failed["items"]] == ["D1"]
        ranged = list_candidates(db, 1, 50, min_score=60, max_score=95)
        assert {item["name"] for item in ranged["items"]} == {"A1", "B1"}
        by_task = list_candidates(db, 1, 50, task_id=task.id)
        assert by_task["total"] == 0  # no hits recorded for this task yet


# ---------------------------------------------------------------------------
# 6. Permissions and organization isolation
# ---------------------------------------------------------------------------


def test_viewer_role_cannot_execute_bulk_approve() -> None:
    with database() as db:
        role = Role(name="viewer", permissions={})
        db.add(role)
        db.flush()
        user = User(email="v@x.com", name="v", role_id=role.id)
        guard = RequirePermission("tasks:execute")
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(guard(user=user, db=db))
        assert excinfo.value.status_code == 403


def test_cross_organization_task_is_hidden() -> None:
    with database() as db:
        task = make_task(db)
        task.organization_id = uuid4()
        db.flush()
        user = User(email="o@x.com", name="o", organization_id=uuid4())
        with pytest.raises(HTTPException) as excinfo:
            list_task_discovery_candidates(
                task.id,
                candidate_status=None,
                evaluation_status=None,
                rating=None,
                min_score=None,
                max_score=None,
                sort_by="target_relevance_score",
                sort_order="desc",
                page=1,
                page_size=50,
                user=user,
                db=db,
            )
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# 7-11. Bulk approve
# ---------------------------------------------------------------------------


def qualified(db: Session, name: str, task: SearchTask, rating: str = "A") -> DiscoveryCandidate:
    candidate = make_candidate(db, name, task=task)
    evaluated_candidate(db, candidate, task, 90 if rating == "A" else 70, rating)
    make_hit(db, candidate, task)
    record_task_evaluation(db, task, candidate)
    return candidate


def run_bulk(db: Session, ids: list, monkeypatch: pytest.MonkeyPatch) -> dict:
    delays: list[str] = []
    monkeypatch.setattr(
        "app.api.v1.router.execute_search_task_job.delay", lambda task_id: delays.append(task_id)
    )
    first_candidate = db.get(DiscoveryCandidate, ids[0])
    payload = DiscoveryCandidateBulkApprove(
        task_id=first_candidate.last_task_id,
        candidate_ids=ids,
        target_titles=["Buyer"],
    )
    result = bulk_approve_discovery_candidates(
        payload, user=User(email="u@x.com", name="u"), _executor=None, db=db
    )
    return result, delays


def test_bulk_approve_allows_user_selected_candidates_without_ab_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with database() as db:
        task = make_task(db)
        good = qualified(db, "Good", task, "A")
        good_b = qualified(db, "GoodB", task, "B")
        rated_c = qualified(db, "Mid", task, "C")
        pending_eval = make_candidate(db, "PendingEval", task=task)
        insufficient = make_candidate(
            db, "Insuff", task=task, evaluation_status="insufficient_data"
        )
        failed_eval = make_candidate(db, "FailedEval", task=task, evaluation_status="failed")
        make_hit(db, pending_eval, task)
        make_hit(db, insufficient, task)
        record_task_evaluation(db, task, insufficient)
        make_hit(db, failed_eval, task)
        record_task_evaluation(db, task, failed_eval)
        no_site = qualified(db, "NoSite", task, "A")
        no_site.domain = None
        no_site.website = None
        db.flush()

        result, delays = run_bulk(
            db,
            [
                c.id
                for c in (good, good_b, rated_c, pending_eval, insufficient, failed_eval, no_site)
            ],
            monkeypatch,
        )
        assert result["approved"] == 6
        assert result["skipped"] == 1
        assert result["failed"] == 0
        by_id = {item["candidate_id"]: item for item in result["items"]}
        assert by_id[str(no_site.id)]["reason_code"] == "MISSING_WEBSITE"
        db.refresh(good)
        db.refresh(good_b)
        assert good.status == "enriching" and good_b.status == "enriching"
        assert len(delays) == 6  # one queued task per approved candidate


def test_bulk_approve_unknown_and_missing_candidates_skip() -> None:
    with database() as db:
        assert bulk_approve_blocker(None) == ("NOT_FOUND", "候选不存在")
        candidate = make_candidate(db, "Ghost")
        candidate.status = "promoted"
        assert bulk_approve_blocker(candidate)[0] == "INVALID_STATUS"


def test_single_failure_does_not_rollback_others(monkeypatch: pytest.MonkeyPatch) -> None:
    with database() as db:
        task = make_task(db)
        good = qualified(db, "Good", task)
        bad = qualified(db, "Bad", task)
        db.flush()

        original = services.approve_discovery_candidate

        def flaky(db_, candidate, **kwargs):
            if candidate.id == bad.id:
                raise RuntimeError("simulated crash")
            return original(db_, candidate, **kwargs)

        monkeypatch.setattr("app.api.v1.router.services.approve_discovery_candidate", flaky)
        result, _ = run_bulk(db, [good.id, bad.id], monkeypatch)
        assert result["approved"] == 1
        assert result["failed"] == 1
        by_id = {item["candidate_id"]: item for item in result["items"]}
        assert by_id[str(bad.id)]["reason_code"] == "TASK_CREATE_FAILED"
        db.refresh(good)
        assert good.status == "enriching"  # committed despite the later failure


def test_repeat_bulk_request_creates_no_duplicate_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    with database() as db:
        task = make_task(db)
        good = qualified(db, "Good", task)
        db.flush()
        first, delays_first = run_bulk(db, [good.id], monkeypatch)
        assert first["approved"] == 1
        task_count = len(db.scalars(select(SearchTask)).all())

        second, delays_second = run_bulk(db, [good.id], monkeypatch)
        assert second["approved"] == 0
        assert second["skipped"] == 1
        assert second["items"][0]["reason_code"] == "INVALID_STATUS"
        assert len(db.scalars(select(SearchTask)).all()) == task_count
        assert delays_second == [] and len(delays_first) == 1


def test_bulk_approve_accepts_selected_task_hit_regardless_of_rating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with database() as db:
        selected_task = make_task(db, name="selected")
        later_task = make_task(db, name="later")
        candidate = qualified(db, "TaskScoped", selected_task, "C")
        # Simulate a later search overwriting the compatibility mirror with A.
        candidate.last_task_id = later_task.id
        candidate.evaluation_status = "completed"
        candidate.target_relevance_score = 95
        candidate.relevance_rating = "A"
        db.flush()
        monkeypatch.setattr("app.api.v1.router.execute_search_task_job.delay", lambda _: None)

        payload = DiscoveryCandidateBulkApprove(
            task_id=selected_task.id,
            candidate_ids=[candidate.id],
            target_titles=["Buyer"],
        )
        result = bulk_approve_discovery_candidates(
            payload, user=User(email="u@x.com", name="u"), _executor=None, db=db
        )

        assert result["approved"] == 1
        assert result["items"][0]["status"] == "approved"
        assert candidate.status == "enriching"


def test_bulk_approve_hides_cross_organization_task() -> None:
    with database() as db:
        task = make_task(db)
        task.organization_id = uuid4()
        candidate = qualified(db, "Tenant", task)
        db.flush()
        payload = DiscoveryCandidateBulkApprove(
            task_id=task.id, candidate_ids=[candidate.id], target_titles=["Buyer"]
        )

        with pytest.raises(HTTPException) as excinfo:
            bulk_approve_discovery_candidates(
                payload,
                user=User(email="other@x.com", name="other", organization_id=uuid4()),
                _executor=None,
                db=db,
            )
        assert excinfo.value.status_code == 404


def test_celery_enqueue_happens_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    with database() as db:
        task = make_task(db)
        good = qualified(db, "Good", task)
        db.flush()
        db.commit()

        order: list[str] = []
        real_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (order.append("commit"), real_commit()))
        monkeypatch.setattr(
            "app.api.v1.router.execute_search_task_job.delay",
            lambda task_id: order.append(f"delay:{task_id}"),
        )
        payload = DiscoveryCandidateBulkApprove(
            task_id=task.id, candidate_ids=[good.id], target_titles=["Buyer"]
        )
        result = bulk_approve_discovery_candidates(
            payload, user=User(email="u@x.com", name="u"), _executor=None, db=db
        )
        assert result["approved"] == 1
        assert order[0] == "commit"
        assert order[1].startswith("delay:")


# ---------------------------------------------------------------------------
# 12. Backfill: only the origin task's hit receives history, idempotent
# ---------------------------------------------------------------------------


def test_backfill_copies_only_origin_task_hit_and_is_idempotent() -> None:
    with database() as db:
        task_origin = make_task(db, name="origin")
        task_other = make_task(db, name="other")
        candidate = make_candidate(db, task=task_origin)
        hit_origin = make_hit(db, candidate, task_origin)
        hit_other = make_hit(db, candidate, task_other)
        evaluated_candidate(db, candidate, task_origin, score=83, rating="B")
        db.flush()

        updated = backfill_hit_evaluations(db)
        assert updated == 1
        db.refresh(hit_origin)
        db.refresh(hit_other)
        assert hit_origin.target_relevance_score == 83
        assert hit_origin.evaluation_status == "completed"
        assert hit_other.target_relevance_score is None
        assert hit_other.evaluated_at is None

        # Re-running copies nothing and leaves both hits untouched.
        assert backfill_hit_evaluations(db) == 0
        db.refresh(hit_origin)
        assert hit_origin.target_relevance_score == 83

        # A candidate whose last_task_id moved away must not backfill the old hit.
        orphan = make_candidate(db, "Orphan", task=task_other)
        hit_orphan = make_hit(db, orphan, task_origin)
        evaluated_candidate(db, orphan, task_other, score=66, rating="B")
        db.flush()
        assert backfill_hit_evaluations(db) == 0
        db.refresh(hit_orphan)
        assert hit_orphan.evaluated_at is None


# ---------------------------------------------------------------------------
# 13-14. Safe serialization and failure classification
# ---------------------------------------------------------------------------


def test_unknown_states_serialize_safely() -> None:
    with database() as db:
        candidate = make_candidate(db, "Future")
        candidate.evaluation_status = "some_future_status"
        candidate.relevance_rating = "Z"
        candidate.status = "mystery"
        db.flush()
        data = candidate_public_dict(candidate)
        assert data["evaluation_status"] == "some_future_status"
        assert data["enrichment_failure"]["code"] == "unknown"


def test_failure_classification_and_secret_redaction() -> None:
    classified = classify_enrichment_error(
        "Hunter quota unavailable: Provider reports no remaining quota; "
        "provider reports reset at 2026-08-01T00:00:00Z"
    )
    assert classified["code"] == "quota_exhausted"
    assert classified["reset_at"] == "2026-08-01T00:00:00Z"

    assert (
        classify_enrichment_error("Enabled Hunter Company Enrichment is unavailable")["code"]
        == "provider_not_configured"
    )
    assert (
        classify_enrichment_error("AI website classification: TLS handshake failed")["code"]
        == "ai_or_network"
    )
    assert (
        classify_enrichment_error("Official website: connection refused")["code"]
        == "website_unreachable"
    )
    assert (
        classify_enrichment_error("Official website did not provide enough descriptive text")[
            "code"
        ]
        == "insufficient_evidence"
    )

    leaked = (
        "AI website classification: HTTP 401 Bearer sk-SECRET-KEY-123 api_key=sk-SECRET-KEY-123"
    )
    redacted = redact_text(leaked)
    assert "sk-SECRET-KEY-123" not in redacted

    with database() as db:
        candidate = make_candidate(db, "Leaky")
        candidate.industry_enrichment_status = "failed"
        candidate.industry_enrichment_error = leaked
        db.flush()
        data = candidate_public_dict(candidate)
        assert "sk-SECRET-KEY-123" not in str(data)


def test_quick_relevance_hint_is_simple_and_useful_without_formal_evaluation() -> None:
    task = SearchTask(
        name="US luggage",
        mode="brand_discovery",
        filters={"categories": ["箱包"], "countries": ["US"]},
        progress={},
    )
    related = DiscoveryCandidate(name="Travel Bags", industry="Luggage Manufacturing")
    unrelated = DiscoveryCandidate(name="Stand Plus", industry="Footwear Manufacturing")

    assert quick_relevance_hint(related, task)["level"] == "high"
    hint = quick_relevance_hint(unrelated, task)
    assert hint["level"] == "low"
    assert hint["reason"] == "当前行业：Footwear Manufacturing"

    unknown_industry = DiscoveryCandidate(name="Poshyc", domain="poshyc.com")
    hint = quick_relevance_hint(unknown_industry, task)
    assert hint["level"] == "medium"
    assert hint["label"] == "可能相关"
    assert "建议查看官网" in hint["reason"]
