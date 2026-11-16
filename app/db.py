"""SQLite storage for launch reports, using only Python's built-in sqlite3.

Every function opens its own connection and closes it before returning, so no
state is held in memory between calls. Data only lives in the database file,
which is what lets a report survive a server restart.

All queries use ? placeholders. Never build SQL with string formatting.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.schemas import ReportIn

if TYPE_CHECKING:
    from app.pipeline import PipelineResult

_REPORT_COLUMNS = (
    "id, name, station, wind_knots, temp_c, fuel_pct, note, created_at"
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# Columns added to `results` after it was first created. init_db adds any
# that are missing from an older database, keeping existing rows.
_RESULTS_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("needs_review", "INTEGER"),
    ("review_reasons_json", "TEXT"),
    ("checks_json", "TEXT"),
)


def _migrate_results(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(results)")
    }
    for name, col_type in _RESULTS_ADDED_COLUMNS:
        if name not in existing:
            # Names and types come from the constant above, never user input.
            conn.execute(f"ALTER TABLE results ADD COLUMN {name} {col_type}")


def init_db(db_path: str) -> None:
    """Create the tables if they do not exist. Safe to call more than once."""
    # A later branch will add the briefings table here.
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                station TEXT NOT NULL,
                wind_knots REAL NOT NULL,
                temp_c REAL NOT NULL,
                fuel_pct REAL NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL  -- UTC ISO 8601 timestamp
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                report_id INTEGER PRIMARY KEY REFERENCES reports(id),
                subsystem TEXT NOT NULL,
                severity TEXT NOT NULL,
                effective_severity TEXT NOT NULL,
                classify_fallback INTEGER NOT NULL,  -- 0 or 1
                verdict TEXT NOT NULL,
                violations_json TEXT NOT NULL,
                brief_by_minutes INTEGER NOT NULL,
                total_cost REAL NOT NULL,
                created_at TEXT NOT NULL,  -- UTC ISO 8601 timestamp
                needs_review INTEGER,  -- 0 or 1
                review_reasons_json TEXT,
                checks_json TEXT
            )
            """
        )
        _migrate_results(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL REFERENCES reports(id),
                position INTEGER NOT NULL,
                step TEXT NOT NULL,
                kind TEXT NOT NULL,
                model_role TEXT,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost REAL NOT NULL,
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL
            )
            """
        )


def save_report(db_path: str, report: ReportIn) -> dict:
    """Insert a report and return it as a dict, including id and created_at."""
    created_at = datetime.now(timezone.utc).isoformat()
    # `with closing(...)` closes the connection; the inner `with conn`
    # commits the transaction (or rolls it back on error).
    with closing(_connect(db_path)) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO reports
                (name, station, wind_knots, temp_c, fuel_pct, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.name,
                report.station,
                report.wind_knots,
                report.temp_c,
                report.fuel_pct,
                report.note,
                created_at,
            ),
        )
        report_id = cursor.lastrowid
    return {"id": report_id, **report.model_dump(), "created_at": created_at}


def get_report(db_path: str, report_id: int) -> dict | None:
    """Return one report as a dict, or None if no report has that id."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            f"SELECT {_REPORT_COLUMNS} FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_reports(db_path: str) -> list[dict]:
    """Return all reports, newest first."""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            # id breaks ties if two reports share a timestamp.
            f"SELECT {_REPORT_COLUMNS} FROM reports "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def list_reports_with_results(db_path: str) -> list[dict]:
    """Return all reports, newest first, each with its result summary.

    Result fields (subsystem, severity, effective_severity, verdict,
    brief_by_minutes, total_cost, needs_review) are None when a report has
    no result yet.
    """
    report_cols = ", ".join(f"r.{c.strip()}" for c in _REPORT_COLUMNS.split(","))
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT {report_cols},
                   res.subsystem, res.severity, res.effective_severity,
                   res.verdict, res.brief_by_minutes, res.total_cost,
                   res.needs_review
            FROM reports AS r
            LEFT JOIN results AS res ON res.report_id = r.id
            ORDER BY r.created_at DESC, r.id DESC
            """
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        # Stored as 0/1; keep None for reports with no result yet.
        if item["needs_review"] is not None:
            item["needs_review"] = bool(item["needs_review"])
        items.append(item)
    return items


def save_pipeline_result(
    db_path: str, report_id: int, result: PipelineResult
) -> None:
    """Save a pipeline result and all its trace steps in one transaction."""
    created_at = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO results
                (report_id, subsystem, severity, effective_severity,
                 classify_fallback, verdict, violations_json,
                 brief_by_minutes, total_cost, created_at,
                 needs_review, review_reasons_json, checks_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                result.subsystem,
                result.severity,
                result.effective_severity,
                int(result.classify_fallback),
                result.verdict,
                json.dumps(result.violations),
                result.brief_by_minutes,
                result.total_cost,
                created_at,
                int(result.needs_review),
                json.dumps(result.review_reasons),
                json.dumps(result.checks),
            ),
        )
        conn.executemany(
            """
            INSERT INTO trace_steps
                (report_id, position, step, kind, model_role, input_tokens,
                 output_tokens, cost, summary, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    report_id,
                    s.position,
                    s.step,
                    s.kind,
                    s.model_role,
                    s.input_tokens,
                    s.output_tokens,
                    s.cost,
                    s.summary,
                    json.dumps(s.detail),
                )
                for s in result.trace
            ],
        )


def get_result(db_path: str, report_id: int) -> dict | None:
    """Return the pipeline result for a report, or None if there is none."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT report_id, subsystem, severity, effective_severity,
                   classify_fallback, verdict, violations_json,
                   brief_by_minutes, total_cost, created_at,
                   needs_review, review_reasons_json, checks_json
            FROM results WHERE report_id = ?
            """,
            (report_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["classify_fallback"] = bool(data["classify_fallback"])
    data["violations"] = json.loads(data.pop("violations_json"))
    # Rows saved before the checks existed have NULLs in the new columns.
    data["needs_review"] = bool(data["needs_review"])
    reasons_json = data.pop("review_reasons_json")
    data["review_reasons"] = json.loads(reasons_json) if reasons_json else []
    checks_json = data.pop("checks_json")
    data["checks"] = json.loads(checks_json) if checks_json else {}
    return data


def get_trace(db_path: str, report_id: int) -> list[dict]:
    """Return a report's trace steps ordered by position."""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT position, step, kind, model_role, input_tokens,
                   output_tokens, cost, summary, detail_json
            FROM trace_steps WHERE report_id = ?
            ORDER BY position, id
            """,
            (report_id,),
        ).fetchall()
    steps = []
    for row in rows:
        data = dict(row)
        data["detail"] = json.loads(data.pop("detail_json"))
        steps.append(data)
    return steps
