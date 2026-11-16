"""API tests for POST /reports, GET /reports and GET /reports/{id}."""

import gc

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

VALID_REPORT = {
    "name": "Alice",
    "station": "Pad 39A",
    "wind_knots": 12.5,
    "temp_c": 18.0,
    "fuel_pct": 99.0,
    "note": "All nominal",
}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path=db_path)) as c:
        yield c


def _detail_fields(body: dict) -> list[str]:
    return [d["field"] for d in body["details"]]


def _messages(body: dict) -> list[str]:
    return [d["message"] for d in body["details"]]


# --- Successful requests --------------------------------------------------


def test_post_valid_report_returns_201_and_echoes_fields(client):
    resp = client.post("/reports", json=VALID_REPORT)
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert body["created_at"]
    for field, value in VALID_REPORT.items():
        assert body[field] == value


def test_saved_report_appears_in_list_and_detail(client):
    saved = client.post("/reports", json=VALID_REPORT).json()

    listed = client.get("/reports")
    assert listed.status_code == 200
    assert listed.json() == {"reports": [saved]}

    one = client.get(f"/reports/{saved['id']}")
    assert one.status_code == 200
    assert one.json() == saved


def test_list_reports_newest_first(client):
    first = client.post("/reports", json=VALID_REPORT).json()
    second = client.post("/reports", json={**VALID_REPORT, "name": "Bob"}).json()
    ids = [r["id"] for r in client.get("/reports").json()["reports"]]
    assert ids == [second["id"], first["id"]]


def test_get_missing_report_returns_404(client):
    resp = client.get("/reports/99999")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Report not found", "report_id": 99999}


def test_unsafe_but_real_fuel_reading_is_accepted(client):
    # 100.1 is outside the launch limit but is a real reading: it must be
    # stored so the rules engine can flag it, not rejected at the schema.
    resp = client.post("/reports", json={**VALID_REPORT, "fuel_pct": 100.1})
    assert resp.status_code == 201
    assert resp.json()["fuel_pct"] == 100.1


# --- Rejected requests ----------------------------------------------------


def test_missing_field_returns_422_naming_field(client):
    payload = {k: v for k, v in VALID_REPORT.items() if k != "station"}
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "Invalid report"
    assert "station" in _detail_fields(body)
    assert "station is required" in _messages(body)


def test_non_numeric_wind_returns_422(client):
    resp = client.post("/reports", json={**VALID_REPORT, "wind_knots": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "Invalid report"
    assert "wind_knots must be a number" in _messages(body)


def test_blank_name_returns_422(client):
    resp = client.post("/reports", json={**VALID_REPORT, "name": "   "})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "Invalid report"
    assert "name" in _detail_fields(body)


def test_extra_field_returns_422(client):
    resp = client.post("/reports", json={**VALID_REPORT, "approved": True})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "Invalid report"
    assert "approved" in _detail_fields(body)


def test_invalid_json_returns_422_with_body_message(client):
    resp = client.post(
        "/reports",
        content='{"name": "Alice", ',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json() == {
        "error": "Invalid report",
        "details": [{"field": "body", "message": "request body must be valid JSON"}],
    }


def test_rejected_reports_are_not_saved(client):
    client.post("/reports", json={**VALID_REPORT, "wind_knots": "abc"})
    client.post("/reports", json={**VALID_REPORT, "extra": 1})
    client.post(
        "/reports",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert client.get("/reports").json() == {"reports": []}


# --- Restart --------------------------------------------------------------


def test_report_survives_restart_via_api(db_path):
    app1 = create_app(db_path=db_path)
    with TestClient(app1) as client1:
        saved = client1.post("/reports", json=VALID_REPORT).json()
    del app1, client1
    gc.collect()

    app2 = create_app(db_path=db_path)
    with TestClient(app2) as client2:
        resp = client2.get(f"/reports/{saved['id']}")
    assert resp.status_code == 200
    assert resp.json() == saved
