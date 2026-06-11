import json
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.models import LLMModel, Task, TaskChangeEvent, TaskResult
from backend.app.schemas import RunRequest, TaskRerunRequest
from backend.app.api.runs import _materialize_run, _run_many, _serialize_run
from backend.app.api.settings import resolve_judge_profile_id
from backend.app.services.agent_tools import build_tool_schemas
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


def _task_coverage(session: Session, task: Task) -> dict:
    current_semantic_hash = task.semantic_hash or task.content_hash
    current_evaluator_version = task.evaluator_version or "v1"
    enabled_models = session.query(LLMModel).filter(LLMModel.enabled.is_not(False)).order_by(LLMModel.display_name, LLMModel.id).all()
    all_model_ids = {model.id for model in enabled_models}
    fresh_model_ids = {
        model_id
        for (model_id,) in session.query(TaskResult.model_id)
        .filter(
            TaskResult.task_id == task.id,
            TaskResult.semantic_hash == current_semantic_hash,
            TaskResult.evaluator_version == current_evaluator_version,
            TaskResult.status == "success",
        )
        .distinct()
        .all()
    } & all_model_ids
    any_success_model_ids = {
        model_id
        for (model_id,) in session.query(TaskResult.model_id)
        .filter(
            TaskResult.task_id == task.id,
            TaskResult.status == "success",
        )
        .distinct()
        .all()
    } & all_model_ids
    stale_model_ids = any_success_model_ids - fresh_model_ids
    pending_model_ids = all_model_ids - fresh_model_ids

    def serialize_models(model_ids: set[int]) -> list[dict]:
        return [
            {"id": model.id, "display_name": model.display_name, "model_id": model.model_id, "provider_id": model.provider_id}
            for model in enabled_models
            if model.id in model_ids
        ]

    return {
        "semantic_hash": current_semantic_hash,
        "evaluator_version": current_evaluator_version,
        "covered_model_ids": sorted(fresh_model_ids),
        "stale_model_ids": sorted(stale_model_ids),
        "pending_model_ids": sorted(pending_model_ids),
        "covered_models": len(fresh_model_ids),
        "stale_models": len(stale_model_ids),
        "pending_models": len(pending_model_ids),
        "total_models": len(enabled_models),
        "models": {
            "covered": serialize_models(fresh_model_ids),
            "stale": serialize_models(stale_model_ids),
            "pending": serialize_models(pending_model_ids),
        },
    }


@router.get("")
def list_tasks(session: Session = Depends(get_session)):
    tasks = session.query(Task).order_by(Task.category, Task.slug).all()
    rows = []
    for task in tasks:
        coverage = _task_coverage(session, task)
        config = json.loads(task.config_json or "{}")
        agent_config = config.get("agent") or {}
        is_agent_task = task.task_type == "agent_tool" or task.evaluator_type in {"agent_trace_eval", "agent_state_machine_eval"}
        agent_api_context = (
            {
                "messages": [{"role": "user", "content": task.prompt}],
                "tools": build_tool_schemas(agent_config),
                "agent": agent_config,
                "fixtures": config.get("fixtures") or {},
                "expected_trace": config.get("expected_trace") or {},
                "state_machine": config.get("state_machine") or {},
            }
            if is_agent_task
            else None
        )
        rows.append(
            {
                "id": task.id,
                "slug": task.slug,
                "title": task.title,
                "category": task.category,
                "dimension": task.dimension,
                "task_type": task.task_type,
                "evaluator_type": task.evaluator_type,
                "evaluator_config": json.loads(task.evaluator_config_json or "{}"),
                "config": config,
                "agent_api_context": agent_api_context,
                "prompt": task.prompt,
                "description": task.description,
                "short_description": getattr(task, "short_description", "") or task.description,
                "content_hash": task.content_hash,
                "semantic_hash": coverage["semantic_hash"],
                "raw_config_hash": task.raw_config_hash,
                "evaluator_version": coverage["evaluator_version"],
                "active": task.active,
                "source_path": task.source_path,
                "covered_models": coverage["covered_models"],
                "stale_models": coverage["stale_models"],
                "pending_models": coverage["pending_models"],
                "total_models": coverage["total_models"],
                "latest_change": _latest_change(session, task.slug),
            }
        )
    return rows


@router.get("/changes")
def list_task_changes(session: Session = Depends(get_session)):
    live_task_slugs = {slug for (slug,) in session.query(Task.slug).all()}
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
        if event.task_slug in live_task_slugs
    ]


@router.get("/{task_slug}/coverage")
def task_coverage(task_slug: str, session: Session = Depends(get_session)):
    task = session.query(Task).filter(Task.slug == task_slug).first()
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_slug": task.slug, **_task_coverage(session, task)}


@router.post("/{task_slug}/rerun-missing")
def rerun_missing_models_for_task(task_slug: str, payload: TaskRerunRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
    task = session.query(Task).filter(Task.slug == task_slug, Task.active.is_not(False)).first()
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    coverage = _task_coverage(session, task)
    target_model_ids = payload.model_ids if payload.model_ids is not None else coverage["pending_model_ids"]
    target_model_ids = [model_id for model_id in target_model_ids if model_id in coverage["pending_model_ids"]]
    if not target_model_ids:
        return {"status": "noop", "task_slug": task_slug, "model_ids": [], "runs": []}
    judge_profile_id = resolve_judge_profile_id(session, payload.judge_profile_id)
    scoped = RunRequest(
        model_ids=target_model_ids,
        suite=payload.suite,
        task_slugs=[task_slug],
        judge_profile_id=judge_profile_id,
        max_concurrency=payload.max_concurrency,
        max_retries=payload.max_retries,
    )
    runs = [_materialize_run(session, model_id, scoped) for model_id in target_model_ids]
    run_ids = [run.run_id for run in runs]
    background.add_task(_run_many, scoped, run_ids)
    return {
        "status": "queued",
        "task_slug": task_slug,
        "model_ids": target_model_ids,
        "judge_profile_id": scoped.judge_profile_id,
        "max_concurrency": payload.max_concurrency,
        "max_retries": payload.max_retries,
        "runs": [_serialize_run(session, run) for run in runs],
    }


@router.post("/sync")
def sync_tasks(session: Session = Depends(get_session)):
    stats = sync_tasks_from_dir(session, Path(get_settings().tasks_dir))
    return stats
