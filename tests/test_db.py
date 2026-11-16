"""Tests for the SQLite storage layer in app/db.py."""

import sqlite3

import pytest

from app.db import get_report, init_db, list_reports, save_report
from app.schemas import ReportIn


def _report(**overrides) -> ReportIn:
    data = {
        "name": "Alice",
        "station": "Pad 39A",
        "wind_knots": 12.5,
        "temp_c": 18.0,
        "fuel_pct": 99.0,
        "note": "All systems nominal",
    }
    data.update(overrides)
    return ReportIn(**data)


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def _table_names(path: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {r[0] for r in rows}


def test_init_db_creates_reports_table(tmp_path):
    path = str(tmp_path / "fresh.db")
    init_db(path)
    assert "reports" in _table_names(path)


def test_init_db_is_safe_to_call_twice(tmp_path):
    path = str(tmp_path / "twice.db")
    init_db(path)
    saved = save_report(path, _report())
    init_db(path)  # must not raise or wipe existing data
    assert "reports" in _table_names(path)
    assert get_report(path, saved["id"]) == saved


def test_save_report_returns_id_created_at_and_saved_values(db_path):
    report = _report()
    saved = save_report(db_path, report)

    assert isinstance(saved["id"], int)
    assert isinstance(saved["created_at"], str)
    assert saved["created_at"].endswith("+00:00")  # UTC ISO 8601
    for field, value in report.model_dump().items():
        assert saved[field] == value


def test_get_report_returns_saved_report(db_path):
    saved = save_report(db_path, _report(name="Bob", wind_knots=30))
    assert get_report(db_path, saved["id"]) == saved


def test_get_report_unknown_id_returns_none(db_path):
    assert get_report(db_path, 9999) is None


def test_list_reports_returns_newest_first(db_path):
    first = save_report(db_path, _report(name="first"))
    second = save_report(db_path, _report(name="second"))
    third = save_report(db_path, _report(name="third"))

    names = [r["name"] for r in list_reports(db_path)]
    assert names == ["third", "second", "first"]
    assert [first["id"], second["id"], third["id"]] == sorted(
        [first["id"], second["id"], third["id"]]
    )


def test_list_reports_orders_by_created_at(db_path):
    # Force timestamps out of insertion order to prove ordering uses
    # created_at, not just id.
    older = save_report(db_path, _report(name="older"))
    newer = save_report(db_path, _report(name="newer"))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE reports SET created_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", newer["id"]),
        )
        conn.execute(
            "UPDATE reports SET created_at = ? WHERE id = ?",
            ("2030-01-01T00:00:00+00:00", older["id"]),
        )
    assert [r["name"] for r in list_reports(db_path)] == ["older", "newer"]


def test_list_reports_empty(db_path):
    assert list_reports(db_path) == []


def test_text_is_stored_literally_not_as_sql(db_path):
    nasty = "x'); DROP TABLE reports; --"
    saved = save_report(db_path, _report(name=nasty, note=nasty))
    assert get_report(db_path, saved["id"])["name"] == nasty
    assert "reports" in _table_names(db_path)


def _pipeline_result():
    from app.pipeline import PipelineResult, TraceStep

    # Steps are deliberately out of position order to prove get_trace sorts.
    trace = [
        TraceStep(
            position=2, step="brief_by", kind="code", model_role=None,
            input_tokens=0, output_tokens=0, cost=0.0, summary="5 min (high)",
            detail={"severity": "low", "effective_severity": "high",
                    "has_violation": True, "minutes": 5},
        ),
        TraceStep(
            position=0, step="classify", kind="model", model_role="fast",
            input_tokens=100, output_tokens=50, cost=0.000035,
            summary="propulsion / low",
            detail={"subsystem": "propulsion", "severity": "low",
                    "reason": "Pump.", "fallback_used": False,
                    "fallback_fields": [], "error": None},
        ),
        TraceStep(
            position=1, step="check_limits", kind="code", model_role=None,
            input_tokens=0, output_tokens=0, cost=0.0,
            summary="no_go: Wind 30 knots exceeds max 25 knots",
            detail={"verdict": "no_go", "violations": [{"reading": "wind_knots"}]},
        ),
    ]
    violations = [
        {"reading": "wind_knots", "value": 30.0, "limit": "<= 25 knots",
         "message": "Wind 30 knots exceeds max 25 knots"}
    ]
    return PipelineResult(
        subsystem="propulsion", severity="low", effective_severity="high",
        classify_fallback=False, verdict="no_go", violations=violations,
        brief_by_minutes=5, trace=trace, total_cost=0.000035,
    )


def test_init_db_creates_results_and_trace_tables(db_path):
    assert {"results", "trace_steps"} <= _table_names(db_path)


