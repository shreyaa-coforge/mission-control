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
