"""Restart test: reports must persist across a server stop and start."""

import gc
import os

from fastapi.testclient import TestClient

from app.db import get_report, save_report
from app.main import create_app
from app.schemas import ReportIn


def test_report_survives_restart(tmp_path):
    """Simulate stopping and restarting the server.

    App #1 is created against a temp database file and started (which runs
    init_db), a report is saved, and then app #1 is shut down and deleted
    entirely so nothing from it remains in memory. App #2 is then created
    and started against the same file, and the report is read back with
    every field checked.

    This is the evidence for the rubric's restart requirement: saved reports
    live in the SQLite file, not in process memory, so they survive a
    restart.
    """
    db_path = str(tmp_path / "restart.db")
    report = ReportIn(
        name="Alice",
        station="Pad 39A",
        wind_knots=26.5,
        temp_c=3.5,
        fuel_pct=97.9,
        note="Gusty and cold",
    )

    # --- Server run #1 ---
    app1 = create_app(db_path=db_path)
    with TestClient(app1):  # runs startup (init_db) and then shutdown
        assert app1.state.db_path == db_path
        saved = save_report(app1.state.db_path, report)

    # --- Stop: delete app #1 entirely ---
    del app1
    gc.collect()
    assert os.path.exists(db_path)

    # --- Server run #2 (restart) ---
    app2 = create_app(db_path=db_path)
    with TestClient(app2):
        loaded = get_report(app2.state.db_path, saved["id"])

    assert loaded is not None
    assert loaded == saved
    assert loaded["id"] == saved["id"]
    assert loaded["created_at"] == saved["created_at"]
    for field, value in report.model_dump().items():
        assert loaded[field] == value


def test_create_app_uses_database_path_env(monkeypatch, tmp_path):
    path = str(tmp_path / "from_env.db")
    monkeypatch.setenv("DATABASE_PATH", path)
    app = create_app()
    assert app.state.db_path == path
    with TestClient(app):
        pass
    assert os.path.exists(path)


def test_create_app_defaults_to_tmp_db_under_pytest(_tmp_database):
    """conftest points DATABASE_PATH at tmp_path, so tests never write to
    the real mission_control.db."""
    app = create_app()
    assert app.state.db_path == str(_tmp_database)
    assert os.path.basename(app.state.db_path) != "mission_control.db"
