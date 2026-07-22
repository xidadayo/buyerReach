from celery import Celery
from celery.signals import worker_ready
from redis import Redis
from datetime import timedelta

from sqlalchemy import or_, select

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
        db.commit()
        task_ids = db.scalars(
            select(SearchTask.id).where(SearchTask.status == TaskStatus.queued)
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


@celery_app.task(name="buyerreach.execute_search_task")
def execute_search_task_job(task_id: str) -> dict:
    from uuid import UUID

    from app.core.database import SessionLocal
    from app.modules.services import execute_search_task

    with SessionLocal() as db:
        task = execute_search_task(db, UUID(task_id))
        db.commit()
        return {"task_id": str(task.id), "status": task.status, "error_message": task.error_message}


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
