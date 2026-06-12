from pathlib import Path
import logging
import os
import time
from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .config import get_settings
from .db import init_db, engine
from .api.providers import router as providers_router
from .api.models import router as models_router
from .api.judges import router as judges_router
from .api.tasks import router as tasks_router
from .api.leaderboard import router as leaderboard_router
from .api.runs import router as runs_router
from .api.settings import router as settings_router
from .api.smoke import router as smoke_router
from .task_registry.loader import sync_tasks_from_dir

settings=get_settings()
app=FastAPI(title=settings.app_name)
app.add_middleware(CORSMiddleware, allow_origins=["*"] if settings.cors_origins=="*" else settings.cors_origins.split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
probe_logger = logging.getLogger("benchmark.probe")


def probe_enabled() -> bool:
    return os.getenv("BENCHMARK_PROBE", "").lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def probe_api_latency(request: Request, call_next):
    if not probe_enabled() or not request.url.path.startswith("/api"):
        return await call_next(request)
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Probe-Duration-Ms"] = f"{elapsed_ms:.1f}"
    probe_logger.warning(
        "[probe] api method=%s path=%s status=%s total_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

@app.on_event("startup")
def startup():
    init_db()
    if settings.database_url.startswith("sqlite"):
        with engine.begin() as conn:
            model_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(llm_models)").fetchall()}
            if "tool_protocol" not in model_cols:
                conn.exec_driver_sql("ALTER TABLE llm_models ADD COLUMN tool_protocol VARCHAR(40) NOT NULL DEFAULT 'openai_function'")
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tasks)").fetchall()}
            if "short_description" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN short_description TEXT DEFAULT ''")
            if "task_type" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN task_type VARCHAR(80) NOT NULL DEFAULT 'llm_judged'")
            if "config_json" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'")
            if "semantic_hash" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN semantic_hash VARCHAR(64) NOT NULL DEFAULT ''")
                conn.exec_driver_sql("UPDATE tasks SET semantic_hash = content_hash WHERE semantic_hash = ''")
            if "raw_config_hash" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN raw_config_hash VARCHAR(64) NOT NULL DEFAULT ''")
            if "evaluator_version" not in cols:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN evaluator_version VARCHAR(40) NOT NULL DEFAULT 'v1'")
            result_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(task_results)").fetchall()}
            if "started_at" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN started_at DATETIME")
            if "finished_at" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN finished_at DATETIME")
            if "attempt_count" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
            if "max_retries" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3")
            if "trace_json" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN trace_json TEXT NOT NULL DEFAULT '{}'")
            if "tool_metrics_json" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN tool_metrics_json TEXT NOT NULL DEFAULT '{}'")
            if "semantic_hash" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN semantic_hash VARCHAR(64) NOT NULL DEFAULT ''")
                conn.exec_driver_sql("UPDATE task_results SET semantic_hash = task_hash WHERE semantic_hash = ''")
            if "evaluator_version" not in result_cols:
                conn.exec_driver_sql("ALTER TABLE task_results ADD COLUMN evaluator_version VARCHAR(40) NOT NULL DEFAULT 'v1'")
            indexes = conn.exec_driver_sql("PRAGMA index_list(task_results)").fetchall()
            for idx in indexes:
                idx_name = idx[1]
                if idx_name == "sqlite_autoindex_task_results_1":
                    create_sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_results'").scalar() or ""
                    if "uq_model_task_hash" in create_sql or "UNIQUE (model_id, task_id, task_hash)" in create_sql:
                        conn.exec_driver_sql("ALTER TABLE task_results RENAME TO task_results_old")
                        conn.exec_driver_sql("""
                            CREATE TABLE task_results (
                                id INTEGER NOT NULL PRIMARY KEY,
                                run_id VARCHAR(80) NOT NULL,
                                model_id INTEGER NOT NULL,
                                task_id INTEGER NOT NULL,
                                task_hash VARCHAR(64) NOT NULL,
                                semantic_hash VARCHAR(64) NOT NULL DEFAULT '',
                                evaluator_version VARCHAR(40) NOT NULL DEFAULT 'v1',
                                prompt TEXT NOT NULL,
                                response TEXT NOT NULL DEFAULT '',
                                score FLOAT,
                                judge_reason TEXT NOT NULL DEFAULT '',
                                raw_judge_response TEXT NOT NULL DEFAULT '',
                                latency FLOAT,
                                started_at DATETIME,
                                finished_at DATETIME,
                                input_tokens INTEGER,
                                output_tokens INTEGER,
                                cost FLOAT,
                                attempt_count INTEGER NOT NULL DEFAULT 0,
                                max_retries INTEGER NOT NULL DEFAULT 3,
                                status VARCHAR(40) NOT NULL,
                                error TEXT NOT NULL DEFAULT '',
                                trace_json TEXT NOT NULL DEFAULT '{}',
                                tool_metrics_json TEXT NOT NULL DEFAULT '{}',
                                created_at DATETIME NOT NULL,
                                updated_at DATETIME NOT NULL,
                                FOREIGN KEY(model_id) REFERENCES llm_models (id),
                                FOREIGN KEY(task_id) REFERENCES tasks (id)
                            )
                        """)
                        conn.exec_driver_sql("""
                            INSERT INTO task_results (id, run_id, model_id, task_id, task_hash, semantic_hash, evaluator_version, prompt, response, score, judge_reason, raw_judge_response, latency, started_at, finished_at, input_tokens, output_tokens, cost, attempt_count, max_retries, status, error, trace_json, tool_metrics_json, created_at, updated_at)
                            SELECT id, run_id, model_id, task_id, task_hash, task_hash, 'v1', prompt, response, score, judge_reason, raw_judge_response, latency, NULL, NULL, input_tokens, output_tokens, cost, 0, 3, status, error, '{}', '{}', created_at, updated_at FROM task_results_old
                        """)
                        conn.exec_driver_sql("DROP TABLE task_results_old")
                        conn.exec_driver_sql("CREATE INDEX ix_task_results_run_id ON task_results (run_id)")
                        conn.exec_driver_sql("CREATE INDEX ix_task_results_model_id ON task_results (model_id)")
                        conn.exec_driver_sql("CREATE INDEX ix_task_results_task_id ON task_results (task_id)")
                        conn.exec_driver_sql("CREATE INDEX ix_task_results_task_hash ON task_results (task_hash)")
                    break
    from .db import SessionLocal
    from .models import BenchmarkRun, TaskResult
    from datetime import datetime, timedelta

    with SessionLocal() as session:
        now = datetime.utcnow()
        stale_cutoff = now - timedelta(seconds=120)
        candidate_runs = session.query(BenchmarkRun).filter(BenchmarkRun.status.in_(["pending", "running"])).all()
        stale_runs = []
        for run in candidate_runs:
            newest_row = (
                session.query(TaskResult)
                .filter(TaskResult.run_id == run.run_id)
                .order_by(TaskResult.updated_at.desc())
                .first()
            )
            last_touch = newest_row.updated_at if newest_row and newest_row.updated_at else (run.started_at or run.updated_at or run.created_at)
            if last_touch and last_touch > stale_cutoff:
                continue
            stale_runs.append(run)
            run.status = "failed"
            run.finished_at = run.finished_at or now
            for row in session.query(TaskResult).filter(TaskResult.run_id == run.run_id, TaskResult.status.in_(["pending", "running"])).all():
                row.status = "failed"
                row.error = row.error or "服务重启导致运行中断，请重新发起运行"
                row.finished_at = row.finished_at or now
        terminal_runs = session.query(BenchmarkRun).filter(BenchmarkRun.status.in_(["failed", "cancelled"])).all()
        for run in terminal_runs:
            target_status = "cancelled" if run.status == "cancelled" else "failed"
            default_error = "运行已终止" if target_status == "cancelled" else "运行异常中断，请重新发起运行"
            for row in session.query(TaskResult).filter(TaskResult.run_id == run.run_id, TaskResult.status.in_(["pending", "running"])).all():
                row.status = target_status
                row.error = row.error or default_error
                row.finished_at = row.finished_at or (run.finished_at or now)
        session.commit()
        sync_tasks_from_dir(session, Path(settings.tasks_dir))


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name}

for router in [providers_router, models_router, judges_router, tasks_router, leaderboard_router, runs_router, settings_router, smoke_router]:
    app.include_router(router, prefix="/api")

frontend_dist=Path("frontend/dist")

def _frontend_root_file(filename: str):
    def endpoint():
        return FileResponse(frontend_dist / filename)
    return endpoint

for static_name in ["favicon.svg", "benchmark-logo.svg", "site.webmanifest"]:
    app.get(f"/{static_name}")(_frontend_root_file(static_name))

if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist/"assets"), name="assets")
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        index=frontend_dist/"index.html"
        return FileResponse(index)
