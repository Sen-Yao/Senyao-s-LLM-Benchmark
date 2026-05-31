from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import JudgeProfile
from backend.app.schemas import JudgeProfileIn

router = APIRouter(prefix="/judges", tags=["judges"])


@router.get("")
def list_judges(session: Session = Depends(get_session)):
    return [
        {
            "id": j.id,
            "provider_id": j.provider_id,
            "name": j.name,
            "model_id": j.model_id,
            "temperature": j.temperature,
            "enabled": j.enabled,
        }
        for j in session.query(JudgeProfile).all()
    ]


@router.post("")
def create_judge(payload: JudgeProfileIn, session: Session = Depends(get_session)):
    row = JudgeProfile(**payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id}
