import os

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import BenchmarkRun, LLMModel, Provider, Task, TaskResult
from backend.app.security.crypto import decrypt_secret, encrypt_secret, fingerprint_secret


def test_live_smoke_rejects_unknown_task_slugs_before_running():
    payload = {
        "provider": {
            "name": "AxonHub Missing Task Smoke",
            "api_base": "http://127.0.0.1:8091/v1",
            "api_key": "live-test-secret",
        },
        "model": {"display_name": "GLM-5 Missing Task", "model_id": "glm-5"},
        "task_slugs": ["missing-smoke-task"],
    }

    with TestClient(app) as client:
        response = client.post("/api/smoke/live", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "unknown task slugs: missing-smoke-task"}


@pytest.mark.skipif(
    not os.getenv("SENYAO_BENCHMARK_ENABLE_LIVE_TESTS"),
    reason="live provider smoke tests are opt-in only",
)
def test_live_smoke_endpoint_creates_provider_model_tasks_and_redacts_secret():
    payload = {
        "provider": {
            "name": "AxonHub Live Smoke",
            "api_base": "http://127.0.0.1:8091/v1",
            "api_key": "live-test-secret",
        },
        "model": {"display_name": "GLM-5 Live Smoke", "model_id": "glm-5"},
        "task_slugs": ["transaction_classify", "radio_band_classify"],
    }

    with TestClient(app) as client:
        response = client.post("/api/smoke/live", json=payload)
        assert response.status_code == 200
        data = response.json()

    assert data["provider"]["name"] == "AxonHub Live Smoke"
    assert data["provider"]["api_key_saved"] is True
    assert data["provider"]["api_key_fingerprint"] == fingerprint_secret("live-test-secret")
    assert "live-test-secret" not in str(data)
    assert data["model"]["model_id"] == "glm-5"
    assert data["run"]["status"] in {"completed", "failed"}
    assert data["run"]["progress"]["total"] == 2
    assert data["leaderboard_row"]["model"] == "GLM-5 Live Smoke"

    session = SessionLocal()
    try:
        provider = session.query(Provider).filter(Provider.name == "AxonHub Live Smoke").one()
        assert decrypt_secret(provider.encrypted_api_key) == "live-test-secret"
    finally:
        session.close()


def test_live_smoke_summary_classifies_success_and_failures_without_secret_leak():
    session = SessionLocal()
    try:
        provider = Provider(
            name="smoke-summary-provider",
            api_base="http://127.0.0.1:8091/v1",
            encrypted_api_key=encrypt_secret("summary-secret"),
            api_key_fingerprint=fingerprint_secret("summary-secret"),
        )
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Smoke Summary Model", model_id="glm-5")
        session.add(model)
        tasks = [
            Task(
                slug="smoke-ok",
                title="OK",
                category="smoke",
                dimension="classification",
                prompt="return ok",
                evaluator_type="exact_match",
                evaluator_config_json='{"answer":"ok"}',
                content_hash="hash-smoke-ok",
            ),
            Task(
                slug="smoke-call-fail",
                title="Call Fail",
                category="smoke",
                dimension="classification",
                prompt="return ok",
                evaluator_type="exact_match",
                evaluator_config_json='{"answer":"ok"}',
                content_hash="hash-smoke-fail",
            ),
            Task(
                slug="smoke-eval-fail",
                title="Eval Fail",
                category="smoke",
                dimension="classification",
                prompt="return ok",
                evaluator_type="exact_match",
                evaluator_config_json='{"answer":"ok"}',
                content_hash="hash-smoke-eval",
            ),
        ]
        session.add_all(tasks)
        session.flush()
        run = BenchmarkRun(run_id="smoke-summary-run", model_id=model.id, suite_slug="smoke", status="completed")
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
                    score=1,
                ),
                TaskResult(
                    run_id=run.run_id,
                    model_id=model.id,
                    task_id=tasks[1].id,
                    task_hash=tasks[1].content_hash,
                    prompt=tasks[1].prompt,
                    status="failed",
                    error="HTTP 500 from provider with key summary-secret",
                ),
                TaskResult(
                    run_id=run.run_id,
                    model_id=model.id,
                    task_id=tasks[2].id,
                    task_hash=tasks[2].content_hash,
                    prompt=tasks[2].prompt,
                    status="failed",
                    error="Evaluator exact_match missing expected answer",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    with TestClient(app) as client:
        summary = client.get("/api/smoke/runs/smoke-summary-run").json()

    assert summary["run_id"] == "smoke-summary-run"
    assert summary["status"] == "completed"
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 2
    assert summary["failure_types"] == {"call_failed": 1, "evaluator_failed": 1}
    assert "summary-secret" not in str(summary)
    assert summary["failures"][0]["error"] == "HTTP 500 from provider with key [REDACTED]"
