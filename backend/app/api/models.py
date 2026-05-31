from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import LLMModel
from backend.app.schemas import ModelIn

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models(session: Session = Depends(get_session)):
    rows = session.query(LLMModel).order_by(LLMModel.display_name).all()
    return [
        {
            "id": m.id,
            "provider_id": m.provider_id,
            "display_name": m.display_name,
            "model_id": m.model_id,
            "enabled": m.enabled,
            "context_window": m.context_window,
            "input_price": m.input_price,
            "output_price": m.output_price,
            "notes": m.notes,
        }
        for m in rows
    ]


@router.post("")
def create_model(payload: ModelIn, session: Session = Depends(get_session)):
    model = LLMModel(**payload.model_dump())
    session.add(model)
    session.commit()
    session.refresh(model)
    return {"id": model.id}


@router.delete("/{model_id}")
def delete_model(model_id: int, session: Session = Depends(get_session)):
    model = session.get(LLMModel, model_id)
    if not model:
        raise HTTPException(404, "model not found")
    session.delete(model)
    session.commit()
    return {"ok": True}
