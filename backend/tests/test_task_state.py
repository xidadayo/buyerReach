from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.models import SearchTask
from app.modules.services import (
    cancel_search_task,
    copy_search_task,
    list_search_tasks,
    pause_search_task,
    queue_search_task,
)
from app.shared.enums import TaskStatus


def test_task_can_pause_cancel_and_copy() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        task = SearchTask(
            name="Task state test",
            mode="brand_discovery",
            status=TaskStatus.queued,
            filters={},
            progress={},
        )
        db.add(task)
        db.commit()

        pause_search_task(db, task.id)
        assert task.status == TaskStatus.paused

        copied = copy_search_task(db, task.id)
        assert copied.status == TaskStatus.draft
        assert copied.name.endswith("(copy)")

        cancel_search_task(db, task.id)
        assert task.status == TaskStatus.cancelled


def test_failed_task_can_be_cancelled() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        task = SearchTask(
            name="Failed task",
            mode="exact_brand",
            status=TaskStatus.failed,
            filters={},
            progress={"brands": 0, "contacts": 0, "emails": 0},
            error_message="Provider returned no results",
        )
        db.add(task)
        db.commit()

        cancel_search_task(db, task.id)

        assert task.status == TaskStatus.cancelled
        assert task.error_message == "Provider returned no results"
        result = list_search_tasks(db)
        assert result["total"] == 0
        assert result["items"] == []
        assert db.get(SearchTask, task.id) is task


def test_completed_task_is_not_requeued() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        task = SearchTask(
            name="Completed task",
            mode="exact_brand",
            status=TaskStatus.completed,
            filters={},
            progress={"brands": 1, "contacts": 0, "emails": 0},
        )
        db.add(task)
        db.commit()

        queued = queue_search_task(db, task.id)

        assert queued is task
        assert task.status == TaskStatus.completed
