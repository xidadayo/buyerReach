from celery import Celery
from celery.signals import worker_ready
from redis import Redis
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.core.config import settings

celery_app = Celery("buyerreach", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_default_queue = "buyerreach"
celery_app.conf.update(task_track_started=True, task_serializer="json", result_serializer="json")
celery_app.conf.update(
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 1800},
)
celery_app.conf.beat_schedule = {
    "publish-domain-outbox": {
        "task": "buyerreach.publish_domain_outbox",
        "schedule": 5.0,
    },
    "recover-durable-work": {
        "task": "buyerreach.recover_durable_work",
        "schedule": 60.0,
    },
    "schedule-batch-targets": {
        "task": "buyerreach.schedule_batch_targets",
        "schedule": 10.0,
    },
    "dispatch-outreach": {
        "task": "buyerreach.dispatch_outreach",
        "schedule": 30.0,
    },
}


@worker_ready.connect
def recover_queued_search_tasks(**_: object) -> None:
    """Restore durable queued tasks after Redis/worker restarts.

    The database is the source of truth for task state. A short Redis lock makes
    this safe when multiple workers (including dedicated-queue workers) start at
    the same time.
    """
    lock = Redis.from_url(settings.redis_url)
    if not lock.set("buyerreach:recover-queued-tasks", "1", nx=True, ex=60):
        return

    from app.core.database import SessionLocal
    from app.modules.models import DiscoveryCandidate, PipelineStageRun, SearchTask
    from app.shared.enums import TaskStatus
    from app.shared.models import utc_now
    from app.pipeline.state_machine import transition_task

    with SessionLocal() as db:
        stale_before = utc_now() - timedelta(minutes=30)
        stale_tasks = db.scalars(
            select(SearchTask).where(
                SearchTask.status == TaskStatus.running,
                SearchTask.updated_at < stale_before,
            )
        ).all()
        for task in stale_tasks:
            transition_task(task, TaskStatus.queued)
            task.error_message = "Recovered after worker lease timeout"
        stale_runs = db.scalars(
            select(PipelineStageRun).where(
                PipelineStageRun.status == "running",
                or_(
                    PipelineStageRun.lease_expires_at.is_(None),
                    PipelineStageRun.lease_expires_at < utc_now(),
                ),
            )
        ).all()
        for run in stale_runs:
            run.status = "retryable"
            run.error_code = "worker_lost"
            run.error_message = "Recovered after worker lease timeout"

        # ── Batch target recovery ──────────────────────────────────────────
        from app.modules.models import ExactBrandTarget
        from app.modules.batch_exact_brand import EXECUTION_RUNNING, EXECUTION_QUEUED

        stale_targets = db.scalars(
            select(ExactBrandTarget).where(
                ExactBrandTarget.execution_status == EXECUTION_RUNNING,
                or_(
                    ExactBrandTarget.lease_expires_at.is_(None),
                    ExactBrandTarget.lease_expires_at < utc_now(),
                ),
            )
        ).all()
        for t in stale_targets:
            t.execution_status = EXECUTION_QUEUED
            t.error_code = "worker_lost"
            t.error_message = "Recovered after worker lease timeout"
            t.lease_owner = None
            t.lease_expires_at = None

        db.commit()
        task_ids = db.scalars(
            select(SearchTask.id).where(
                SearchTask.status == TaskStatus.queued,
                SearchTask.mode != "batch_exact_brand",
            )
        ).all()
        candidate_ids = db.scalars(
            select(DiscoveryCandidate.id).where(
                DiscoveryCandidate.industry_enrichment_status == "queued"
            )
        ).all()
    for task_id in task_ids:
        execute_search_task_job.apply_async(args=[str(task_id)], queue="buyerreach")
    for candidate_id in candidate_ids:
        enrich_candidate_industry_job.apply_async(
            args=[str(candidate_id)], queue="industry_enrichment"
        )


@celery_app.task(name="buyerreach.recover_durable_work")
def recover_durable_work_job() -> dict:
    recover_queued_search_tasks()
    return {"status": "recovery_scan_completed"}


