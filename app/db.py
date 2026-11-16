"""SQLite storage for launch reports, using only Python's built-in sqlite3.

Every function opens its own connection and closes it before returning, so no
state is held in memory between calls. Data only lives in the database file,
which is what lets a report survive a server restart.

All queries use ? placeholders. Never build SQL with string formatting.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from app.schemas import ReportIn

_REPORT_COLUMNS = (
    "id, name, station, wind_knots, temp_c, fuel_pct, note, created_at"
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Create the tables if they do not exist. Safe to call more than once."""
    # Later branches will add the results, briefings, and trace_steps tables
    # here, alongside reports.
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
