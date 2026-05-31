from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import LLMModel, Provider, Task, TaskChangeEvent, TaskResult
from backend.app.security.crypto import encrypt_secret, fingerprint_secret


def test_task_change_events_and_stale_result_visibility():
    suffix = uuid4().hex[:8]
    provider_name = f"visibility-provider-{suffix}"
    task_slug = f"visibility-task-{suffix}"
    with SessionLocal() as session:
        provider = Provider(
            name=provider_name,
            api_base="http://127.0.0.1:9999/v1",
            encrypted_api_key=encrypt_secret("test-secret"),
            api_key_fingerprint=fingerprint_secret("test-secret"),
        )
        session.add(provider)
        session.commit()
        session.refresh(provider)

        model = LLMModel(
            provider_id=provider.id,
            display_name=f"Visibility Model {suffix}",
            model_id=f"visibility-model-{suffix}",
        )
        task = Task(
            slug=task_slug,
            title="Visibility Task",
            category="reasoning",
            dimension="reasoning",
            prompt="Answer yes.",
            evaluator_type="contains",
            evaluator_config_json='{"contains":"yes"}',
            content_hash="new-hash",
            source_path="reasoning/visibility-task.yaml",
            active=True,
        )
        session.add_all([model, task])
        session.commit()
        session.refresh(model)
        session.refresh(task)

        session.add(
            TaskResult(
                run_id="old-run",
                model_id=model.id,
                task_id=task.id,
                task_hash="old-hash",
                prompt="Old prompt",
                response="yes",
                score=100,
                status="success",
            )
        )
        session.add(
            TaskChangeEvent(
                task_slug=task.slug,
                change_type="hash_changed",
                old_hash="old-hash",
                new_hash="new-hash",
                requires_rerun=True,
            )
        )
        session.commit()

    with TestClient(app) as client:
        tasks = client.get("/api/tasks").json()
        visible = next(t for t in tasks if t["slug"] == task_slug)
        assert visible["covered_models"] == 0
        assert visible["stale_models"] == 1
        assert visible["pending_models"] == 1
        assert visible["latest_change"]["change_type"] == "hash_changed"
        assert visible["latest_change"]["requires_rerun"] is True

        changes = client.get("/api/tasks/changes").json()
        change = next(c for c in changes if c["task_slug"] == task_slug)
        assert change["old_hash"] == "old-hash"
        assert change["new_hash"] == "new-hash"
        assert change["requires_rerun"] is True
