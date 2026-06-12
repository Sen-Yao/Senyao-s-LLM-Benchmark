from collections import defaultdict
import logging
import os
import time
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import LLMModel, Provider, Task, TaskResult

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])
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


def _current_version_by_task(tasks: list[Task]) -> dict[int, tuple[str, str]]:
    return {task.id: (task.semantic_hash or task.content_hash, task.evaluator_version or "v1") for task in tasks}


def _is_fresh_result(result: TaskResult, current: tuple[str, str] | None) -> bool:
    if current is None:
        return False
    current_semantic_hash, current_evaluator_version = current
    result_semantic_hash = result.semantic_hash or result.task_hash
    result_evaluator_version = result.evaluator_version or "v1"
    return result_semantic_hash == current_semantic_hash and result_evaluator_version == current_evaluator_version


def _latest_display_results_by_task(session: Session, model_id: int, tasks: list[Task]) -> dict[int, tuple[TaskResult, bool]]:
    """Return one leaderboard result per live task with freshness metadata.

    Fresh results match the task semantic hash and evaluator version. If a task only has
    historical successful results, the newest stale result is still shown so the leaderboard
    does not look empty while run detail pages contain usable scores.
    """
    current_by_task = _current_version_by_task(tasks)
    if not current_by_task:
        return {}
    results = (
        session.query(TaskResult)
        .filter(
            TaskResult.model_id == model_id,
            TaskResult.status == "success",
            TaskResult.score.isnot(None),
            TaskResult.task_id.in_(current_by_task.keys()),
        )
        .order_by(TaskResult.id.desc())
        .all()
    )
    chosen: dict[int, tuple[TaskResult, bool]] = {}
    stale_fallback: dict[int, TaskResult] = {}
    for result in results:
        if result.task_id in chosen:
            continue
        is_fresh = _is_fresh_result(result, current_by_task.get(result.task_id))
        if is_fresh:
            chosen[result.task_id] = (result, True)
        elif result.task_id not in stale_fallback:
            stale_fallback[result.task_id] = result
    for task_id, result in stale_fallback.items():
        chosen.setdefault(task_id, (result, False))
    return chosen


@router.get("")
def leaderboard(session: Session = Depends(get_session)):
    probe = Probe("leaderboard")
    tasks = session.query(Task).filter(Task.active.is_not(False), Task.source_path != "").order_by(Task.category, Task.slug).all()
    probe.mark("query_tasks", tasks=len(tasks))
    dims = sorted({t.dimension for t in tasks})
    task_by_id = {t.id: t for t in tasks}
    rows = []
    models = session.query(LLMModel).filter(LLMModel.enabled.is_(True)).all()
    probe.mark("query_models", models=len(models))
    for model in models:
        provider = session.get(Provider, model.provider_id)
        latest = _latest_display_results_by_task(session, model.id, tasks)
        by_dim = defaultdict(list)
        current = 0
        stale = 0
        for task_id, (result, is_fresh) in latest.items():
            task = task_by_id.get(task_id)
            if task:
                by_dim[task.dimension].append(result.score)
                if is_fresh:
                    current += 1
                else:
                    stale += 1
        dim_scores = {d: round(sum(v) / len(v), 2) if v else None for d, v in by_dim.items()}
        all_scores = [s for vals in by_dim.values() for s in vals]
        total = len(tasks)
        covered = current + stale
        if current == total and tasks:
            coverage_status = "complete"
        elif covered:
            coverage_status = "stale" if stale else "partial"
        else:
            coverage_status = "partial"
        rows.append({
            "model_id": model.id,
            "model": model.display_name,
            "provider": provider.name if provider else "",
            "overall": round(sum(all_scores) / len(all_scores), 2) if all_scores else None,
            "dimensions": dim_scores,
            "coverage": {"current": current, "stale": stale, "total": total, "status": coverage_status},
        })
    probe.mark("serialize_rows", rows=len(rows))
    return {"dimensions": dims, "rows": rows}