@celery_app.task(name="buyerreach.publish_domain_outbox")
def publish_domain_outbox_job(limit: int = 100) -> dict:
    from app.core.database import SessionLocal
    from app.modules.services import _notify
    from app.pipeline.outbox import mark_publish_failed, pending_events
    from app.shared.models import utc_now

    published = 0
    failed = 0
    with SessionLocal() as db:
        for event in pending_events(db, limit):
            try:
                _notify(db, event.event_type or event.event_name, event.payload or {}, strict=True)
                event.published_at = utc_now()
                event.last_error = None
                published += 1
            except Exception as exc:
                mark_publish_failed(event, str(exc))
                failed += 1
        db.commit()
    return {"published": published, "failed": failed}


@celery_app.task(name="buyerreach.dispatch_outreach")
def dispatch_outreach_job(limit: int = 100) -> dict:
    """Durably evaluate due mail; external transport remains feature-configured."""
    from app.core.database import SessionLocal
    from app.modules.outreach import dispatch_due_messages
    with SessionLocal() as db:
        result = dispatch_due_messages(db, limit)
        db.commit()
        return result


@celery_app.task(name="buyerreach.execute_search_task")
def execute_search_task_job(task_id: str) -> dict:
    from uuid import UUID

    from app.core.database import SessionLocal
    from app.modules.models import SearchTask
    from app.modules.services import execute_search_task
    from app.shared.enums import TaskStatus

    with SessionLocal() as db:
        try:
            task = execute_search_task(db, UUID(task_id))
            db.commit()
            return {"task_id": str(task.id), "status": task.status, "error_message": task.error_message}
        except Exception as exc:
            db.rollback()
            # execute_search_task commits the running state before the pipeline
            # runs, so without this handler a crash leaves the task "running"
            # forever in the UI.
            task = db.get(SearchTask, UUID(task_id))
            if task is not None and task.status in {TaskStatus.queued, TaskStatus.running}:
                task.status = TaskStatus.failed
                task.error_message = str(exc)[:2000]
                db.commit()
            raise


@celery_app.task(
    name="buyerreach.enrich_candidate_industry",
    queue="industry_enrichment",
    soft_time_limit=240,
    time_limit=270,
)
def enrich_candidate_industry_job(candidate_id: str) -> dict:
    from uuid import UUID
    from app.core.database import SessionLocal
    from app.modules.services import enrich_candidate_industry

    with SessionLocal() as db:
        try:
            result = enrich_candidate_industry(db, UUID(candidate_id))
            db.commit()
            return {
                "candidate_id": candidate_id,
                "status": result.get("industry_enrichment_status"),
            }
        except Exception as exc:
            db.rollback()
            from app.modules.models import DiscoveryCandidate
            from app.shared.models import utc_now

            candidate = db.get(DiscoveryCandidate, UUID(candidate_id))
            if candidate is not None:
                candidate.industry_enrichment_status = "failed"
                candidate.industry_enrichment_error = str(exc)[:2000]
                candidate.industry_enriched_at = utc_now()
                db.commit()
            return {"candidate_id": candidate_id, "status": "failed", "error": str(exc)[:500]}


