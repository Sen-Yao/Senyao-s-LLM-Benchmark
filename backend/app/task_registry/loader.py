import hashlib
import json
import logging
from pathlib import Path
from typing import Any
import yaml
from sqlalchemy.orm import Session
from backend.app.models import Task, TaskChangeEvent


logger = logging.getLogger(__name__)
SEMANTIC_IGNORED_EVALUATION_KEYS = {"method", "evaluator_version", "schema_version", "metadata"}


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def raw_config_hash(payload: dict[str, Any]) -> str:
    return _canonical_json_hash(payload)


def _semantic_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in evaluation.items()
        if key not in SEMANTIC_IGNORED_EVALUATION_KEYS
    }


def stable_task_hash(payload: dict[str, Any]) -> str:
    evaluation = payload.get("evaluation") or {}
    relevant = {
        "id": payload.get("id"),
        "dimension": payload.get("dimension"),
        "type": payload.get("type"),
        "prompt_template": payload.get("prompt_template"),
        "agent": payload.get("agent"),
        "tools": payload.get("tools"),
        "fixtures": payload.get("fixtures"),
        "expected_trace": payload.get("expected_trace"),
        "evaluation": _semantic_evaluation(evaluation),
    }
    for key in [
        "allowed_actions",
        "required_checkpoints",
        "forbidden_actions",
        "command_policy",
        "budget",
    ]:
        if key in payload and payload[key] is not None:
            relevant[key] = payload[key]
    if payload.get("state_machine") is not None:
        relevant["state_machine"] = payload.get("state_machine")
    return _canonical_json_hash(relevant)


def evaluator_version(payload: dict[str, Any]) -> str:
    evaluation = payload.get("evaluation") or {}
    return str(evaluation.get("evaluator_version") or "v1")


def load_yaml_task(path: Path, base_dir: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"task yaml root must be a mapping: {path}")
    rel = path.relative_to(base_dir)
    category = rel.parts[0] if len(rel.parts) > 1 else "unclassified"
    evaluation = raw.get("evaluation") or {}
    task_type = raw.get("type", "llm_judged")
    evaluator_type = evaluation.get("method", "llm_eval")
    semantic_hash = stable_task_hash(raw)
    if task_type == "agent_tool" and evaluator_type == "llm_eval":
        evaluator_type = "agent_trace_eval"
    return {
        "slug": raw["id"],
        "title": raw.get("name", raw["id"]),
        "category": category,
        "dimension": raw.get("dimension") or category,
        "task_type": task_type,
        "description": raw.get("description", ""),
        "short_description": raw.get("short_description") or raw.get("summary") or raw.get("description", ""),
        "prompt": raw.get("prompt_template", ""),
        "evaluator_type": evaluator_type,
        "evaluator_config_json": json.dumps(evaluation, ensure_ascii=False, sort_keys=True),
        "config_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
        "content_hash": semantic_hash,
        "semantic_hash": semantic_hash,
        "raw_config_hash": raw_config_hash(raw),
        "evaluator_version": evaluator_version(raw),
        "source_path": str(rel),
    }


def sync_tasks_from_dir(session: Session, tasks_dir: Path) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    seen: set[str] = set()
    for path in sorted(tasks_dir.rglob("*.yaml")):
        try:
            data = load_yaml_task(path, tasks_dir)
        except ValueError as exc:
            logger.warning("Skipping invalid task file: %s", exc)
            continue
        seen.add(data["slug"])
        task = session.query(Task).filter(Task.slug == data["slug"]).one_or_none()
        if task is None:
            session.add(Task(**data, active=True))
            session.add(TaskChangeEvent(task_slug=data["slug"], change_type="created", new_hash=data["content_hash"], requires_rerun=True))
            stats["created"] += 1
            continue
        current_semantic_hash = task.semantic_hash or task.content_hash
        current_evaluator_version = task.evaluator_version or "v1"
        semantic_changed = current_semantic_hash != data["semantic_hash"]
        evaluator_changed = current_evaluator_version != data["evaluator_version"]
        raw_changed = task.raw_config_hash != data["raw_config_hash"]
        content_changed = task.content_hash != data["content_hash"]
        if semantic_changed or evaluator_changed or raw_changed or content_changed:
            old_hash = task.content_hash
            old_semantic_hash = current_semantic_hash
            for key, value in data.items():
                setattr(task, key, value)
            task.active = True
            requires_rerun = semantic_changed or evaluator_changed
            if requires_rerun:
                change_type = "evaluator_changed" if evaluator_changed and not semantic_changed else "semantic_hash_changed"
            else:
                change_type = "schema_changed"
            session.add(
                TaskChangeEvent(
                    task_slug=task.slug,
                    change_type=change_type,
                    old_hash=old_semantic_hash if requires_rerun else old_hash,
                    new_hash=task.semantic_hash if requires_rerun else task.content_hash,
                    requires_rerun=requires_rerun,
                )
            )
            stats["updated"] += 1
        else:
            task.active = True
            stats["unchanged"] += 1
    for task in session.query(Task).filter(Task.active.is_(True)).all():
        if task.slug not in seen:
            task.active = False
            session.add(TaskChangeEvent(task_slug=task.slug, change_type="deleted", old_hash=task.content_hash, requires_rerun=False))
            stats["deactivated"] += 1
    session.commit()
    return stats
