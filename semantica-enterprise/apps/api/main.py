from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from apps.api.routes import router
from apps.api.agent_internal import router as agent_internal_router
from apps.api.conversations import router as conversation_router
from packages.platform.bootstrap import bootstrap
from packages.platform.config import get_settings
from packages.platform.database import SessionLocal, init_db
from packages.platform.storage import object_storage


settings = get_settings()
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        bootstrap(db)
    object_storage.ensure_bucket()
    yield


app = FastAPI(title=settings.app_name, version="0.10.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.allowed_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(agent_internal_router, prefix=settings.api_prefix)
app.include_router(conversation_router, prefix=settings.api_prefix)


@app.get("/health/live")
def live():
    return {"status": "ok", "version": "0.10.0"}


@app.get("/health/ready")
def ready():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    object_storage.ensure_bucket()
    return {"status": "ready"}


if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    return FileResponse(static_dir / "index.html")
