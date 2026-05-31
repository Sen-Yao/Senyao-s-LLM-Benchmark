from pathlib import Path
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.models import Task, TaskResult, LLMModel
from backend.app.task_registry.loader import sync_tasks_from_dir

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.get("")
def list_tasks(session: Session = Depends(get_session)):
    tasks = session.query(Task).order_by(Task.category, Task.slug).all()
    model_count = session.query(LLMModel).count()
    return [{"id": t.id, "slug": t.slug, "title": t.title, "category": t.category, "dimension": t.dimension, "evaluator_type": t.evaluator_type, "content_hash": t.content_hash, "active": t.active, "source_path": t.source_path, "covered_models": session.query(TaskResult.model_id).filter(TaskResult.task_id==t.id, TaskResult.task_hash==t.content_hash, TaskResult.status=="success").distinct().count(), "total_models": model_count} for t in tasks]

@router.post("/sync")
def sync_tasks(session: Session = Depends(get_session)):
    stats = sync_tasks_from_dir(session, Path(get_settings().tasks_dir))
    return stats
