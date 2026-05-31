from collections import defaultdict
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.db import get_session
from backend.app.models import LLMModel, Provider, Task, TaskResult

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

@router.get("")
def leaderboard(session: Session = Depends(get_session)):
    tasks = session.query(Task).filter(Task.active.is_(True)).all()
    dims = sorted({t.dimension for t in tasks})
    task_by_id={t.id:t for t in tasks}
    rows=[]
    for model in session.query(LLMModel).filter(LLMModel.enabled.is_(True)).all():
        provider=session.get(Provider, model.provider_id)
        results=session.query(TaskResult).filter(TaskResult.model_id==model.id, TaskResult.status=="success").all()
        by_dim=defaultdict(list)
        current=0
        for r in results:
            t=task_by_id.get(r.task_id)
            if t and r.task_hash == t.content_hash and r.score is not None:
                current += 1
                by_dim[t.dimension].append(r.score)
        dim_scores={d: round(sum(v)/len(v),2) if v else None for d,v in by_dim.items()}
        all_scores=[s for vals in by_dim.values() for s in vals]
        rows.append({"model_id": model.id, "model": model.display_name, "provider": provider.name if provider else "", "overall": round(sum(all_scores)/len(all_scores),2) if all_scores else None, "dimensions": dim_scores, "coverage": {"current": current, "total": len(tasks), "status": "complete" if current==len(tasks) and tasks else "partial"}})
    return {"dimensions": dims, "rows": rows}
