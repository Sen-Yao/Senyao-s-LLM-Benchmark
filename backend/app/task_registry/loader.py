import hashlib
import json
from pathlib import Path
from typing import Any
import yaml
from sqlalchemy.orm import Session
from backend.app.models import Task, TaskChangeEvent


def stable_task_hash(payload: dict[str, Any]) -> str:
    relevant = {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "description": payload.get("description"),
        "prompt_template": payload.get("prompt_template"),
        "evaluation": payload.get("evaluation"),
    }
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_yaml_task(path: Path, base_dir: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    rel = path.relative_to(base_dir)
    category = rel.parts[0] if len(rel.parts) > 1 else "unclassified"
    evaluation = raw.get("evaluation") or {}
    return {
        "slug": raw["id"],
        "title": raw.get("name", raw["id"]),
        "category": category,
        "dimension": raw.get("dimension") or category,
        "description": raw.get("description", ""),
        "prompt": raw.get("prompt_template", ""),
        "evaluator_type": evaluation.get("method", "llm_eval"),
        "evaluator_config_json": json.dumps(evaluation, ensure_ascii=False, sort_keys=True),
        "content_hash": stable_task_hash(raw),
        "source_path": str(rel),
    }


def sync_tasks_from_dir(session: Session, tasks_dir: Path) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
    seen: set[str] = set()
    for path in sorted(tasks_dir.rglob("*.yaml")):
        data = load_yaml_task(path, tasks_dir)
        seen.add(data["slug"])
        task = session.query(Task).filter(Task.slug == data["slug"]).one_or_none()
        if task is None:
            session.add(Task(**data, active=True))
            session.add(TaskChangeEvent(task_slug=data["slug"], change_type="created", new_hash=data["content_hash"], requires_rerun=True))
            stats["created"] += 1
            continue
        if task.content_hash != data["content_hash"]:
            old_hash = task.content_hash
            for key, value in data.items():
                setattr(task, key, value)
            task.active = True
            session.add(TaskChangeEvent(task_slug=task.slug, change_type="hash_changed", old_hash=old_hash, new_hash=task.content_hash, requires_rerun=True))
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
