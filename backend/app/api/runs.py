from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db import SessionLocal, get_session
from backend.app.models import BenchmarkRun, Task, TaskResult
from backend.app.schemas import RunRequest
from backend.app.services.benchmark import run_model_tasks

router = APIRouter(prefix="/runs", tags=["runs"])


async def _run_many(payload: RunRequest):
    session = SessionLocal()
    try:
        for mid in payload.model_ids:
            await run_model_tasks(session, mid, payload.task_slugs, payload.judge_profile_id, payload.suite)
    finally:
        session.close()


@router.post("")
def create_run(payload: RunRequest, background: BackgroundTasks):
    background.add_task(_run_many, payload)
    return {"status": "queued", "model_ids": payload.model_ids, "task_slugs": payload.task_slugs}


@router.post("/incremental/task/{task_slug}")
def rerun_task_for_models(task_slug: str, payload: RunRequest, background: BackgroundTasks):
    scoped = RunRequest(
        model_ids=payload.model_ids,
        suite=payload.suite,
        task_slugs=[task_slug],
        judge_profile_id=payload.judge_profile_id,
    )
    background.add_task(_run_many, scoped)
    return {"status": "queued", "task_slug": task_slug, "model_ids": payload.model_ids}


def _serialize_result(result: TaskResult, task: Task | None = None) -> dict:
    return {
        "task_id": result.task_id,
        "task_slug": task.slug if task else "",
        "task_title": task.title if task else "",
        "dimension": task.dimension if task else "",
        "score": result.score,
        "status": result.status,
        "response": result.response,
        "judge_reason": result.judge_reason,
        "error": result.error,
        "latency": result.latency,
    }


def _run_results(session: Session, run_id: str, status: str | None = None) -> list[TaskResult]:
    query = session.query(TaskResult).filter(TaskResult.run_id == run_id)
    if status:
        query = query.filter(TaskResult.status == status)
    return query.order_by(TaskResult.id.asc()).all()


def _progress_for(session: Session, run: BenchmarkRun) -> dict:
    results = _run_results(session, run.run_id)
    counts = {"success": 0, "failed": 0, "running": 0, "pending": 0}
    current_task = ""
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        if not current_task and result.status == "running":
            task = session.get(Task, result.task_id)
            current_task = task.slug if task else ""
    total = len(results)
    terminal = counts.get("success", 0) + counts.get("failed", 0)
    return {
        "total": total,
        "completed": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "running": counts.get("running", 0),
        "pending": counts.get("pending", 0),
        "percent": round((terminal / total) * 100) if total else 0,
        "current_task": current_task,
    }


def _failure_summary_for(session: Session, run: BenchmarkRun) -> dict:
    failed = _run_results(session, run.run_id, status="failed")
    latest_error = ""
    if failed:
        latest_error = failed[-1].error
    return {"count": len(failed), "latest_error": latest_error}


def _serialize_run(session: Session, run: BenchmarkRun) -> dict:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "model_id": run.model_id,
        "status": run.status,
        "suite_slug": run.suite_slug,
        "total_score": run.total_score,
        "total_latency": run.total_latency,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "progress": _progress_for(session, run),
        "failure_summary": _failure_summary_for(session, run),
    }


@router.get("")
def list_runs(session: Session = Depends(get_session)):
    runs = session.query(BenchmarkRun).order_by(BenchmarkRun.id.desc()).limit(100).all()
    return [_serialize_run(session, run) for run in runs]


@router.get("/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_session)):
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    results = _run_results(session, run_id)
    return {
        **_serialize_run(session, run),
        "results": [_serialize_result(result, session.get(Task, result.task_id)) for result in results],
    }


@router.get("/{run_id}/results")
def run_results(
    run_id: str,
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    results = _run_results(session, run_id, status=status)
    return [_serialize_result(result, session.get(Task, result.task_id)) for result in results]
