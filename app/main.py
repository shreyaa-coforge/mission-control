import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_report, init_db, list_reports, save_report
from app.schemas import ReportIn, format_validation_errors

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DB_PATH = "mission_control.db"

INVALID_JSON_BODY = {
    "error": "Invalid report",
    "details": [{"field": "body", "message": "request body must be valid JSON"}],
}


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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # A body that is not JSON at all gets one fixed, plain message rather
        # than whatever partial errors the parser produced.
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return JSONResponse(status_code=422, content=INVALID_JSON_BODY)
        return JSONResponse(status_code=422, content=format_validation_errors(exc))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/reports", status_code=201)
    def create_report(report: ReportIn) -> dict:
        saved = save_report(app.state.db_path, report)
        # The pipeline (rules verdict, model briefing) will run here in a
        # later branch. It must never approve a briefing or change the verdict.
        return saved

    @app.get("/reports")
    def get_reports() -> dict:
        return {"reports": list_reports(app.state.db_path)}

    @app.get("/reports/{report_id}")
    def get_one_report(report_id: int):
        report = get_report(app.state.db_path, report_id)
        if report is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Report not found", "report_id": report_id},
            )
        return report

    return app


# Module-level app so `uvicorn app.main:app` keeps working.
app = create_app()
