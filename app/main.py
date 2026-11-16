import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DB_PATH = "mission_control.db"


def create_app(db_path: str | None = None) -> FastAPI:
    """Build the FastAPI app.

    If db_path is None, DATABASE_PATH from the environment is used, falling
    back to "mission_control.db". The database is initialised on startup, not
    at import time, so importing this module never creates a database file.
    """
    if db_path is None:
        db_path = os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.db_path)
        yield

    app = FastAPI(title="Mission Control: Go/No-Go Launch Console", lifespan=lifespan)
    app.state.db_path = db_path

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


# Module-level app so `uvicorn app.main:app` keeps working.
app = create_app()
