import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import (
    AlreadyApproved,
    BriefingNotFound,
    ReviewRequired,
    approve_briefing,
    get_briefing,
    get_report,
    get_result,
    get_trace,
    init_db,
    list_reports_with_results,
    save_pipeline_result,
    save_report,
)
from app.model_client import ModelClient, get_model_client
from app.pipeline import run_pipeline
from app.schemas import ApproveRequest, ReportIn, format_validation_errors

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DB_PATH = "mission_control.db"

INVALID_JSON_DETAILS = [
    {"field": "body", "message": "request body must be valid JSON"}
]
INVALID_JSON_BODY = {"error": "Invalid report", "details": INVALID_JSON_DETAILS}

REVIEW_REQUIRED_MESSAGE = (
    "This report was flagged for review. Confirm you reviewed it by sending "
    "reviewed: true."
)


def create_app(
    db_path: str | None = None, model_client: ModelClient | None = None
) -> FastAPI:
    """Build the FastAPI app.

    If db_path is None, DATABASE_PATH from the environment is used, falling
    back to "mission_control.db". The database is initialised on startup, not
    at import time, so importing this module never creates a database file.

    If model_client is None, get_model_client() builds one from the
    environment (MODEL_PROVIDER). That happens on startup rather than here,
    so importing this module (``app = create_app()`` below) never reads
    credentials or fails because of a missing API key.
    """
    if db_path is None:
        db_path = os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.db_path)
        if app.state.model_client is None:
            app.state.model_client = get_model_client()
        yield

    app = FastAPI(title="Mission Control: Go/No-Go Launch Console", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.model_client = model_client

    def _report_view(report_id: int) -> dict | None:
        report = get_report(app.state.db_path, report_id)
        if report is None:
            return None
        return {
            "report": report,
            "result": get_result(app.state.db_path, report_id),
            "trace": get_trace(app.state.db_path, report_id),
            "briefing": get_briefing(app.state.db_path, report_id),
        }

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # A body that is not JSON at all gets one fixed, plain message rather
        # than whatever partial errors the parser produced.
        error = (
            "Invalid approval"
            if request.url.path.startswith("/briefings/")
            else "Invalid report"
        )
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return JSONResponse(
                status_code=422,
                content={"error": error, "details": INVALID_JSON_DETAILS},
            )
        return JSONResponse(
            status_code=422, content=format_validation_errors(exc, error)
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/file", include_in_schema=False)
    def file_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "file.html")

    @app.post("/reports", status_code=201)
    async def create_report(report: ReportIn) -> dict:
        # Save first: the report must be kept even if the pipeline fails.
        saved = save_report(app.state.db_path, report)
        try:
            # The pipeline computes the verdict in plain Python (app/rules.py)
            # and only uses the model to classify. It never approves anything.
            result = await run_pipeline(app.state.model_client, saved)
            save_pipeline_result(app.state.db_path, saved["id"], result)
        except Exception:
            logger.exception("Pipeline failed for report %s", saved["id"])
            return {
                "report": saved,
                "result": None,
                "trace": [],
                "briefing": None,
                "error": "The report was saved, but the pipeline failed. "
                "See the server log for details.",
            }
        return _report_view(saved["id"])

    @app.get("/reports")
    def get_reports() -> dict:
        return {"reports": list_reports_with_results(app.state.db_path)}

    @app.get("/reports/{report_id}")
    def get_one_report(report_id: int):
        view = _report_view(report_id)
        if view is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Report not found", "report_id": report_id},
            )
        return view

    # This is the ONLY code path that approves a briefing. It is triggered by
    # a person in the console; the pipeline and the reviewer model never call
    # it. It only changes the briefing, never the verdict or the result.
    @app.post("/briefings/{report_id}/approve")
    def approve(report_id: int, body: ApproveRequest):
        try:
            approve_briefing(
                app.state.db_path, report_id, body.approved_by, body.reviewed
            )
        except BriefingNotFound:
            return JSONResponse(
                status_code=404,
                content={"error": "Briefing not found", "report_id": report_id},
            )
        except AlreadyApproved:
            return JSONResponse(
                status_code=409,
                content={"error": "Briefing is already approved"},
            )
        except ReviewRequired:
            result = get_result(app.state.db_path, report_id)
            return JSONResponse(
                status_code=409,
                content={
                    "error": REVIEW_REQUIRED_MESSAGE,
                    "review_reasons": result["review_reasons"] if result else [],
                },
            )
        return get_briefing(app.state.db_path, report_id)

    return app


# Module-level app so `uvicorn app.main:app` keeps working.
app = create_app()