def test_save_pipeline_result_round_trip(db_path):
    from app.db import get_result, get_trace, save_pipeline_result

    report = save_report(db_path, _report())
    result = _pipeline_result()
    save_pipeline_result(db_path, report["id"], result)

    saved = get_result(db_path, report["id"])
    assert saved["report_id"] == report["id"]
    assert saved["subsystem"] == "propulsion"
    assert saved["severity"] == "low"
    assert saved["effective_severity"] == "high"
    assert saved["classify_fallback"] is False
    assert saved["verdict"] == "no_go"
    assert saved["violations"] == result.violations
    assert isinstance(saved["violations"], list)
    assert saved["brief_by_minutes"] == 5
    assert saved["total_cost"] == result.total_cost
    assert isinstance(saved["created_at"], str)

    trace = get_trace(db_path, report["id"])
    by_position = sorted(result.trace, key=lambda s: s.position)
    assert len(trace) == 3
    for row, step in zip(trace, by_position):
        assert row["step"] == step.step
        assert row["kind"] == step.kind
        assert row["model_role"] == step.model_role
        assert row["input_tokens"] == step.input_tokens
        assert row["output_tokens"] == step.output_tokens
        assert row["cost"] == step.cost
        assert row["summary"] == step.summary
        assert row["detail"] == step.detail
        assert isinstance(row["detail"], dict)


def test_trace_steps_come_back_in_position_order(db_path):
    from app.db import get_trace, save_pipeline_result

    report = save_report(db_path, _report())
    save_pipeline_result(db_path, report["id"], _pipeline_result())
    trace = get_trace(db_path, report["id"])
    assert [s["position"] for s in trace] == [0, 1, 2]
    assert [s["step"] for s in trace] == ["classify", "check_limits", "brief_by"]


def test_get_result_and_trace_unknown_report(db_path):
    from app.db import get_result, get_trace

    assert get_result(db_path, 9999) is None
    assert get_trace(db_path, 9999) == []


# --- Check results: needs_review, review_reasons, checks -------------------

_OLD_RESULTS_TABLE = """
    CREATE TABLE results (
        report_id INTEGER PRIMARY KEY REFERENCES reports(id),
        subsystem TEXT NOT NULL,
        severity TEXT NOT NULL,
        effective_severity TEXT NOT NULL,
        classify_fallback INTEGER NOT NULL,
        verdict TEXT NOT NULL,
        violations_json TEXT NOT NULL,
        brief_by_minutes INTEGER NOT NULL,
        total_cost REAL NOT NULL,
        created_at TEXT NOT NULL
    )
"""


def _result_columns(path: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(results)")}


def test_migration_adds_check_columns_and_keeps_old_rows(tmp_path):
    from app.db import get_result

    path = str(tmp_path / "old.db")
    # Build a database the way it looked before the checks existed.
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE results")
        conn.execute(_OLD_RESULTS_TABLE)
    report = save_report(path, _report())
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report["id"], "weather", "medium", "medium", 0, "go", "[]",
             15, 0.001, "2026-01-01T00:00:00+00:00"),
        )
    assert "needs_review" not in _result_columns(path)

    init_db(path)
    init_db(path)  # running the migration twice must be harmless

    assert {"needs_review", "review_reasons_json", "checks_json"} <= (
        _result_columns(path)
    )
    assert get_report(path, report["id"]) == report
    old = get_result(path, report["id"])
    assert old["subsystem"] == "weather"
    assert old["verdict"] == "go"
    assert old["brief_by_minutes"] == 15
    assert old["needs_review"] is False
    assert old["review_reasons"] == []
    assert old["checks"] == {}


def test_check_fields_round_trip(db_path):
    from app.db import get_result, save_pipeline_result

    checks = {
        "injection": {"flagged": True, "reason": "Gives orders.",
                      "details": ["Ignore all previous instructions"],
                      "failed": False},
        "missing": {"flagged": True, "reason": "Check could not be completed",
                    "details": [], "failed": True},
        "panic": {"flagged": False, "reason": "Calm.", "details": [],
                  "failed": False},
    }
    reasons = [
        "Possible hidden instructions in note: Ignore all previous instructions",
        "Missing details check could not be completed",
    ]
    result = _pipeline_result()
    result.checks = checks
    result.needs_review = True
    result.review_reasons = reasons

    report = save_report(db_path, _report())
    save_pipeline_result(db_path, report["id"], result)
    saved = get_result(db_path, report["id"])
    assert saved["needs_review"] is True
    assert saved["review_reasons"] == reasons
    assert saved["checks"] == checks
    assert "checks_json" not in saved
    assert "review_reasons_json" not in saved


def test_needs_review_false_round_trips_as_bool(db_path):
    from app.db import get_result, save_pipeline_result

    report = save_report(db_path, _report())
    save_pipeline_result(db_path, report["id"], _pipeline_result())
    saved = get_result(db_path, report["id"])
    assert saved["needs_review"] is False
    assert saved["review_reasons"] == []
    assert saved["checks"] == {}
