import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.leaderboard import leaderboard
from backend.app.api.providers import out as provider_out
from backend.app.api.runs import _progress_for, run_detail
from backend.app.db import get_session
from backend.app.models import BenchmarkRun, LLMModel, Provider, Task, TaskResult
from backend.app.schemas import ProviderIn
from backend.app.security.crypto import decrypt_secret, encrypt_secret, fingerprint_secret
from backend.app.services.benchmark import run_model_tasks

router = APIRouter(prefix="/smoke", tags=["smoke"])

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
]


class SmokeModelIn(BaseModel):
    display_name: str
    model_id: str


class LiveSmokeRequest(BaseModel):
    provider: ProviderIn
    model: SmokeModelIn
    task_slugs: list[str]
    judge_profile_id: int | None = None


def redact_text(value: str, explicit_secrets: list[str] | None = None) -> str:
    text = value or ""
    for secret in explicit_secrets or []:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).split(m.group(1))[0] + m.group(1) + "=[REDACTED]", text)
    return text


def classify_failure(error: str) -> str:
    lowered = error.lower()
    if "evaluator" in lowered or "judge" in lowered or "exact_match" in lowered or "evaluation" in lowered:
        return "evaluator_failed"
    if "http" in lowered or "provider" in lowered or "timeout" in lowered or "connect" in lowered:
        return "call_failed"
    return "unknown_failed"


def smoke_summary(run_id: str, session: Session) -> dict:
    run = session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    model = session.get(LLMModel, run.model_id)
    provider = session.get(Provider, model.provider_id) if model else None
    explicit_secrets: list[str] = []
    if provider:
        explicit_secrets.append(provider.api_key_fingerprint)
        try:
            explicit_secrets.append(decrypt_secret(provider.encrypted_api_key))
        except Exception:
            pass
    results = session.query(TaskResult).filter(TaskResult.run_id == run_id).order_by(TaskResult.id.asc()).all()
    failures = []
    failure_types: dict[str, int] = {}
    success_count = 0
    for result in results:
        task = session.get(Task, result.task_id)
        if result.status == "success":
            success_count += 1
            continue
        if result.status == "failed":
            failure_type = classify_failure(result.error)
            failure_types[failure_type] = failure_types.get(failure_type, 0) + 1
            failures.append(
                {
                    "task_id": result.task_id,
                    "task_slug": task.slug if task else "",
                    "failure_type": failure_type,
                    "error": redact_text(result.error, explicit_secrets),
                }
            )
    return {
        "run_id": run.run_id,
        "status": run.status,
        "model": model.display_name if model else "",
        "provider": provider.name if provider else "",
        "progress": _progress_for(session, run),
        "success_count": success_count,
        "failure_count": len(failures),
        "failure_types": failure_types,
        "failures": failures,
    }


@router.get("/runs/{run_id}")
def get_smoke_summary(run_id: str, session: Session = Depends(get_session)):
    return smoke_summary(run_id, session)


@router.post("/live")
async def run_live_smoke(payload: LiveSmokeRequest, session: Session = Depends(get_session)):
    existing_slugs = {
        slug
        for (slug,) in session.query(Task.slug).filter(Task.slug.in_(payload.task_slugs), Task.active.is_(True)).all()
    }
    missing_slugs = sorted(set(payload.task_slugs) - existing_slugs)
    if missing_slugs:
        raise HTTPException(status_code=400, detail=f"unknown task slugs: {', '.join(missing_slugs)}")

    provider = session.query(Provider).filter(Provider.name == payload.provider.name).one_or_none()
    if provider is None:
        provider = Provider(
            name=payload.provider.name,
            api_base=payload.provider.api_base,
            encrypted_api_key=encrypt_secret(payload.provider.api_key),
            api_key_fingerprint=fingerprint_secret(payload.provider.api_key),
            enabled=payload.provider.enabled,
            notes=payload.provider.notes,
        )
        session.add(provider)
        session.flush()
    else:
        provider.api_base = payload.provider.api_base
        provider.encrypted_api_key = encrypt_secret(payload.provider.api_key)
        provider.api_key_fingerprint = fingerprint_secret(payload.provider.api_key)
        provider.enabled = payload.provider.enabled
        provider.notes = payload.provider.notes
        session.flush()

    model = (
        session.query(LLMModel)
        .filter(LLMModel.provider_id == provider.id, LLMModel.model_id == payload.model.model_id)
        .one_or_none()
    )
    if model is None:
        model = LLMModel(
            provider_id=provider.id,
            display_name=payload.model.display_name,
            model_id=payload.model.model_id,
            enabled=True,
        )
        session.add(model)
        session.flush()
    else:
        model.display_name = payload.model.display_name
        model.enabled = True
        session.flush()
    session.commit()

    run = await run_model_tasks(
        session,
        model.id,
        task_slugs=payload.task_slugs,
        judge_profile_id=payload.judge_profile_id,
        suite="live-smoke",
    )
    board = leaderboard(session)
    row = next((item for item in board["rows"] if item["model_id"] == model.id), None)
    return {
        "provider": provider_out(provider).model_dump(),
        "model": {"id": model.id, "display_name": model.display_name, "model_id": model.model_id},
        "run": run_detail(run.run_id, session),
        "summary": smoke_summary(run.run_id, session),
        "leaderboard_row": row,
    }
