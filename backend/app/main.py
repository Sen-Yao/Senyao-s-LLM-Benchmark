from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .config import get_settings
from .db import init_db
from .api.providers import router as providers_router
from .api.models import router as models_router
from .api.judges import router as judges_router
from .api.tasks import router as tasks_router
from .api.leaderboard import router as leaderboard_router
from .api.runs import router as runs_router

settings=get_settings()
app=FastAPI(title=settings.app_name)
app.add_middleware(CORSMiddleware, allow_origins=["*"] if settings.cors_origins=="*" else settings.cors_origins.split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    init_db()

@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name}

for router in [providers_router, models_router, judges_router, tasks_router, leaderboard_router, runs_router]:
    app.include_router(router, prefix="/api")

frontend_dist=Path("frontend/dist")
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist/"assets"), name="assets")
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        index=frontend_dist/"index.html"
        return FileResponse(index)
