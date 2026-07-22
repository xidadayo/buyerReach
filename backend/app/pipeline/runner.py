import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.models import PipelineStageRun, SearchTask
from app.pipeline.stages import Stage
from app.pipeline.definition import PIPELINE_V1, PIPELINE_V2
from app.shared.models import utc_now


def input_hash(payload: dict[str, Any], version_salt: str = "") -> str:
    raw = json.dumps(
        {"payload": payload, "version": version_salt},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def run_stage_once(
    db: Session,
    task_id: UUID,
    candidate_id: UUID | None,
    stage: Stage,
    context: dict[str, Any],
    payload: dict[str, Any],
) -> PipelineStageRun:
    digest = input_hash(payload, stage.version)
    key = ":".join(
        (
            str(task_id),
            str(candidate_id) if candidate_id else "task",
            stage.name,
            stage.version,
            digest,
        )
    )
    existing = db.scalar(select(PipelineStageRun).where(PipelineStageRun.idempotency_key == key))
    if existing is not None:
        return existing
    run = PipelineStageRun(
        task_id=task_id,
        candidate_id=candidate_id,
        stage_name=stage.name,
        stage_version=stage.version,
        input_hash=digest,
        idempotency_key=key,
        input_payload=payload,
        status="running",
        attempts=1,
    )
    db.add(run)
    db.flush()
    if not stage.can_run(context):
        run.status = "cancelled" if context.get("cancelled") else "blocked"
        return run
    run.output_payload = stage.execute(context, payload)
    run.status = "completed"
    return run


def begin_stage(
    db: Session,
    task_id: UUID,
    stage_name: str,
    payload: dict[str, Any],
    candidate_id: UUID | None = None,
) -> PipelineStageRun:
    task = db.get(SearchTask, task_id)
    definition = PIPELINE_V2 if task is not None and task.pipeline_version == "2.0.0" else PIPELINE_V1
    aliases = {"candidate_filtering": "candidate_prefiltering", "industry_enrichment": "company_profile_extraction",
               "ai_relevance_scoring": "concept_scope_matching"}
    persisted_name = aliases.get(stage_name, stage_name) if definition is PIPELINE_V2 else stage_name
    version = definition.stage_versions[persisted_name]
    digest = input_hash(payload, version)
    key = ":".join(
        (str(task_id), str(candidate_id) if candidate_id else "task", persisted_name, version, digest)
    )
    run = db.scalar(select(PipelineStageRun).where(PipelineStageRun.idempotency_key == key))
    if run is None:
        run = PipelineStageRun(
            task_id=task_id,
            candidate_id=candidate_id,
            stage_name=persisted_name,
            stage_version=version,
            input_hash=digest,
            idempotency_key=key,
            input_payload=payload,
        )
        db.add(run)
    if run.status != "completed":
        run.status = "running"
        run.attempts = int(run.attempts or 0) + 1
        run.started_at = utc_now()
        run.lease_expires_at = utc_now() + timedelta(minutes=30)
        run.error_code = None
        run.error_message = None
    db.flush()
    return run


def complete_stage(run: PipelineStageRun, output: dict[str, Any], *, cost: float = 0) -> None:
    run.output_payload = output
    run.cost = float(run.cost or 0) + cost
    run.status = "completed"
    run.completed_at = utc_now()
    run.lease_expires_at = None


def fail_stage(run: PipelineStageRun, error: Exception, retryable: bool = True) -> None:
    run.status = "retryable" if retryable else "failed"
    run.error_code = error.__class__.__name__
    run.error_message = str(error)[:2000]
    run.completed_at = utc_now()
    run.lease_expires_at = None
