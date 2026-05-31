from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import BenchmarkRun, LLMModel, Provider, Task, TaskResult
from backend.app.security.crypto import encrypt_secret, fingerprint_secret


def _seed_run_with_results():
    session = SessionLocal()
    try:
        provider = Provider(
            name="run-visibility-provider",
            api_base="http://127.0.0.1:9999/v1",
            encrypted_api_key=encrypt_secret("test-secret"),
            api_key_fingerprint=fingerprint_secret("test-secret"),
        )
        session.add(provider)
        session.flush()
        model = LLMModel(
            provider_id=provider.id,
            display_name="Run Visibility Model",
            model_id="run-visibility-model",
        )
        session.add(model)
        tasks = [
            Task(
                slug="run-visibility-ok",
                title="OK",
                category="core",
                dimension="accuracy",
                prompt="Say OK",
                evaluator_type="contains",
                evaluator_config_json='{"contains":"OK"}',
                content_hash="hash-ok",
            ),
            Task(
                slug="run-visibility-fail",
                title="Fail",
                category="core",
                dimension="accuracy",
                prompt="Say FAIL",
                evaluator_type="contains",
                evaluator_config_json='{"contains":"FAIL"}',
                content_hash="hash-fail",
            ),
            Task(
                slug="run-visibility-running",
                title="Running",
                category="core",
                dimension="reasoning",
                prompt="Think",
                evaluator_type="contains",
                evaluator_config_json='{"contains":"THINK"}',
                content_hash="hash-running",
            ),
        ]
        session.add_all(tasks)
        session.flush()
        run = BenchmarkRun(
            run_id="run-visibility-001",
            model_id=model.id,
            suite_slug="all",
            status="running",
            total_score=None,
            total_latency=1.2,
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                TaskResult(
                    run_id=run.run_id,
                    model_id=model.id,
                    task_id=tasks[0].id,
                    task_hash=tasks[0].content_hash,
                    prompt=tasks[0].prompt,
                    status="success",
                    score=1.0,
                    response="OK",
                ),
                TaskResult(
                    run_id=run.run_id,
                    model_id=model.id,
                    task_id=tasks[1].id,
                    task_hash=tasks[1].content_hash,
                    prompt=tasks[1].prompt,
                    status="failed",
                    error="provider timeout",
                ),
                TaskResult(
                    run_id=run.run_id,
                    model_id=model.id,
                    task_id=tasks[2].id,
                    task_hash=tasks[2].content_hash,
                    prompt=tasks[2].prompt,
                    status="running",
                ),
            ]
        )
        session.commit()
        return run.run_id
    finally:
        session.close()


def test_run_list_exposes_progress_and_failure_summary():
    run_id = _seed_run_with_results()

    with TestClient(app) as client:
        runs = client.get("/api/runs").json()
        visible = next(row for row in runs if row["run_id"] == run_id)

        assert visible["progress"] == {
            "total": 3,
            "completed": 1,
            "failed": 1,
            "running": 1,
            "pending": 0,
            "percent": 67,
            "current_task": "run-visibility-running",
        }
        assert visible["failure_summary"] == {"count": 1, "latest_error": "provider timeout"}


def test_run_detail_exposes_ordered_results_and_filters_failures():
    run_id = _seed_run_with_results()

    with TestClient(app) as client:
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["run_id"] == run_id
        assert detail["progress"]["current_task"] == "run-visibility-running"
        assert [result["task_slug"] for result in detail["results"]] == [
            "run-visibility-ok",
            "run-visibility-fail",
            "run-visibility-running",
        ]

        failed = client.get(f"/api/runs/{run_id}/results", params={"status": "failed"}).json()
        assert len(failed) == 1
        assert failed[0]["task_slug"] == "run-visibility-fail"
        assert failed[0]["error"] == "provider timeout"
