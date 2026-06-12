from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app, startup
from backend.app.models import BenchmarkRun, JudgeProfile, LLMModel, Provider, Task, TaskResult


def test_management_crud_and_incremental_queue():
    with TestClient(app) as client:
        provider_payload = {
            "name": "local-test-provider",
            "api_base": "http://127.0.0.1:9999/v1",
            "api_key": "test-secret",
        }
        provider = client.post("/api/providers", json=provider_payload).json()
        assert provider["name"] == "local-test-provider"
        assert provider["api_key_saved"] is True
        assert provider["api_key_fingerprint"]

        with SessionLocal() as session:
            saved_provider = session.get(Provider, provider["id"])
            assert saved_provider is not None
            assert saved_provider.name == "local-test-provider"
            assert saved_provider.encrypted_api_key != "test-secret"

        listed_models = client.get(f"/api/providers/{provider['id']}/models").json()
        assert listed_models["ok"] is False
        assert listed_models["models"] == []
        assert listed_models["error"]

        patched = client.patch(
            f"/api/providers/{provider['id']}",
            json={"notes": "patched", "enabled": False},
        ).json()
        assert patched["notes"] == "patched"
        assert patched["enabled"] is False

        model = client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "display_name": "Local Test Model",
                "model_id": "local-test-model",
            },
        ).json()
        assert model["provider_name"] == "local-test-provider"

        with SessionLocal() as session:
            saved_model = session.get(LLMModel, model["id"])
            assert saved_model is not None
            assert saved_model.provider_id == provider["id"]
            assert saved_model.model_id == "local-test-model"

        model = client.patch(
            f"/api/models/{model['id']}",
            json={"display_name": "Local Test Model v2", "enabled": False},
        ).json()
        assert model["display_name"] == "Local Test Model v2"
        assert model["enabled"] is False

        judge = client.post(
            "/api/judges",
            json={
                "provider_id": provider["id"],
                "name": "Local Judge",
                "model_id": "local-judge-model",
                "temperature": 0,
            },
        ).json()
        assert judge["provider_name"] == "local-test-provider"

        with SessionLocal() as session:
            saved_judge = session.get(JudgeProfile, judge["id"])
            assert saved_judge is not None
            assert saved_judge.provider_id == provider["id"]
            assert saved_judge.model_id == "local-judge-model"
            assert saved_judge.temperature == 0

        judge = client.patch(f"/api/judges/{judge['id']}", json={"temperature": 0.2}).json()
        assert judge["temperature"] == 0.2

        settings = client.patch("/api/settings", json={"default_judge_profile_id": judge["id"]}).json()
        assert settings["default_judge_profile_id"] == judge["id"]
        assert settings["default_judge"]["name"] == "Local Judge"

        queued_with_default = client.post(
            "/api/runs/incremental/task/example-task",
            json={"model_ids": [model["id"]]},
        ).json()
        assert queued_with_default["status"] == "queued"
        assert queued_with_default["judge_profile_id"] == judge["id"]

        queued = client.post(
            "/api/runs/incremental/task/example-task",
            json={"model_ids": [model["id"]], "judge_profile_id": judge["id"]},
        ).json()
        assert queued["status"] == "queued"
        assert queued["task_slug"] == "example-task"
        assert queued["model_ids"] == [model["id"]]
        assert "runs" in queued

        assert client.delete(f"/api/judges/{judge['id']}").json() == {"ok": True}
        assert client.delete(f"/api/models/{model['id']}").json() == {"ok": True}
        assert client.delete(f"/api/providers/{provider['id']}").json() == {"ok": True}


def test_startup_syncs_yaml_tasks_into_database(monkeypatch):
    monkeypatch.setattr("backend.app.main.settings.tasks_dir", Path("tasks"))
    startup()
    with SessionLocal() as session:
        assert session.query(Task).count() >= 20
        assert session.query(Task).filter(Task.slug == "transaction_classify").one_or_none() is not None


def test_provider_delete_cascades_dependent_models_judges_runs_and_results():
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={"name": "delete-provider", "api_base": "http://127.0.0.1:9999/v1", "api_key": "test-secret"},
        ).json()
        model = client.post(
            "/api/models",
            json={"provider_id": provider["id"], "display_name": "Delete Model", "model_id": "delete-model"},
        ).json()
        judge = client.post(
            "/api/judges",
            json={"provider_id": provider["id"], "name": "Delete Judge", "model_id": "delete-judge"},
        ).json()
        with SessionLocal() as session:
            task = Task(
                slug="delete-task",
                title="Delete Task",
                category="reasoning",
                dimension="reasoning",
                task_type="llm_judged",
                prompt="Answer yes.",
                evaluator_type="contains",
                evaluator_config_json='{"contains":"yes"}',
                content_hash="hash",
                source_path="reasoning/delete-task.yaml",
                active=True,
            )
            session.add(task)
            session.flush()
            run = BenchmarkRun(run_id="delete-run", model_id=model["id"], status="completed")
            result = TaskResult(
                run_id="delete-run",
                model_id=model["id"],
                task_id=task.id,
                task_hash="hash",
                prompt="Answer yes.",
                response="yes",
                score=100,
                status="success",
            )
            session.add_all([run, result])
            session.commit()

        assert client.delete(f"/api/providers/{provider['id']}").json() == {"ok": True}

        with SessionLocal() as session:
            assert session.get(Provider, provider["id"]) is None
            assert session.get(LLMModel, model["id"]) is None
            assert session.get(JudgeProfile, judge["id"]) is None
            assert session.query(BenchmarkRun).filter_by(run_id="delete-run").one_or_none() is None
            assert session.query(TaskResult).filter_by(run_id="delete-run").one_or_none() is None


def test_provider_patch_updates_name_endpoint_and_key_reveal():
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={"name": "editable-provider", "api_base": "http://old.example/v1", "api_key": "old-secret"},
        ).json()
        patched = client.patch(
            f"/api/providers/{provider['id']}",
            json={"name": "edited-provider", "api_base": "http://new.example/v1", "api_key": "new-secret"},
        ).json()
        assert patched["name"] == "edited-provider"
        assert patched["api_base"] == "http://new.example/v1"
        assert patched["api_key_fingerprint"] != provider["api_key_fingerprint"]
        revealed = client.get(f"/api/providers/{provider['id']}/secret").json()
        assert revealed == {"api_key": "new-secret"}


def test_provider_test_uses_max_tokens_for_openai_compatible_chat(monkeypatch):
    captured = {}

    async def fake_chat_completion(api_base, api_key, model_id, prompt, temperature=0.0, timeout=120.0, max_tokens=None):
        captured.update({
            "api_base": api_base,
            "api_key": api_key,
            "model_id": model_id,
            "prompt": prompt,
            "temperature": temperature,
            "timeout": timeout,
            "max_tokens": max_tokens,
        })
        from backend.app.adapters.openai_compatible import ModelCallResult
        return ModelCallResult(text="OK", latency=0.01)

    monkeypatch.setattr("backend.app.api.providers.chat_completion", fake_chat_completion)
    with TestClient(app) as client:
        provider = client.post(
            "/api/providers",
            json={"name": "chat-provider", "api_base": "http://chat.example/v1", "api_key": "chat-secret"},
        ).json()
        result = client.post(f"/api/providers/{provider['id']}/test", json={"model_id": "chat-model"}).json()

    assert result["ok"] is True
    assert captured["model_id"] == "chat-model"
    assert captured["max_tokens"] == 8
