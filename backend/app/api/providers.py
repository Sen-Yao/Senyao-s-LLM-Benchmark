from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.adapters.openai_compatible import chat_completion, list_models as fetch_model_ids
from backend.app.db import get_session
from backend.app.models import BenchmarkRun, JudgeProfile, LLMModel, Provider, TaskResult
from backend.app.schemas import ProviderIn, ProviderOut, ProviderPatch, ProviderTestRequest
from backend.app.security.crypto import decrypt_secret, encrypt_secret, fingerprint_secret

router = APIRouter(prefix="/providers", tags=["providers"])


def out(provider: Provider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        api_base=provider.api_base,
        api_key_saved=bool(provider.encrypted_api_key),
        api_key_fingerprint=provider.api_key_fingerprint,
        enabled=provider.enabled,
        notes=provider.notes,
    )


@router.get("", response_model=list[ProviderOut])
def list_providers(session: Session = Depends(get_session)):
    return [out(p) for p in session.query(Provider).order_by(Provider.name).all()]


@router.post("", response_model=ProviderOut)
def create_provider(payload: ProviderIn, session: Session = Depends(get_session)):
    provider = Provider(
        name=payload.name,
        api_base=payload.api_base,
        encrypted_api_key=encrypt_secret(payload.api_key),
        api_key_fingerprint=fingerprint_secret(payload.api_key),
        enabled=payload.enabled,
        notes=payload.notes,
    )
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return out(provider)


@router.patch("/{provider_id}", response_model=ProviderOut)
def patch_provider(provider_id: int, payload: ProviderPatch, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data and data["api_key"]:
        provider.encrypted_api_key = encrypt_secret(data.pop("api_key"))
        provider.api_key_fingerprint = fingerprint_secret(payload.api_key or "")
    for key, value in data.items():
        setattr(provider, key, value)
    session.commit()
    session.refresh(provider)
    return out(provider)


@router.get("/{provider_id}/secret")
def reveal_provider_secret(provider_id: int, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    if not provider.encrypted_api_key:
        return {"api_key": ""}
    return {"api_key": decrypt_secret(provider.encrypted_api_key)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: int, payload: ProviderTestRequest, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    model_id = payload.model_id or "gpt-4o-mini"
    try:
        result = await chat_completion(
            provider.api_base,
            decrypt_secret(provider.encrypted_api_key),
            model_id,
            "Reply with exactly: OK",
            timeout=20,
            max_tokens=8,
        )
        return {"ok": True, "latency": result.latency, "response_preview": result.text[:120]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/{provider_id}/models")
async def list_provider_models(provider_id: int, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    try:
        models = await fetch_model_ids(
            provider.api_base,
            decrypt_secret(provider.encrypted_api_key),
            timeout=30,
        )
        if not models and not provider.api_base.rstrip("/").endswith("/v1"):
            models = await fetch_model_ids(
                provider.api_base.rstrip("/") + "/v1",
                decrypt_secret(provider.encrypted_api_key),
                timeout=30,
            )
        return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "models": [], "error": str(exc)}


@router.delete("/{provider_id}")
def delete_provider(provider_id: int, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    model_ids = [mid for (mid,) in session.query(LLMModel.id).filter(LLMModel.provider_id == provider_id).all()]
    if model_ids:
        run_ids = [rid for (rid,) in session.query(BenchmarkRun.run_id).filter(BenchmarkRun.model_id.in_(model_ids)).all()]
        if run_ids:
            session.query(TaskResult).filter(TaskResult.run_id.in_(run_ids)).delete(synchronize_session=False)
        session.query(TaskResult).filter(TaskResult.model_id.in_(model_ids)).delete(synchronize_session=False)
        session.query(BenchmarkRun).filter(BenchmarkRun.model_id.in_(model_ids)).delete(synchronize_session=False)
        session.query(LLMModel).filter(LLMModel.id.in_(model_ids)).delete(synchronize_session=False)
    session.query(JudgeProfile).filter(JudgeProfile.provider_id == provider_id).delete(synchronize_session=False)
    session.delete(provider)
    session.commit()
    return {"ok": True}
