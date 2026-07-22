from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import DiscoveryCandidate, SearchTask, SystemSetting, VendorCredential
from app.modules.schemas import SearchTaskCreate
from app.modules.services import create_search_task
from app.pipeline.definition import PIPELINE_V1, PIPELINE_V2
from app.pipeline.policy import RelevancePolicy
from app.pipeline.runner import input_hash, run_stage_once
from app.pipeline.outbox import add_event, mark_publish_failed
from app.pipeline.stages import FunctionStage, StageRegistry
from app.pipeline.state_machine import InvalidTransition, TransitionContext, transition_candidate


def database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def candidate() -> DiscoveryCandidate:
    from app.shared.models import utc_now

    return DiscoveryCandidate(
        name="Acme",
        normalized_name="acme",
        dedupe_key="acme",
        provider="test",
        status="pending",
        raw_data={},
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
    )


def test_illegal_candidate_transition_is_rejected() -> None:
    with database() as db:
        item = candidate()
        db.add(item)
        db.flush()
        try:
            transition_candidate(db, item, "promoted", TransitionContext("illegal"))
        except InvalidTransition:
            pass
        else:
            raise AssertionError("illegal transition accepted")
        assert item.status == "pending"


def test_transition_idempotency_and_cancellation_budget_guards() -> None:
    with database() as db:
        item = candidate()
        db.add(item)
        db.flush()
        assert transition_candidate(db, item, "filtering", TransitionContext("once"))
        assert not transition_candidate(db, item, "filtering", TransitionContext("once"))
        try:
            transition_candidate(
                db, item, "evidence_pending", TransitionContext("cancelled", task_cancelled=True)
            )
        except InvalidTransition:
            pass
        else:
            raise AssertionError("cancelled task advanced")


def test_same_stage_submission_executes_once_and_versions_change_hash() -> None:
    calls = []
    stage = FunctionStage(
        "provider_search", "1.0.0", lambda _, payload: calls.append(payload) or {"ok": True}
    )
    with database() as db:
        task = SearchTask(name="t", mode="brand_discovery", filters={}, progress={})
        db.add(task)
        db.flush()
        first = run_stage_once(db, task.id, None, stage, {}, {"q": "bags"})
        second = run_stage_once(db, task.id, None, stage, {}, {"q": "bags"})
        assert first.id == second.id and len(calls) == 1
        assert input_hash({"q": "bags"}, "prompt-1") != input_hash({"q": "bags"}, "prompt-2")


def test_task_snapshot_is_secret_free_and_immutable_for_existing_task() -> None:
    with database() as db:
        db.add(VendorCredential(vendor="demo", encrypted_api_key="TEST-API-KEY", enabled=True))
        db.flush()
        payload = SearchTaskCreate(
            name="bags", mode="brand_discovery", countries=["US"], categories=["bags"]
        )
        old = create_search_task(db, payload)
        old_version = old.configuration_version
        assert "TEST-API-KEY" not in str(old.configuration_snapshot)
        db.add(SystemSetting(key="rollout", value={"mode": "active"}))
        db.flush()
        new = create_search_task(db, payload)
        assert old.configuration_version == old_version
        assert new.configuration_version != old_version


def test_confirmed_intent_freezes_pipeline_two_contracts() -> None:
    from app.pipeline.concepts import SearchIntent, TargetConcept

    with database() as db:
        intent = SearchIntent(original_prompt="find suppliers", source="ai",
            target_concepts=[TargetConcept(source_text="component", normalized_label="component", confidence=90)],
            overall_confidence=90)
        task = create_search_task(db, SearchTaskCreate(name="v2", mode="brand_discovery",
            countries=["DE"], categories=["component"], original_prompt="find suppliers",
            search_intent=intent.model_dump(mode="json")))
        assert task.pipeline_version == PIPELINE_V2.pipeline_version
        assert task.intent_schema_version == "2.0.0"
        assert task.intent_prompt_version == "intent-2.0.2"
        assert task.configuration_snapshot["concept_matching"]["scoring_policy_version"] == "relevance-2.0.0"
        assert "api_key" not in str(task.configuration_snapshot).casefold()


def test_policy_only_is_deterministic_and_does_not_need_adapter() -> None:
    score, rating = RelevancePolicy().evaluate(
        {"industry_fit": 100, "market_fit": 80, "buyer_fit": 70, "evidence_quality": 60}
    )
    assert (score, rating) == (83, "A")


def test_pipeline_registry_assembles_all_versioned_stages() -> None:
    registry = StageRegistry()
    for name, version in PIPELINE_V1.stage_versions.items():
        registry.register(FunctionStage(name, version, lambda _c, p: p))
    assert [stage.name for stage in registry.assemble(PIPELINE_V1.stage_versions)] == list(
        PIPELINE_V1.stage_versions
    )


def test_task_vendor_plan_is_frozen_at_creation() -> None:
    from app.modules.models import TaskVendorPlan, VendorStrategy

    with database() as db:
        strategy = VendorStrategy(
            name="default",
            primary_vendor="apollo",
            fallback_vendors=["hunter"],
            adapter_version="v1",
        )
        db.add(strategy)
        db.flush()
        payload = SearchTaskCreate(
            name="bags", mode="brand_discovery", countries=["US"], categories=["bags"]
        )
        old = create_search_task(db, payload)
        strategy.primary_vendor = "hunter"
        strategy.fallback_vendors = []
        new = create_search_task(db, payload)
        old_plan = db.scalar(select(TaskVendorPlan).where(TaskVendorPlan.task_id == old.id))
        new_plan = db.scalar(select(TaskVendorPlan).where(TaskVendorPlan.task_id == new.id))
        assert old_plan is not None and old_plan.primary_vendor == "apollo"
        assert new_plan is not None and new_plan.primary_vendor == "hunter"


def test_outbox_failure_remains_pending_and_redacts_secret() -> None:
    from app.modules.models import DomainEvent
    from app.modules.services import emit

    with database() as db:
        emit(db, "task.created", {"task_id": "task-1", "api_key": "TEST-API-KEY"})
        db.flush()
        event = db.scalar(select(DomainEvent))
        assert event is not None
        assert event.published_at is None
        assert "TEST-API-KEY" not in str(event.payload)
        mark_publish_failed(event, "network unavailable")
        assert event.publish_attempts == 1
        assert event.published_at is None


def test_outbox_sequences_events_per_aggregate() -> None:
    from app.modules.models import DomainEvent

    with database() as db:
        add_event(db, "task.queued", "search_task", "task-1", {})
        db.flush()
        add_event(db, "task.running", "search_task", "task-1", {})
        db.flush()
        assert list(db.scalars(select(DomainEvent.sequence).order_by(DomainEvent.sequence))) == [
            1,
            2,
        ]
