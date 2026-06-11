from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db import get_session
from backend.app.models import AppSetting, JudgeProfile, Provider
from backend.app.schemas import AppSettingsPatch

router = APIRouter(prefix="/settings", tags=["settings"])
DEFAULT_JUDGE_PROFILE_ID_KEY = "default_judge_profile_id"


def _setting_value(session: Session, key: str) -> str:
    row = session.get(AppSetting, key)
    return row.value if row else ""


def _set_setting_value(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=key, value=value))


def _parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def get_default_judge_profile_id(session: Session) -> int | None:
    judge_id = _parse_int(_setting_value(session, DEFAULT_JUDGE_PROFILE_ID_KEY))
    if judge_id is None:
        return None
    judge = session.get(JudgeProfile, judge_id)
    if not judge or not judge.enabled:
        return None
    return judge.id


def resolve_judge_profile_id(session: Session, judge_profile_id: int | None) -> int | None:
    if judge_profile_id is not None:
        return judge_profile_id
    return get_default_judge_profile_id(session)


def _judge_out(session: Session, judge_id: int | None) -> dict | None:
    if judge_id is None:
        return None
    judge = session.get(JudgeProfile, judge_id)
    if not judge:
        return None
    provider = session.get(Provider, judge.provider_id)
    return {
        "id": judge.id,
        "provider_id": judge.provider_id,
        "provider_name": provider.name if provider else "",
        "name": judge.name,
        "model_id": judge.model_id,
        "temperature": judge.temperature,
        "enabled": judge.enabled,
    }


def _settings_out(session: Session) -> dict:
    default_id = get_default_judge_profile_id(session)
    return {
        "default_judge_profile_id": default_id,
        "default_judge": _judge_out(session, default_id),
    }


@router.get("")
def get_app_settings(session: Session = Depends(get_session)):
    return _settings_out(session)


@router.patch("")
def patch_app_settings(payload: AppSettingsPatch, session: Session = Depends(get_session)):
    if payload.default_judge_profile_id is not None:
        judge = session.get(JudgeProfile, payload.default_judge_profile_id)
        if not judge:
            raise HTTPException(status_code=404, detail="judge not found")
        if not judge.enabled:
            raise HTTPException(status_code=400, detail="judge is disabled")
        _set_setting_value(session, DEFAULT_JUDGE_PROFILE_ID_KEY, str(judge.id))
    else:
        _set_setting_value(session, DEFAULT_JUDGE_PROFILE_ID_KEY, "")
    session.commit()
    return _settings_out(session)
