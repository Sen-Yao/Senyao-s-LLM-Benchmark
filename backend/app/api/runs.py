import asyncio
import json
import logging
import os
import time
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db import SessionLocal, get_session
from backend.app.models import BenchmarkRun, LLMModel, Provider, Task, TaskResult
from backend.app.schemas import RunRequest
from backend.app.services.benchmark import run_model_tasks
from backend.app.api.settings import resolve_judge_profile_id

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL_RESULT_STATUSES = {"success", "failed", "cancelled"}
DISPLAY_RESULT_STATUSES = {"success", "failed"}
probe_logger = logging.getLogger("benchmark.probe")


def _probe_enabled() -> bool:
    return os.getenv("BENCHMARK_PROBE", "").lower() in {"1", "true", "yes", "on"}


class Probe:
    def __init__(self, name: str, **fields):
        self.name = name
        self.fields = fields
        self.enabled = _probe_enabled()
        self.start = time.perf_counter()
        self.last = self.start

    def mark(self, step: str, **fields):
        if not self.enabled:
            return
        now = time.perf_counter()
        probe_logger.warning(
            "[probe] %s step=%s delta_ms=%.1f total_ms=%.1f %s",
            self.name,
            step,
            (now - self.last) * 1000,
            (now - self.start) * 1000,
            {**self.fields, **fields},
        )
        self.last = now


def _tasks_for(session: Session, task_slugs: list[str] | None = None) -> list[Task]:
    query = session.query(Task).filter(Task.active.is_not(False))
    if task_slugs:
        query = query.filter(Task.slug.in_(task_slugs))
    return query.order_by(Task.category, Task.slug).all()


def _with_default_judge(session: Session, payload: RunRequest) -> RunRequest:
    judge_profile_id = resolve_judge_profile_id(session, payload.judge_profile_id)
    if judge_profile_id == payload.judge_profile_id:
        return payload
    return payload.model_copy(update={"judge_profile_id": judge_profile_id})


def _materialize_run(session: Session, model_id: int, payload: RunRequest) -> BenchmarkRun:
    tasks = _tasks_for(session, payload.task_slugs)
    run = BenchmarkRun(
        run_id=__import__("uuid").uuid4().hex[:16],
        model_id=model_id,
        suite_slug=payload.suite,
        status="pending",
    )
    session.add(run)
    session.flush()
    for task in tasks:
        session.add(
            TaskResult(
                run_id=run.run_id,
                model_id=model_id,
                task_id=task.id,
                task_hash=task.content_hash,
                semantic_hash=task.semantic_hash or task.content_hash,
                evaluator_version=task.evaluator_version or "v1",
                prompt=task.prompt,
                status="pending",
            )
        )
    session.commit()
    session.refresh(run)
    return run


def _mark_run_failed(session: Session, run_id: str | None, error: str):
    if not run_id:
        return
    now = datetime.utcnow()
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()
    if run and run.status not in {"completed", "failed", "cancelled"}:
        run.status = "failed"
        run.finished_at = run.finished_at or now
    for row in session.query(TaskResult).filter(TaskResult.run_id == run_id, TaskResult.status.in_(["pending", "running"])).all():
        row.status = "failed"
        row.error = row.error or error
        row.finished_at = row.finished_at or now
    session.commit()


async def _run_many(payload: RunRequest, run_ids: list[str] | None = None):
    model_semaphore = asyncio.Semaphore(max(1, payload.max_concurrency))

    async def _one(index: int, mid: int):
        run_id = run_ids[index] if run_ids and index < len(run_ids) else None
        async with model_semaphore:
            session = SessionLocal()
            try:
                await run_model_tasks(
                    session,
                    mid,
                    payload.task_slugs,
                    payload.judge_profile_id,
                    payload.suite,
                    run_id=run_id,
                    max_concurrency=1,
                    max_retries=payload.max_retries,
                    force_rerun=payload.force_rerun,
                )
            except Exception as exc:
                _mark_run_failed(session, run_id, f"后台任务异常：{exc}")
            finally:
                session.close()

    await asyncio.gather(*[_one(index, mid) for index, mid in enumerate(payload.model_ids)])


