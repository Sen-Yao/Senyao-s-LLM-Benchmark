import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_SECRET_KEY", "dev-local-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/benchmark.db")
os.environ.setdefault("TASKS_DIR", "tasks")

from backend.app.db import Base, SessionLocal, engine
from backend.app.main import startup
from backend.app.models import BenchmarkRun, JudgeProfile, LLMModel, Provider, Task, TaskResult
from backend.app.security.crypto import encrypt_secret


REMOTE_BASE = "https://benchmark.senyao.org"


def parse_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def fetch_json(path: str):
    response = httpx.get(
        REMOTE_BASE + path,
        headers={
            "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
            "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        },
        timeout=30,
        follow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


def backup_db() -> Path | None:
    db_path = ROOT / "data" / "benchmark.db"
    if not db_path.exists():
        return None
    backup = db_path.with_name(f"benchmark.db.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db_path, backup)
    return backup


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    startup()


def main() -> None:
    backup = backup_db()
    reset_db()

    providers = fetch_json("/api/providers")
    models = fetch_json("/api/models")
    judges = fetch_json("/api/judges")
    runs = fetch_json("/api/runs")

    provider_map: dict[int, int] = {}
    model_map: dict[int, int] = {}
    judge_map: dict[int, int] = {}

    with SessionLocal() as session:
        for row in providers:
            provider = Provider(
                name=row["name"],
                api_base=row["api_base"],
                encrypted_api_key=encrypt_secret("migrated-placeholder"),
                api_key_fingerprint=row.get("api_key_fingerprint") or "",
                enabled=row.get("enabled", True),
                notes=(row.get("notes") or "") + "\n[migrated] API key must be re-entered locally.",
            )
            session.add(provider)
            session.flush()
            provider_map[int(row["id"])] = provider.id

        for row in models:
            provider_id = provider_map.get(int(row["provider_id"]))
            if not provider_id:
                continue
            model = LLMModel(
                provider_id=provider_id,
                display_name=row["display_name"],
                model_id=row["model_id"],
                enabled=row.get("enabled", True),
                context_window=row.get("context_window"),
                input_price=row.get("input_price"),
                output_price=row.get("output_price"),
                notes=row.get("notes") or "",
                tool_protocol=row.get("tool_protocol") or "openai_function",
            )
            session.add(model)
            session.flush()
            model_map[int(row["id"])] = model.id

        for row in judges:
            provider_id = provider_map.get(int(row["provider_id"]))
            if not provider_id:
                continue
            judge = JudgeProfile(
                provider_id=provider_id,
                name=row["name"],
                model_id=row["model_id"],
                temperature=row.get("temperature", 0.0),
                enabled=row.get("enabled", True),
            )
            session.add(judge)
            session.flush()
            judge_map[int(row["id"])] = judge.id

        task_by_slug = {task.slug: task for task in session.query(Task).all()}
        imported_runs = 0
        imported_results = 0
        skipped_results = 0

        for row in reversed(runs):
            model_id = model_map.get(int(row["model_id"]))
            if not model_id:
                continue
            run_id = row["run_id"]
            run = BenchmarkRun(
                run_id=run_id,
                model_id=model_id,
                suite_slug=row.get("suite_slug") or "all",
                status=row.get("status") or "completed",
                total_score=row.get("total_score"),
                total_cost=row.get("total_cost"),
                total_latency=row.get("total_latency"),
                started_at=parse_dt(row.get("started_at")),
                finished_at=parse_dt(row.get("finished_at")),
            )
            session.add(run)
            session.flush()
            imported_runs += 1

            detail = fetch_json(f"/api/runs/{run_id}")
            for result in detail.get("results", []):
                task = task_by_slug.get(result.get("task_slug"))
                if not task:
                    skipped_results += 1
                    continue
                session.add(
                    TaskResult(
                        run_id=run_id,
                        model_id=model_id,
                        task_id=task.id,
                        task_hash=task.content_hash,
                        semantic_hash=task.semantic_hash or task.content_hash,
                        evaluator_version=task.evaluator_version or "v1",
                        prompt=result.get("prompt") or task.prompt,
                        response=result.get("response") or "",
                        score=result.get("score"),
                        judge_reason=result.get("judge_reason") or "",
                        raw_judge_response=result.get("raw_judge_response") or "",
                        latency=result.get("latency"),
                        started_at=parse_dt(result.get("started_at")),
                        finished_at=parse_dt(result.get("finished_at")),
                        input_tokens=result.get("input_tokens"),
                        output_tokens=result.get("output_tokens"),
                        cost=result.get("cost"),
                        attempt_count=result.get("attempt_count") or 0,
                        max_retries=result.get("max_retries") or 3,
                        status=result.get("status") or "success",
                        error=result.get("error") or "",
                        trace_json=json.dumps(result.get("trace") or {}, ensure_ascii=False),
                        tool_metrics_json=json.dumps(result.get("tool_metrics") or {}, ensure_ascii=False),
                    )
                )
                imported_results += 1

        session.commit()

    print(
        json.dumps(
            {
                "backup": str(backup) if backup else None,
                "providers": len(provider_map),
                "models": len(model_map),
                "judges": len(judge_map),
                "runs": imported_runs,
                "results": imported_results,
                "skipped_results": skipped_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
