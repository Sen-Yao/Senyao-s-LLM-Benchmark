from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import JudgeProfile, Provider
from backend.app.schemas import JudgeProfileIn, JudgeProfilePatch

router = APIRouter(prefix="/judges", tags=["judges"])


def out(j: JudgeProfile, provider: Provider | None = None) -> dict:
    return {
        "id": j.id,
        "provider_id": j.provider_id,
        "provider_name": provider.name if provider else "",
        "name": j.name,
        "model_id": j.model_id,
        "temperature": j.temperature,
        "enabled": j.enabled,
    }


@router.get("")
def list_judges(session: Session = Depends(get_session)):
    return [out(j, session.get(Provider, j.provider_id)) for j in session.query(JudgeProfile).all()]


@router.post("")
def create_judge(payload: JudgeProfileIn, session: Session = Depends(get_session)):
    row = JudgeProfile(**payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return out(row, session.get(Provider, row.provider_id))


@router.patch("/{judge_id}")
def patch_judge(judge_id: int, payload: JudgeProfilePatch, session: Session = Depends(get_session)):
    row = session.get(JudgeProfile, judge_id)
    if not row:
        raise HTTPException(404, "judge not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    session.commit()
    session.refresh(row)
    return out(row, session.get(Provider, row.provider_id))


@router.delete("/{judge_id}")
def delete_judge(judge_id: int, session: Session = Depends(get_session)):
    row = session.get(JudgeProfile, judge_id)
    if not row:
        raise HTTPException(404, "judge not found")
    session.delete(row)
    session.commit()
    return {"ok": True}
