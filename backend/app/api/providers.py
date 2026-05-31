from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import Provider
from backend.app.schemas import ProviderIn, ProviderOut, ProviderPatch
from backend.app.security.crypto import encrypt_secret, fingerprint_secret

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


@router.delete("/{provider_id}")
def delete_provider(provider_id: int, session: Session = Depends(get_session)):
    provider = session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    session.delete(provider)
    session.commit()
    return {"ok": True}
