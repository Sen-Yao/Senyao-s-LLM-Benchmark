from pathlib import Path
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.models import LLMModel, Task, TaskChangeEvent, TaskResult
from backend.app.task_registry.loader import sync_tasks_from_dir

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _latest_change(session: Session, task_slug: str) -> dict | None:
    event = (
        session.query(TaskChangeEvent)
        .filter(TaskChangeEvent.task_slug == task_slug)
        .order_by(TaskChangeEvent.id.desc())
        .first()
    )
    if not event:
        return None
    return {
        "id": event.id,
        "task_slug": event.task_slug,
        "change_type": event.change_type,
        "old_hash": event.old_hash,
        "new_hash": event.new_hash,
        "requires_rerun": event.requires_rerun,
        "created_at": event.created_at,
    }


@router.get("")
def list_tasks(session: Session = Depends(get_session)):
    tasks = session.query(Task).order_by(Task.category, Task.slug).all()
    model_count = session.query(LLMModel).count()
    rows = []
    for task in tasks:
        covered = (
            session.query(TaskResult.model_id)
            .filter(
                TaskResult.task_id == task.id,
                TaskResult.task_hash == task.content_hash,
                TaskResult.status == "success",
            )
            .distinct()
            .count()
        )
        stale = (
            session.query(TaskResult.model_id)
            .filter(
                TaskResult.task_id == task.id,
                TaskResult.task_hash != task.content_hash,
                TaskResult.status == "success",
            )
            .distinct()
            .count()
        )
        rows.append(
            {
                "id": task.id,
                "slug": task.slug,
                "title": task.title,
                "category": task.category,
                "dimension": task.dimension,
                "evaluator_type": task.evaluator_type,
                "content_hash": task.content_hash,
                "active": task.active,
                "source_path": task.source_path,
                "covered_models": covered,
                "stale_models": stale,
                "pending_models": max(model_count - covered, 0),
                "total_models": model_count,
                "latest_change": _latest_change(session, task.slug),
            }
        )
    return rows


@router.get("/changes")
def list_task_changes(session: Session = Depends(get_session)):
    events = session.query(TaskChangeEvent).order_by(TaskChangeEvent.id.desc()).limit(200).all()
    return [
        {
            "id": event.id,
            "task_slug": event.task_slug,
            "change_type": event.change_type,
            "old_hash": event.old_hash,
            "new_hash": event.new_hash,
            "requires_rerun": event.requires_rerun,
            "created_at": event.created_at,
        }
        for event in events
    ]


@router.post("/sync")
def sync_tasks(session: Session = Depends(get_session)):
    stats = sync_tasks_from_dir(session, Path(get_settings().tasks_dir))
    return stats
