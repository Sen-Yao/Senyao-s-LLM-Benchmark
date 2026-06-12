from fastapi.testclient import TestClient

from backend.app.api.runs import _model_current_results
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
            "cancelled": 0,
            "unknown": 0,
            "accounted": 3,
            "missing": 0,
            "percent": 67,
            "current_task": "run-visibility-running",
        }
        assert visible["failure_summary"] == {"count": 1, "latest_error": "provider timeout"}
        assert visible["model_display_name"] == "Run Visibility Model"
        assert visible["model_api_id"] == "run-visibility-model"
        assert visible["provider_name"] == "run-visibility-provider"


def test_run_detail_exposes_model_identity():
    run_id = _seed_run_with_results()

    with TestClient(app) as client:
        detail = client.get(f"/api/runs/{run_id}").json()

    assert detail["model_display_name"] == "Run Visibility Model"
    assert detail["model_api_id"] == "run-visibility-model"
    assert detail["provider_name"] == "run-visibility-provider"


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


def test_create_run_materializes_pending_task_list_before_background_execution(monkeypatch):
    async def noop(payload, run_ids=None):
        return None

    monkeypatch.setattr("backend.app.api.runs._run_many", noop)
    with TestClient(app) as client:
        with SessionLocal() as session:
            provider = Provider(name="queued-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
            session.add(provider)
            session.flush()
            model = LLMModel(provider_id=provider.id, display_name="Queued Model", model_id="queued-model")
            session.add(model)
            tasks = [
                Task(slug="queued-a", title="Queued A", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-a"),
                Task(slug="queued-b", title="Queued B", category="core", dimension="accuracy", prompt="B", evaluator_type="contains", evaluator_config_json='{"contains":"B"}', content_hash="hash-b"),
            ]
            session.add_all(tasks)
            session.commit()
            model_id = model.id
        created = client.post("/api/runs", json={"model_ids": [model_id], "task_slugs": ["queued-a", "queued-b"]}).json()
        assert created["status"] == "queued"
        assert len(created["runs"]) == 1
        run_id = created["runs"][0]["run_id"]
        detail = client.get(f"/api/runs/{run_id}").json()

    assert detail["status"] == "pending"
    assert detail["progress"] == {
        "total": 2,
        "completed": 0,
        "failed": 0,
        "running": 0,
        "pending": 2,
        "cancelled": 0,
        "unknown": 0,
        "accounted": 2,
        "missing": 0,
        "percent": 0,
        "current_task": "",
    }
    assert [row["status"] for row in detail["results"]] == ["pending", "pending"]


def test_cancel_run_marks_pending_and_running_results_cancelled():
    run_id = _seed_run_with_results()
    with SessionLocal() as session:
        run = session.query(BenchmarkRun).filter_by(run_id=run_id).one()
        task = Task(slug="run-visibility-pending", title="Pending", category="core", dimension="accuracy", prompt="Wait", evaluator_type="contains", evaluator_config_json='{"contains":"WAIT"}', content_hash="hash-pending")
        session.add(task)
        session.flush()
        session.add(TaskResult(run_id=run.run_id, model_id=run.model_id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()

    with TestClient(app) as client:
        result = client.post(f"/api/runs/{run_id}/cancel").json()
        detail = client.get(f"/api/runs/{run_id}").json()

    assert result == {"ok": True, "run_id": run_id, "cancelled": 2}
    assert detail["status"] == "cancelled"
    assert detail["progress"]["cancelled"] == 2
    assert detail["progress"]["percent"] == 100



def test_runner_reuses_materialized_pending_rows_and_exposes_full_total_during_execution(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    observed = {}

    async def fake_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        with SessionLocal() as inspect_session:
            rows = inspect_session.query(TaskResult).filter_by(run_id="materialized-run").order_by(TaskResult.id.asc()).all()
            observed.setdefault("snapshots", []).append([(row.prompt, row.status) for row in rows])
        return type("Call", (), {"text": prompt, "latency": 0.01, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", fake_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="materialized-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Materialized Model", model_id="materialized-model")
        session.add(model)
        tasks = [
            Task(slug="materialized-a", title="Materialized A", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-a"),
            Task(slug="materialized-b", title="Materialized B", category="core", dimension="accuracy", prompt="B", evaluator_type="contains", evaluator_config_json='{"contains":"B"}', content_hash="hash-b"),
        ]
        session.add_all(tasks)
        run = BenchmarkRun(run_id="materialized-run", model_id=1, suite_slug="all", status="pending")
        session.add(run)
        session.flush()
        run.model_id = model.id
        for task in tasks:
            session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        model_id = model.id
        asyncio.run(run_model_tasks(session, model_id, ["materialized-a", "materialized-b"], run_id="materialized-run"))
        final_rows = session.query(TaskResult).filter_by(run_id="materialized-run").order_by(TaskResult.id.asc()).all()

    assert observed["snapshots"][0] == [("A", "running"), ("B", "pending")]
    assert len(final_rows) == 2
    assert [row.status for row in final_rows] == ["success", "success"]



def test_multiple_runs_same_model_task_keep_separate_run_progress_and_leaderboard_dedupes_latest_current_results():
    from backend.app.api.leaderboard import leaderboard

    with SessionLocal() as session:
        provider = Provider(name="idempotency-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Idempotency Model", model_id="idempotency-model")
        session.add(model)
        tasks = [
            Task(slug="idem-a", title="Idem A", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-a", source_path="tasks/idem-a.yaml"),
            Task(slug="idem-b", title="Idem B", category="core", dimension="accuracy", prompt="B", evaluator_type="contains", evaluator_config_json='{"contains":"B"}', content_hash="hash-b", source_path="tasks/idem-b.yaml"),
        ]
        session.add_all(tasks)
        session.flush()
        run1 = BenchmarkRun(run_id="idem-run-1", model_id=model.id, suite_slug="all", status="completed")
        run2 = BenchmarkRun(run_id="idem-run-2", model_id=model.id, suite_slug="all", status="running")
        session.add_all([run1, run2])
        session.flush()
        session.add_all([
            TaskResult(run_id="idem-run-1", model_id=model.id, task_id=tasks[0].id, task_hash="hash-a", prompt="A", status="success", score=10, response="A-old"),
            TaskResult(run_id="idem-run-1", model_id=model.id, task_id=tasks[1].id, task_hash="hash-b", prompt="B", status="success", score=20, response="B-old"),
            TaskResult(run_id="idem-run-2", model_id=model.id, task_id=tasks[0].id, task_hash="hash-a", prompt="A", status="success", score=90, response="A-new"),
            TaskResult(run_id="idem-run-2", model_id=model.id, task_id=tasks[1].id, task_hash="hash-b", prompt="B", status="pending"),
        ])
        session.commit()

        from backend.app.api.runs import run_detail
        detail1 = run_detail("idem-run-1", session)
        detail2 = run_detail("idem-run-2", session)
        board = leaderboard(session)
        row = next(r for r in board["rows"] if r["model"] == "Idempotency Model")

    assert detail1["progress"]["total"] == 2
    assert detail1["progress"]["completed"] == 2
    assert [r["response"] for r in detail1["results"]] == ["A-old", "B-old"]
    assert detail2["progress"]["total"] == 2
    assert detail2["progress"]["completed"] == 1
    assert detail2["progress"]["pending"] == 1
    assert [r["response"] for r in detail2["results"]] == ["A-new", ""]
    assert row["coverage"] == {"current": 2, "stale": 0, "total": 2, "status": "complete"}
    assert row["overall"] == 55


def test_leaderboard_shows_latest_stale_results_when_current_hash_is_missing():
    from backend.app.api.leaderboard import leaderboard

    with SessionLocal() as session:
        provider = Provider(
            name="stale-leaderboard-provider",
            api_base="http://127.0.0.1:9999/v1",
            encrypted_api_key=encrypt_secret("test-secret"),
            api_key_fingerprint=fingerprint_secret("test-secret"),
        )
        session.add(provider)
        session.flush()
        model = LLMModel(
            provider_id=provider.id,
            display_name="Stale Leaderboard Model",
            model_id="stale-leaderboard-model",
        )
        task = Task(
            slug="stale-leaderboard-task",
            title="Stale Leaderboard Task",
            category="core",
            dimension="accuracy",
            prompt="A",
            evaluator_type="contains",
            evaluator_config_json='{"contains":"A"}',
            content_hash="new-hash",
            source_path="tasks/stale-leaderboard-task.yaml",
        )
        session.add_all([model, task])
        session.flush()
        session.add(
            TaskResult(
                run_id="stale-leaderboard-run",
                model_id=model.id,
                task_id=task.id,
                task_hash="old-hash",
                prompt="A old",
                status="success",
                score=77,
                response="A",
            )
        )
        session.commit()

        board = leaderboard(session)
        row = next(r for r in board["rows"] if r["model"] == "Stale Leaderboard Model")

    assert row["overall"] == 77
    assert row["dimensions"] == {"accuracy": 77.0}
    assert row["coverage"] == {"current": 0, "stale": 1, "total": 1, "status": "stale"}


def test_progress_accounts_for_unknown_status_and_exposes_timestamps():
    with SessionLocal() as session:
        provider = Provider(name="unknown-status-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Unknown Status Model", model_id="unknown-status-model")
        session.add(model)
        task = Task(slug="unknown-status-task", title="Unknown Status", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-unknown")
        session.add(task)
        session.flush()
        run = BenchmarkRun(run_id="unknown-status-run", model_id=model.id, suite_slug="all", status="running")
        session.add(run)
        session.flush()
        result = TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="queued")
        session.add(result)
        session.commit()

        from backend.app.api.runs import run_detail
        detail = run_detail("unknown-status-run", session)

    assert detail["progress"]["total"] == 1
    assert detail["progress"]["unknown"] == 1
    assert detail["progress"]["accounted"] == 1
    assert detail["progress"]["missing"] == 0
    assert detail["created_at"]
    assert detail["updated_at"]
    assert detail["results"][0]["created_at"]
    assert detail["results"][0]["updated_at"]
    assert "started_at" in detail["results"][0]
    assert "finished_at" in detail["results"][0]


def test_empty_model_response_is_failed_instead_of_success(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    async def fake_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        return type("Call", (), {"text": "   ", "latency": 0.01, "input_tokens": 1, "output_tokens": 0})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", fake_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="empty-response-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Empty Response Model", model_id="empty-response-model")
        session.add(model)
        task = Task(slug="empty-response-task", title="Empty Response", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-empty")
        session.add(task)
        session.flush()
        run = BenchmarkRun(run_id="empty-response-run", model_id=model.id, suite_slug="all", status="pending")
        session.add(run)
        session.flush()
        session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        asyncio.run(run_model_tasks(session, model.id, ["empty-response-task"], run_id="empty-response-run"))
        row = session.query(TaskResult).filter_by(run_id="empty-response-run").one()

    assert row.status == "failed"
    assert "空回答" in row.error
    assert row.response == ""


def test_model_current_results_merges_latest_fresh_results_across_runs():
    with SessionLocal() as session:
        provider = Provider(name="current-results-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Current Results Model", model_id="current-results-model")
        session.add(model)
        tasks = [
            Task(slug="current-success", title="Current Success", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-current-success", semantic_hash="sem-current-success", evaluator_version="v2", source_path="tasks/current-success.yaml"),
            Task(slug="current-failed", title="Current Failed", category="core", dimension="accuracy", prompt="B", evaluator_type="contains", evaluator_config_json='{"contains":"B"}', content_hash="hash-current-failed", semantic_hash="sem-current-failed", evaluator_version="v2", source_path="tasks/current-failed.yaml"),
            Task(slug="current-stale", title="Current Stale", category="core", dimension="reasoning", prompt="C", evaluator_type="contains", evaluator_config_json='{"contains":"C"}', content_hash="hash-current-stale", semantic_hash="sem-current-stale", evaluator_version="v2", source_path="tasks/current-stale.yaml"),
            Task(slug="current-pending", title="Current Pending", category="core", dimension="reasoning", prompt="D", evaluator_type="contains", evaluator_config_json='{"contains":"D"}', content_hash="hash-current-pending", semantic_hash="sem-current-pending", evaluator_version="v2", source_path="tasks/current-pending.yaml"),
        ]
        session.add_all(tasks)
        session.flush()
        runs = [
            BenchmarkRun(run_id="current-run-old", model_id=model.id, suite_slug="all", status="completed"),
            BenchmarkRun(run_id="current-run-new", model_id=model.id, suite_slug="all", status="completed"),
            BenchmarkRun(run_id="current-run-active", model_id=model.id, suite_slug="all", status="running"),
        ]
        session.add_all(runs)
        session.flush()
        session.add_all([
            TaskResult(run_id="current-run-old", model_id=model.id, task_id=tasks[0].id, task_hash=tasks[0].content_hash, semantic_hash="sem-current-success", evaluator_version="v2", prompt="A", status="success", score=40, response="old success"),
            TaskResult(run_id="current-run-new", model_id=model.id, task_id=tasks[0].id, task_hash=tasks[0].content_hash, semantic_hash="sem-current-success", evaluator_version="v2", prompt="A", status="success", score=90, response="new success"),
            TaskResult(run_id="current-run-old", model_id=model.id, task_id=tasks[1].id, task_hash=tasks[1].content_hash, semantic_hash="sem-current-failed", evaluator_version="v2", prompt="B", status="success", score=80, response="old ok"),
            TaskResult(run_id="current-run-new", model_id=model.id, task_id=tasks[1].id, task_hash=tasks[1].content_hash, semantic_hash="sem-current-failed", evaluator_version="v2", prompt="B", status="failed", score=0, response="", error="fresh fail"),
            TaskResult(run_id="current-run-old", model_id=model.id, task_id=tasks[2].id, task_hash="old-hash", semantic_hash="old-sem", evaluator_version="v1", prompt="C", status="success", score=70, response="stale success"),
            TaskResult(run_id="current-run-active", model_id=model.id, task_id=tasks[3].id, task_hash=tasks[3].content_hash, semantic_hash="sem-current-pending", evaluator_version="v2", prompt="D", status="running"),
        ])
        session.commit()
        model_id = model.id

    with SessionLocal() as session:
        payload = _model_current_results(session, model_id)

    by_slug = {row["task_slug"]: row for row in payload["results"] if row["task_slug"].startswith("current-")}
    assert by_slug["current-success"]["response"] == "new success"
    assert by_slug["current-success"]["score"] == 90
    assert by_slug["current-success"]["freshness"] == "fresh"
    assert by_slug["current-failed"]["status"] == "failed"
    assert by_slug["current-failed"]["error"] == "fresh fail"
    assert by_slug["current-failed"]["freshness"] == "fresh"
    assert by_slug["current-stale"]["response"] == "stale success"
    assert by_slug["current-stale"]["freshness"] == "stale"
    assert by_slug["current-pending"]["status"] == "pending"
    assert by_slug["current-pending"]["active_status"] == "running"
    assert by_slug["current-pending"]["freshness"] == "pending"
    assert payload["coverage"]["current"] >= 2
    assert payload["coverage"]["stale"] >= 1
    assert payload["coverage"]["pending"] >= 1


def test_model_current_results_coverage_is_not_latest_run_progress():
    with SessionLocal() as session:
        provider = Provider(name="coverage-source-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Coverage Source Model", model_id="coverage-source-model")
        session.add(model)
        tasks = [
            Task(slug="coverage-source-a", title="Coverage Source A", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-coverage-a", semantic_hash="sem-coverage-a", evaluator_version="v2", source_path="tasks/coverage-source-a.yaml"),
            Task(slug="coverage-source-b", title="Coverage Source B", category="core", dimension="accuracy", prompt="B", evaluator_type="contains", evaluator_config_json='{"contains":"B"}', content_hash="hash-coverage-b", semantic_hash="sem-coverage-b", evaluator_version="v2", source_path="tasks/coverage-source-b.yaml"),
            Task(slug="coverage-source-c", title="Coverage Source C", category="core", dimension="accuracy", prompt="C", evaluator_type="contains", evaluator_config_json='{"contains":"C"}', content_hash="hash-coverage-c", semantic_hash="sem-coverage-c", evaluator_version="v2", source_path="tasks/coverage-source-c.yaml"),
        ]
        session.add_all(tasks)
        session.flush()
        session.add_all([
            BenchmarkRun(run_id="coverage-source-old", model_id=model.id, suite_slug="all", status="completed"),
            BenchmarkRun(run_id="coverage-source-latest", model_id=model.id, suite_slug="all", status="running"),
        ])
        session.flush()
        session.add_all([
            TaskResult(run_id="coverage-source-old", model_id=model.id, task_id=tasks[0].id, task_hash=tasks[0].content_hash, semantic_hash="sem-coverage-a", evaluator_version="v2", prompt="A", status="success", score=100, response="A"),
            TaskResult(run_id="coverage-source-old", model_id=model.id, task_id=tasks[1].id, task_hash=tasks[1].content_hash, semantic_hash="sem-coverage-b", evaluator_version="v2", prompt="B", status="success", score=80, response="B"),
            TaskResult(run_id="coverage-source-latest", model_id=model.id, task_id=tasks[2].id, task_hash=tasks[2].content_hash, semantic_hash="sem-coverage-c", evaluator_version="v2", prompt="C", status="pending"),
        ])
        session.commit()
        model_id = model.id

    with SessionLocal() as session:
        from backend.app.api.runs import run_detail
        latest = run_detail("coverage-source-latest", session)
        payload = _model_current_results(session, model_id)

    scoped = {row["task_slug"]: row for row in payload["results"] if row["task_slug"].startswith("coverage-source-")}
    assert latest["progress"]["completed"] == 0
    assert latest["progress"]["total"] == 1
    assert [scoped["coverage-source-a"]["freshness"], scoped["coverage-source-b"]["freshness"], scoped["coverage-source-c"]["freshness"]] == ["fresh", "fresh", "pending"]
    assert sum(1 for row in scoped.values() if row["freshness"] == "fresh") == 2


def test_runner_skips_existing_success_with_same_task_hash(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    calls = {"count": 0}

    async def fake_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        calls["count"] += 1
        return type("Call", (), {"text": prompt, "latency": 0.01, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", fake_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="incremental-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Incremental Model", model_id="incremental-model")
        session.add(model)
        task = Task(slug="incremental-task", title="Incremental", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-incremental")
        session.add(task)
        session.flush()
        old_run = BenchmarkRun(run_id="incremental-old", model_id=model.id, suite_slug="all", status="completed")
        new_run = BenchmarkRun(run_id="incremental-new", model_id=model.id, suite_slug="all", status="pending")
        session.add_all([old_run, new_run])
        session.flush()
        session.add(TaskResult(run_id=old_run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="success", score=88, response="cached answer", judge_reason="cached reason"))
        session.add(TaskResult(run_id=new_run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()

        asyncio.run(run_model_tasks(session, model.id, ["incremental-task"], run_id="incremental-new"))
        row = session.query(TaskResult).filter_by(run_id="incremental-new").one()

    assert calls["count"] == 0
    assert row.status == "success"
    assert row.score == 88
    assert row.response == "cached answer"
    assert row.latency == 0.0
    assert row.started_at is not None
    assert row.finished_at is not None


def test_runner_force_rerun_ignores_existing_success_with_same_task_hash(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    calls = {"count": 0}

    async def fake_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        calls["count"] += 1
        return type("Call", (), {"text": "fresh answer", "latency": 0.01, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", fake_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="force-rerun-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Force Rerun Model", model_id="force-rerun-model")
        session.add(model)
        task = Task(slug="force-rerun-task", title="Force Rerun", category="core", dimension="accuracy", prompt="fresh", evaluator_type="contains", evaluator_config_json='{"contains":"fresh"}', content_hash="hash-force-rerun")
        session.add(task)
        session.flush()
        old_run = BenchmarkRun(run_id="force-rerun-old", model_id=model.id, suite_slug="all", status="completed")
        new_run = BenchmarkRun(run_id="force-rerun-new", model_id=model.id, suite_slug="all", status="pending")
        session.add_all([old_run, new_run])
        session.flush()
        session.add(TaskResult(run_id=old_run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="success", score=88, response="cached answer", judge_reason="cached reason"))
        session.add(TaskResult(run_id=new_run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()

        asyncio.run(run_model_tasks(session, model.id, ["force-rerun-task"], run_id="force-rerun-new", force_rerun=True))
        row = session.query(TaskResult).filter_by(run_id="force-rerun-new").one()

    assert calls["count"] == 1
    assert row.status == "success"
    assert row.score == 100
    assert row.response == "fresh answer"
    assert row.latency == 0.01
    assert row.attempt_count == 1



def test_create_run_accepts_concurrency_configuration(monkeypatch):
    async def noop(payload, run_ids=None):
        return None

    monkeypatch.setattr("backend.app.api.runs._run_many", noop)
    with TestClient(app) as client:
        with SessionLocal() as session:
            provider = Provider(name="concurrency-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
            session.add(provider)
            session.flush()
            model = LLMModel(provider_id=provider.id, display_name="Concurrency Model", model_id="concurrency-model")
            task = Task(slug="concurrency-task", title="Concurrency Task", category="core", dimension="accuracy", prompt="A", evaluator_type="contains", evaluator_config_json='{"contains":"A"}', content_hash="hash-concurrency")
            session.add_all([model, task])
            session.commit()
            model_id = model.id
        created = client.post("/api/runs", json={"model_ids": [model_id], "task_slugs": ["concurrency-task"], "max_concurrency": 3}).json()

    assert created["status"] == "queued"
    assert created["max_concurrency"] == 3
    assert created["runs"][0]["progress"]["total"] == 1


def test_create_run_rejects_empty_model_selection():
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"model_ids": [], "max_concurrency": 3})
    assert response.status_code == 422


def test_run_many_starts_selected_models_concurrently(monkeypatch):
    import asyncio

    from backend.app.api.runs import _run_many
    from backend.app.schemas import RunRequest

    active = {"count": 0, "max": 0}
    seen_models = []

    async def fake_run_model_tasks(session, model_id, task_slugs, judge_profile_id=None, suite="all", **kwargs):
        seen_models.append(model_id)
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        await asyncio.sleep(0.02)
        active["count"] -= 1

    monkeypatch.setattr("backend.app.api.runs.run_model_tasks", fake_run_model_tasks)

    payload = RunRequest(model_ids=[101, 202], task_slugs=["parallel-model-task"], max_concurrency=1)
    asyncio.run(_run_many(payload, run_ids=["run-a", "run-b"]))

    assert active["max"] == 2
    assert set(seen_models) == {101, 202}


def test_runner_retries_failed_task_before_marking_success(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    attempts = {"count": 0}

    async def flaky_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary provider failure")
        return type("Call", (), {"text": "OK", "latency": 0.01, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", flaky_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="retry-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Retry Model", model_id="retry-model")
        task = Task(slug="retry-task", title="Retry Task", category="core", dimension="accuracy", prompt="OK", evaluator_type="contains", evaluator_config_json='{"contains":"OK"}', content_hash="hash-retry")
        session.add_all([model, task])
        session.flush()
        run = BenchmarkRun(run_id="retry-run", model_id=model.id, suite_slug="all", status="pending")
        session.add(run)
        session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        asyncio.run(run_model_tasks(session, model.id, ["retry-task"], run_id="retry-run", max_retries=3, retry_backoff_base=0))
        session.expire_all()
        row = session.query(TaskResult).filter_by(run_id="retry-run").one()

    assert attempts["count"] == 3
    assert row.status == "success"
    assert row.response == "OK"
    assert row.error == ""
    assert row.attempt_count == 3
    assert row.max_retries == 3
    with TestClient(app) as client:
        detail = client.get("/api/runs/retry-run").json()
    assert detail["results"][0]["attempt_count"] == 3
    assert detail["results"][0]["max_retries"] == 3


def test_runner_fails_after_retry_budget_is_exhausted(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    attempts = {"count": 0}

    async def always_fails(api_base, api_key, model_id, prompt, **kwargs):
        attempts["count"] += 1
        raise RuntimeError(f"boom-{attempts['count']}")

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", always_fails)
    with SessionLocal() as session:
        provider = Provider(name="retry-exhaust-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Retry Exhaust Model", model_id="retry-exhaust-model")
        task = Task(slug="retry-exhaust-task", title="Retry Exhaust Task", category="core", dimension="accuracy", prompt="OK", evaluator_type="contains", evaluator_config_json='{"contains":"OK"}', content_hash="hash-retry-exhaust")
        session.add_all([model, task])
        session.flush()
        run = BenchmarkRun(run_id="retry-exhaust-run", model_id=model.id, suite_slug="all", status="pending")
        session.add(run)
        session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        asyncio.run(run_model_tasks(session, model.id, ["retry-exhaust-task"], run_id="retry-exhaust-run", max_retries=2, retry_backoff_base=0))
        session.expire_all()
        row = session.query(TaskResult).filter_by(run_id="retry-exhaust-run").one()

    assert attempts["count"] == 3
    assert row.status == "failed"
    assert "boom-3" in row.error
    assert row.attempt_count == 3
    assert row.max_retries == 2


def test_runner_times_out_slow_attempt_and_retries_successfully(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    attempts = {"count": 0}

    async def slow_then_success(api_base, api_key, model_id, prompt, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            await asyncio.sleep(0.05)
            return type("Call", (), {"text": "TOO LATE", "latency": 0.05, "input_tokens": 1, "output_tokens": 1})()
        return type("Call", (), {"text": "OK", "latency": 0.01, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", slow_then_success)
    with SessionLocal() as session:
        provider = Provider(name="timeout-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Timeout Model", model_id="timeout-model")
        task = Task(slug="timeout-task", title="Timeout Task", category="core", dimension="accuracy", prompt="OK", evaluator_type="contains", evaluator_config_json='{"contains":"OK"}', content_hash="hash-timeout")
        session.add_all([model, task])
        session.flush()
        run = BenchmarkRun(run_id="timeout-run", model_id=model.id, suite_slug="all", status="pending")
        session.add(run)
        session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        asyncio.run(run_model_tasks(session, model.id, ["timeout-task"], run_id="timeout-run", max_retries=1, retry_backoff_base=0, attempt_timeout=0.01))
        session.expire_all()
        row = session.query(TaskResult).filter_by(run_id="timeout-run").one()

    assert attempts["count"] == 2
    assert row.status == "success"
    assert row.response == "OK"
    assert row.attempt_count == 2
    assert row.error == ""


def test_runner_processes_tasks_concurrently_within_single_run(monkeypatch):
    import asyncio
    from backend.app.services.benchmark import run_model_tasks

    active = {"count": 0, "max": 0}

    async def slow_chat_completion(api_base, api_key, model_id, prompt, **kwargs):
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        await asyncio.sleep(0.05)
        active["count"] -= 1
        return type("Call", (), {"text": prompt, "latency": 0.05, "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr("backend.app.services.benchmark.chat_completion", slow_chat_completion)
    with SessionLocal() as session:
        provider = Provider(name="parallel-provider", api_base="http://127.0.0.1:9999/v1", encrypted_api_key=encrypt_secret("test-secret"), api_key_fingerprint=fingerprint_secret("test-secret"))
        session.add(provider)
        session.flush()
        model = LLMModel(provider_id=provider.id, display_name="Parallel Model", model_id="parallel-model")
        session.add(model)
        session.flush()
        tasks=[]
        for slug in ["parallel-a", "parallel-b", "parallel-c"]:
            task = Task(slug=slug, title=slug, category="core", dimension="accuracy", prompt=slug, evaluator_type="contains", evaluator_config_json=f'{{"contains":"{slug}"}}', content_hash=f"hash-{slug}")
            tasks.append(task)
        session.add_all(tasks)
        session.flush()
        run = BenchmarkRun(run_id="parallel-run", model_id=model.id, suite_slug="all", status="pending")
        session.add(run)
        for task in tasks:
            session.add(TaskResult(run_id=run.run_id, model_id=model.id, task_id=task.id, task_hash=task.content_hash, prompt=task.prompt, status="pending"))
        session.commit()
        asyncio.run(run_model_tasks(session, model.id, [t.slug for t in tasks], run_id="parallel-run", max_concurrency=2))
        session.expire_all()
        rows = session.query(TaskResult).filter_by(run_id="parallel-run").all()

    assert active["max"] == 2
    assert {row.status for row in rows} == {"success"}
