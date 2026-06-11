from uuid import uuid4

from fastapi import BackgroundTasks

from backend.app.db import SessionLocal
from backend.app.models import BenchmarkRun, LLMModel, Provider, Task, TaskChangeEvent, TaskResult
from backend.app.security.crypto import encrypt_secret, fingerprint_secret


def test_task_change_events_and_stale_result_visibility():
    from backend.app.api.tasks import list_task_changes, list_tasks

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

    with SessionLocal() as session:
        tasks = list_tasks(session)
        visible = next(t for t in tasks if t["slug"] == task_slug)
        assert visible["covered_models"] == 0
        assert visible["stale_models"] == 1
        assert visible["pending_models"] == 1
        assert visible["prompt"] == "Answer yes."
        assert visible["evaluator_config"] == {"contains": "yes"}
        assert visible["description"] == ""
        assert visible["source_path"] == "reasoning/visibility-task.yaml"
        assert visible["latest_change"]["change_type"] == "hash_changed"
        assert visible["latest_change"]["requires_rerun"] is True

        changes = list_task_changes(session)
        change = next(c for c in changes if c["task_slug"] == task_slug)
        assert change["old_hash"] == "old-hash"
        assert change["new_hash"] == "new-hash"
        assert change["requires_rerun"] is True


def test_task_changes_exclude_deleted_events_for_nonexistent_tasks():
    from backend.app.api.tasks import list_task_changes

    with SessionLocal() as session:
        session.add(
            TaskChangeEvent(
                task_slug="deleted-visibility-task",
                change_type="deleted",
                old_hash="old",
                new_hash="",
                requires_rerun=False,
            )
        )
        session.commit()

    with SessionLocal() as session:
        changes = list_task_changes(session)
        assert all(c["task_slug"] != "deleted-visibility-task" for c in changes)


def test_rerun_missing_models_only_materializes_non_fresh_task_results(monkeypatch):
    from backend.app.api.tasks import rerun_missing_models_for_task, task_coverage
    from backend.app.schemas import TaskRerunRequest

    suffix = uuid4().hex[:8]
    provider_name = f"rerun-provider-{suffix}"
    task_slug = f"rerun-task-{suffix}"
    scheduled = []

    def fake_add_task(self, func, *args, **kwargs):
        scheduled.append((func, args, kwargs))

    monkeypatch.setattr(BackgroundTasks, "add_task", fake_add_task)

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

        fresh_model = LLMModel(provider_id=provider.id, display_name=f"Fresh {suffix}", model_id=f"fresh-{suffix}")
        stale_model = LLMModel(provider_id=provider.id, display_name=f"Stale {suffix}", model_id=f"stale-{suffix}")
        never_model = LLMModel(provider_id=provider.id, display_name=f"Never {suffix}", model_id=f"never-{suffix}")
        disabled_model = LLMModel(provider_id=provider.id, display_name=f"Disabled {suffix}", model_id=f"disabled-{suffix}", enabled=False)
        task = Task(
            slug=task_slug,
            title="Rerun Task",
            category="reasoning",
            dimension="reasoning",
            prompt="Answer yes.",
            evaluator_type="contains",
            evaluator_config_json='{"contains":"yes"}',
            content_hash="content-new",
            semantic_hash="semantic-new",
            evaluator_version="v2",
            active=True,
        )
        session.add_all([fresh_model, stale_model, never_model, disabled_model, task])
        session.commit()
        for row in [fresh_model, stale_model, never_model, disabled_model, task]:
            session.refresh(row)
        original_hash = task.content_hash
        original_semantic_hash = task.semantic_hash

        session.add_all(
            [
                TaskResult(
                    run_id="fresh-old-run",
                    model_id=fresh_model.id,
                    task_id=task.id,
                    task_hash="content-new",
                    semantic_hash="semantic-new",
                    evaluator_version="v2",
                    prompt="Answer yes.",
                    response="yes",
                    score=100,
                    status="success",
                ),
                TaskResult(
                    run_id="stale-old-run",
                    model_id=stale_model.id,
                    task_id=task.id,
                    task_hash="content-old",
                    semantic_hash="semantic-old",
                    evaluator_version="v1",
                    prompt="Old prompt",
                    response="yes",
                    score=80,
                    status="success",
                ),
                TaskResult(
                    run_id="disabled-old-run",
                    model_id=disabled_model.id,
                    task_id=task.id,
                    task_hash="content-old",
                    semantic_hash="semantic-old",
                    evaluator_version="v1",
                    prompt="Old prompt",
                    response="yes",
                    score=70,
                    status="success",
                ),
            ]
        )
        session.commit()

        before = task_coverage(task_slug, session)
        assert before["covered_model_ids"] == [fresh_model.id]
        assert before["stale_model_ids"] == [stale_model.id]
        assert before["pending_model_ids"] == [stale_model.id, never_model.id]

        result = rerun_missing_models_for_task(
            task_slug,
            TaskRerunRequest(model_ids=[fresh_model.id, stale_model.id, never_model.id, disabled_model.id]),
            BackgroundTasks(),
            session,
        )
        session.refresh(task)
        assert task.content_hash == original_hash
        assert task.semantic_hash == original_semantic_hash

        assert result["status"] == "queued"
        assert result["model_ids"] == [stale_model.id, never_model.id]
        assert len(result["runs"]) == 2
        assert len(scheduled) == 1
        run_ids = [run["run_id"] for run in result["runs"]]
        rows = session.query(TaskResult).filter(TaskResult.run_id.in_(run_ids)).order_by(TaskResult.model_id).all()
        assert [row.model_id for row in rows] == [stale_model.id, never_model.id]
        assert {row.task_id for row in rows} == {task.id}
        assert {row.semantic_hash for row in rows} == {"semantic-new"}
        assert {row.evaluator_version for row in rows} == {"v2"}
        assert session.query(BenchmarkRun).filter(BenchmarkRun.run_id.in_(run_ids)).count() == 2