@celery_app.task(
    bind=True,
    name="buyerreach.execute_batch_target",
    soft_time_limit=300,
    time_limit=360,
)
def execute_batch_target_job(self, target_id: str, lease_token: str) -> dict:
    """Execute the vendor pipeline for a single ExactBrandTarget."""
    from uuid import UUID
    from app.core.database import SessionLocal
    from app.modules.models import BatchImport, ExactBrandTarget, SearchTask as ST
    from app.modules.batch_exact_brand import (
        execute_target_pipeline,
        aggregate_parent_task_status,
    )
    from app.pipeline.state_machine import transition_task

    with SessionLocal() as db:
        try:
            target = db.scalar(
                select(ExactBrandTarget)
                .where(ExactBrandTarget.id == UUID(target_id))
                .with_for_update()
            )
            if target is None:
                return {"target_id": target_id, "status": "not_found"}
            if target.lease_owner != lease_token or target.execution_status != "running":
                return {"target_id": target_id, "status": target.execution_status, "skipped": True}

            # Skip already-completed targets
            result = execute_target_pipeline(db, target)
            target.lease_owner = None
            target.lease_expires_at = None

            # Update parent task status
            if target.search_task_id:
                parent_task = db.get(ST, target.search_task_id)
                if parent_task:
                    new_status = aggregate_parent_task_status(db, target.search_task_id)
                    if parent_task.status != new_status:
                        transition_task(parent_task, new_status)
                    batch = db.get(BatchImport, target.batch_import_id)
                    if batch is not None and new_status in {
                        "completed", "partial", "failed", "cancelled"
                    }:
                        batch.status = new_status

            db.commit()
            return {"target_id": target_id, "status": target.execution_status, **result}
        except Exception as exc:
            db.rollback()
            # Try to mark the target as failed
            with SessionLocal() as db2:
                target2 = db2.get(ExactBrandTarget, UUID(target_id))
                if target2 is not None:
                    target2.execution_status = (
                        "retryable"
                        if int(target2.execution_attempts or 0) < int(target2.max_attempts or 3)
                        else "failed"
                    )
                    target2.error_code = type(exc).__name__
                    target2.error_message = str(exc)[:2000]
                    target2.lease_owner = None
                    target2.lease_expires_at = None
                    db2.commit()
            return {"target_id": target_id, "status": "failed", "error": str(exc)[:500]}


@celery_app.task(name="buyerreach.schedule_batch_targets")
def schedule_batch_targets_job() -> dict:
    """Periodic task: enqueue pending/queued batch targets for execution.

    Respects the max_concurrency setting from the parent task snapshot.
    """
    from app.core.database import SessionLocal
    from app.modules.models import ExactBrandTarget, SearchTask
    from app.pipeline.state_machine import transition_task

    with SessionLocal() as db:
        # Find targets that are queued/pending
        from app.shared.models import utc_now
        from uuid import uuid4

        pending_targets = list(
            db.scalars(
                select(ExactBrandTarget)
                .where(
                    ExactBrandTarget.execution_status.in_(["pending", "queued"]),
                    or_(
                        ExactBrandTarget.lease_expires_at.is_(None),
                        ExactBrandTarget.lease_expires_at < utc_now(),
                    ),
                )
                .order_by(ExactBrandTarget.created_at.asc())
                .limit(10)
                .with_for_update(skip_locked=True)
            ).all()
        )

        enqueued = 0
        skipped = 0
        dispatches: list[tuple[str, str]] = []
        for target in pending_targets:
            parent_task = None
            # Check parent task status
            if target.search_task_id:
                parent_task = db.get(SearchTask, target.search_task_id)
                if parent_task and parent_task.status in ("cancelled", "paused"):
                    skipped += 1
                    continue

            # Check concurrency limit from parent task config
            if target.search_task_id:
                running_count = db.scalar(
                    select(func.count()).select_from(
                        select(ExactBrandTarget).where(
                            ExactBrandTarget.search_task_id == target.search_task_id,
                            ExactBrandTarget.execution_status == "running",
                        ).subquery()
                    )
                ) or 0

                parent_task = db.get(SearchTask, target.search_task_id)
                max_concurrency = 3
                if parent_task and parent_task.configuration_snapshot:
                    max_concurrency = parent_task.configuration_snapshot.get("max_concurrency", 3)

                if running_count >= max_concurrency:
                    skipped += 1
                    continue

            if int(target.execution_attempts or 0) >= int(target.max_attempts or 3):
                target.execution_status = "failed"
                target.error_code = "RETRY_LIMIT_EXCEEDED"
                skipped += 1
                continue

            # Claim durably before publishing. The lease token makes duplicate
            # Celery delivery harmless.
            lease_token = str(uuid4())
            target.execution_status = "running"
            target.execution_attempts = int(target.execution_attempts or 0) + 1
            target.lease_owner = lease_token
            target.lease_expires_at = utc_now() + timedelta(minutes=10)
            dispatches.append((str(target.id), lease_token))
            if parent_task and parent_task.status == "queued":
                transition_task(parent_task, "running")
            enqueued += 1

        db.commit()
        for target_id, lease_token in dispatches:
            execute_batch_target_job.apply_async(
                args=[target_id, lease_token], queue="buyerreach"
            )
        return {"enqueued": enqueued, "skipped": skipped}
