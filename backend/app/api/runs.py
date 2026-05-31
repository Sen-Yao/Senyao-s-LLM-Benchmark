from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from backend.app.db import SessionLocal, get_session
from backend.app.models import BenchmarkRun, TaskResult
from backend.app.schemas import RunRequest
from backend.app.services.benchmark import run_model_tasks

router = APIRouter(prefix="/runs", tags=["runs"])

async def _run_many(payload: RunRequest):
    session=SessionLocal()
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

@router.get("")
def list_runs(session: Session = Depends(get_session)):
    return [{"id": r.id, "run_id": r.run_id, "model_id": r.model_id, "status": r.status, "total_score": r.total_score, "started_at": r.started_at, "finished_at": r.finished_at} for r in session.query(BenchmarkRun).order_by(BenchmarkRun.id.desc()).limit(100).all()]

@router.get("/{run_id}/results")
def run_results(run_id: str, session: Session = Depends(get_session)):
    return [{"task_id": r.task_id, "score": r.score, "status": r.status, "response": r.response, "judge_reason": r.judge_reason, "error": r.error} for r in session.query(TaskResult).filter(TaskResult.run_id==run_id).all()]