@router.post("")
def create_run(payload: RunRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
    payload = _with_default_judge(session, payload)
    runs = [_materialize_run(session, mid, payload) for mid in payload.model_ids]
    run_ids = [run.run_id for run in runs]
    background.add_task(_run_many, payload, run_ids)
    return {
        "status": "queued",
        "model_ids": payload.model_ids,
        "task_slugs": payload.task_slugs,
        "judge_profile_id": payload.judge_profile_id,
        "max_concurrency": payload.max_concurrency,
        "max_retries": payload.max_retries,
        "runs": [_serialize_run(session, run) for run in runs],
    }


@router.post("/incremental/task/{task_slug}")
def rerun_task_for_models(task_slug: str, payload: RunRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
    payload = _with_default_judge(session, payload)
    scoped = RunRequest(
        model_ids=payload.model_ids,
        suite=payload.suite,
        task_slugs=[task_slug],
        judge_profile_id=payload.judge_profile_id,
        max_concurrency=payload.max_concurrency,
        max_retries=payload.max_retries,
        force_rerun=payload.force_rerun,
    )
    runs = [_materialize_run(session, mid, scoped) for mid in payload.model_ids]
    run_ids = [run.run_id for run in runs]
    background.add_task(_run_many, scoped, run_ids)
    return {"status": "queued", "task_slug": task_slug, "model_ids": payload.model_ids, "judge_profile_id": scoped.judge_profile_id, "max_concurrency": scoped.max_concurrency, "max_retries": scoped.max_retries, "force_rerun": scoped.force_rerun, "runs": [_serialize_run(session, run) for run in runs]}


def _safe_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_result(result: TaskResult, task: Task | None = None) -> dict:
    return {
        "task_id": result.task_id,
        "task_slug": task.slug if task else "",
        "task_title": task.title if task else "",
        "dimension": task.dimension if task else "",
        "task_type": task.task_type if task else "",
        "score": result.score,
        "status": result.status,
        "response": result.response,
        "judge_reason": result.judge_reason,
        "error": result.error,
        "latency": result.latency,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "created_at": result.created_at,
        "updated_at": result.updated_at,
        "attempt_count": result.attempt_count,
        "max_retries": result.max_retries,
        "trace": _safe_json(result.trace_json),
        "tool_metrics": _safe_json(result.tool_metrics_json),
    }


def _is_fresh_result_for_task(result: TaskResult, task: Task) -> bool:
    result_semantic_hash = result.semantic_hash or result.task_hash
    result_evaluator_version = result.evaluator_version or "v1"
    return result_semantic_hash == (task.semantic_hash or task.content_hash) and result_evaluator_version == (task.evaluator_version or "v1")


def _result_sort_key(result: TaskResult) -> tuple:
    return (result.updated_at or result.finished_at or result.started_at or result.created_at or datetime.min, result.id or 0)


def _empty_result_for_task(task: Task) -> dict:
    return {
        "task_id": task.id,
        "task_slug": task.slug,
        "task_title": task.title,
        "dimension": task.dimension,
        "task_type": task.task_type,
        "score": None,
        "status": "pending",
        "response": "",
        "judge_reason": "",
        "error": "",
        "latency": None,
        "started_at": None,
        "finished_at": None,
        "created_at": None,
        "updated_at": None,
        "attempt_count": 0,
        "max_retries": 0,
        "trace": {},
        "tool_metrics": {},
    }


def _model_current_results(session: Session, model_id: int) -> dict:
    model = session.get(LLMModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="model not found")
    provider = session.get(Provider, model.provider_id)
    tasks = session.query(Task).filter(Task.active.is_not(False), Task.source_path != "").order_by(Task.category, Task.slug).all()
    task_by_id = {task.id: task for task in tasks}
    rows = (
        session.query(TaskResult)
        .filter(TaskResult.model_id == model_id, TaskResult.task_id.in_(task_by_id.keys()))
        .order_by(TaskResult.id.desc())
        .all()
        if task_by_id
        else []
    )
    fresh_by_task: dict[int, TaskResult] = {}
    stale_by_task: dict[int, TaskResult] = {}
    active_by_task: dict[int, TaskResult] = {}
    for row in rows:
        task = task_by_id.get(row.task_id)
        if not task:
            continue
        if row.status in {"running", "pending"} and _is_fresh_result_for_task(row, task):
            if row.task_id not in active_by_task or _result_sort_key(row) > _result_sort_key(active_by_task[row.task_id]):
                active_by_task[row.task_id] = row
            continue
        if row.status not in DISPLAY_RESULT_STATUSES:
            continue
        bucket = fresh_by_task if _is_fresh_result_for_task(row, task) else stale_by_task
        if row.task_id not in bucket or _result_sort_key(row) > _result_sort_key(bucket[row.task_id]):
            bucket[row.task_id] = row
    results = []
    current = stale = pending = failed = running = 0
    total_latency = 0.0
    score_sum = 0.0
    score_count = 0
    for task in tasks:
        row = fresh_by_task.get(task.id)
        freshness = "fresh" if row else "pending"
        if row is None and task.id in stale_by_task:
            row = stale_by_task[task.id]
            freshness = "stale"
        active = active_by_task.get(task.id)
        serialized = _serialize_result(row, task) if row else _empty_result_for_task(task)
        serialized.update({
            "freshness": freshness,
            "active_status": active.status if active else "",
            "active_run_id": active.run_id if active else "",
            "run_id": row.run_id if row else "",
            "semantic_hash": task.semantic_hash or task.content_hash,
            "evaluator_version": task.evaluator_version or "v1",
        })
        if freshness == "fresh":
            current += 1
        elif freshness == "stale":
            stale += 1
        else:
            pending += 1
        if active and active.status == "running":
            running += 1
        if serialized["status"] == "failed":
            failed += 1
        if serialized["status"] == "success" and serialized["score"] is not None:
            score_sum += float(serialized["score"])
            score_count += 1
        total_latency += float(serialized["latency"] or 0)
        results.append(serialized)
    total = len(tasks)
    return {
        "model_id": model.id,
        "model_display_name": model.display_name,
        "model_api_id": model.model_id,
        "provider_name": provider.name if provider else "",
        "overall": round(score_sum / score_count, 2) if score_count else None,
        "total_latency": total_latency,
        "coverage": {
            "current": current,
            "stale": stale,
            "pending": pending,
            "failed": failed,
            "running": running,
            "total": total,
            "status": "complete" if current == total and total else ("stale" if stale else "partial"),
        },
        "results": results,
    }



def _run_results(session: Session, run_id: str, status: str | None = None) -> list[TaskResult]:
    query = session.query(TaskResult).filter(TaskResult.run_id == run_id)
    if status:
        query = query.filter(TaskResult.status == status)
    return query.order_by(TaskResult.id.asc()).all()


def _progress_for(session: Session, run: BenchmarkRun) -> dict:
    results = _run_results(session, run.run_id)
    known_statuses = {"success", "failed", "running", "pending", "cancelled"}
    counts = {status: 0 for status in known_statuses}
    unknown = 0
    current_task = ""
    for result in results:
        if result.status in known_statuses:
            counts[result.status] += 1
        else:
            unknown += 1
        if not current_task and result.status == "running":
            task = session.get(Task, result.task_id)
            current_task = task.slug if task else ""
    total = len(results)
    accounted = sum(counts.values()) + unknown
    missing = max(total - accounted, 0)
    terminal = counts.get("success", 0) + counts.get("failed", 0) + counts.get("cancelled", 0)
    return {
        "total": total,
        "completed": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "running": counts.get("running", 0),
        "pending": counts.get("pending", 0),
        "cancelled": counts.get("cancelled", 0),
        "unknown": unknown,
        "accounted": accounted,
        "missing": missing,
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
    model = session.get(LLMModel, run.model_id)
    provider = session.get(Provider, model.provider_id) if model else None
    return {
        "id": run.id,
        "run_id": run.run_id,
        "model_id": run.model_id,
        "model_display_name": model.display_name if model else "",
        "model_api_id": model.model_id if model else "",
        "provider_name": provider.name if provider else "",
        "status": run.status,
        "suite_slug": run.suite_slug,
        "total_score": run.total_score,
        "total_latency": run.total_latency,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "progress": _progress_for(session, run),
        "failure_summary": _failure_summary_for(session, run),
    }


@router.get("")
def list_runs(session: Session = Depends(get_session)):
    probe = Probe("runs.list")
    runs = session.query(BenchmarkRun).order_by(BenchmarkRun.id.desc()).limit(100).all()
    probe.mark("query_runs", runs=len(runs))
    payload = [_serialize_run(session, run) for run in runs]
    probe.mark("serialize_runs", runs=len(runs))
    return payload


@router.get("/model/{model_id}/current-results")
def model_current_results(model_id: int, session: Session = Depends(get_session)):
    return _model_current_results(session, model_id)


@router.get("/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_session)):
    probe = Probe("runs.detail", run_id=run_id)
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()
    probe.mark("query_run")
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    results = _run_results(session, run_id)
    probe.mark("query_results", results=len(results))
    serialized_run = _serialize_run(session, run)
    probe.mark("serialize_run")
    serialized_results = [_serialize_result(result, session.get(Task, result.task_id)) for result in results]
    probe.mark("serialize_results", results=len(results))
    return {
        **serialized_run,
        "results": serialized_results,
    }


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, session: Session = Depends(get_session)):
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    cancellable = _run_results(session, run_id)
    cancelled = 0
    for result in cancellable:
        if result.status in {"pending", "running"}:
            result.status = "cancelled"
            result.error = result.error or "用户终止运行"
            cancelled += 1
    if run.status not in {"completed", "failed", "cancelled"}:
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
    session.commit()
    return {"ok": True, "run_id": run_id, "cancelled": cancelled}


@router.get("/{run_id}/results")
def run_results(
    run_id: str,
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    results = _run_results(session, run_id, status=status)
    return [_serialize_result(result, session.get(Task, result.task_id)) for result in results]
