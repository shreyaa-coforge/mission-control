"""API tests for POST /reports, GET /reports and GET /reports/{id}."""

import gc
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_client import FakeModel, ModelError

VALID_REPORT = {
    "name": "Alice",
    "station": "Pad 39A",
    "wind_knots": 12.5,
    "temp_c": 18.0,
    "fuel_pct": 99.0,
    "note": "All nominal",
}


CLASSIFY_REPLY = json.dumps(
    {"subsystem": "avionics", "severity": "low", "reason": "Minor sensor noise."}
)

REPORT_FIELDS = ("id", "name", "station", "wind_knots", "temp_c", "fuel_pct",
                 "note", "created_at")
RESULT_SUMMARY_FIELDS = ("subsystem", "severity", "effective_severity",
                         "verdict", "brief_by_minutes", "total_cost")


def fake_model() -> FakeModel:
    return FakeModel(script={"classify": CLASSIFY_REPLY})


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path=db_path, model_client=fake_model())) as c:
        yield c


def _detail_fields(body: dict) -> list[str]:
    return [d["field"] for d in body["details"]]


def _messages(body: dict) -> list[str]:
    return [d["message"] for d in body["details"]]


# --- Successful requests --------------------------------------------------


def test_post_valid_report_returns_201_and_echoes_fields(client):
    resp = client.post("/reports", json=VALID_REPORT)
    assert resp.status_code == 201
    body = resp.json()["report"]
    assert isinstance(body["id"], int)
    assert body["created_at"]
    for field, value in VALID_REPORT.items():
        assert body[field] == value


def test_saved_report_appears_in_list_and_detail(client):
    posted = client.post("/reports", json=VALID_REPORT).json()
    saved = posted["report"]

    listed = client.get("/reports")
    assert listed.status_code == 200
    items = listed.json()["reports"]
    assert len(items) == 1
    assert {k: items[0][k] for k in REPORT_FIELDS} == saved

    one = client.get(f"/reports/{saved['id']}")
    assert one.status_code == 200
    assert one.json()["report"] == saved


def test_list_reports_newest_first(client):
    first = client.post("/reports", json=VALID_REPORT).json()["report"]
    second = client.post("/reports", json={**VALID_REPORT, "name": "Bob"}).json()[
        "report"
    ]
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
    assert resp.json()["report"]["fuel_pct"] == 100.1


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
    app1 = create_app(db_path=db_path, model_client=fake_model())
    with TestClient(app1) as client1:
        saved = client1.post("/reports", json=VALID_REPORT).json()["report"]
    del app1, client1
    gc.collect()

    app2 = create_app(db_path=db_path, model_client=fake_model())
    with TestClient(app2) as client2:
        resp = client2.get(f"/reports/{saved['id']}")
    assert resp.status_code == 200
    assert resp.json()["report"] == saved


def test_result_and_trace_survive_restart_via_api(db_path):
    app1 = create_app(db_path=db_path, model_client=fake_model())
    with TestClient(app1) as client1:
        posted = client1.post(
            "/reports", json={**VALID_REPORT, "wind_knots": 30}
        ).json()
    del app1, client1
    gc.collect()

    # App #2 gets a model that would fail if called: GET must read from the
    # database, not re-run the pipeline.
    app2 = create_app(
        db_path=db_path, model_client=FakeModel(script={"classify": ModelError("x")})
    )
    with TestClient(app2) as client2:
        resp = client2.get(f"/reports/{posted['report']['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"] == posted["report"]
    assert body["result"] == posted["result"]
    assert body["trace"] == posted["trace"]
    assert body["result"]["verdict"] == "no_go"
    assert [s["step"] for s in body["trace"]] == [
        "classify", "check_limits", "brief_by"
    ]


# --- Pipeline on filing ---------------------------------------------------


def test_post_safe_readings_runs_pipeline_and_returns_go(client):
    resp = client.post("/reports", json=VALID_REPORT)
    assert resp.status_code == 201
    body = resp.json()
    result = body["result"]
    assert result["verdict"] == "go"
    assert result["violations"] == []
    assert result["subsystem"] == "avionics"
    assert result["severity"] == "low"
    assert result["effective_severity"] == "low"
    assert result["brief_by_minutes"] == 30
    assert result["classify_fallback"] is False
    assert [s["step"] for s in body["trace"]] == [
        "classify", "check_limits", "brief_by"
    ]
    assert [s["position"] for s in body["trace"]] == [0, 1, 2]


def test_post_high_wind_is_no_go_and_forces_high(client):
    resp = client.post("/reports", json={**VALID_REPORT, "wind_knots": 30})
    assert resp.status_code == 201
    result = resp.json()["result"]
    assert result["verdict"] == "no_go"
    assert result["severity"] == "low"  # model's view is kept as-is
    assert result["effective_severity"] == "high"
    assert result["brief_by_minutes"] == 5
    assert [v["reading"] for v in result["violations"]] == ["wind_knots"]


def test_post_with_model_error_uses_code_verdict_and_fallbacks(db_path):
    model = FakeModel(script={"classify": ModelError("model is down")})
    app = create_app(db_path=db_path, model_client=model)
    with TestClient(app) as c:
        resp = c.post("/reports", json={**VALID_REPORT, "temp_c": 2.0})
    assert resp.status_code == 201
    body = resp.json()
    result = body["result"]
    assert result["subsystem"] == "unknown"
    assert result["severity"] == "high"
    assert result["classify_fallback"] is True
    # The verdict still comes from the code rules on the readings.
    assert result["verdict"] == "no_go"
    assert [v["reading"] for v in result["violations"]] == ["temp_c"]
    assert result["brief_by_minutes"] == 5
    assert len(body["trace"]) == 3
    assert body["trace"][0]["detail"]["fallback_used"] is True


def test_post_model_error_with_safe_readings_is_still_go(db_path):
    model = FakeModel(script={"classify": ModelError("model is down")})
    with TestClient(create_app(db_path=db_path, model_client=model)) as c:
        result = c.post("/reports", json=VALID_REPORT).json()["result"]
    assert result["verdict"] == "go"
    assert result["severity"] == "high"
    assert result["brief_by_minutes"] == 5


def test_unexpected_pipeline_crash_keeps_report_saved(db_path, monkeypatch, caplog):
    async def boom(client, report):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr("app.main.run_pipeline", boom)
    with TestClient(create_app(db_path=db_path, model_client=fake_model())) as c:
        with caplog.at_level(logging.ERROR, logger="app.main"):
            resp = c.post("/reports", json=VALID_REPORT)
        assert resp.status_code == 201
        body = resp.json()
        assert body["result"] is None
        assert body["trace"] == []
        assert body["error"]
        assert "unexpected bug" in caplog.text

        report_id = body["report"]["id"]
        one = c.get(f"/reports/{report_id}").json()
        assert one == {"report": body["report"], "result": None, "trace": []}

        item = c.get("/reports").json()["reports"][0]
        assert item["id"] == report_id
        for field in RESULT_SUMMARY_FIELDS:
            assert item[field] is None


def test_get_report_returns_same_result_and_trace_as_post(client):
    posted = client.post("/reports", json={**VALID_REPORT, "fuel_pct": 97}).json()
    got = client.get(f"/reports/{posted['report']['id']}")
    assert got.status_code == 200
    body = got.json()
    assert body == {
        "report": posted["report"],
        "result": posted["result"],
        "trace": posted["trace"],
    }


def test_list_includes_verdict_and_severity(client):
    first = client.post("/reports", json=VALID_REPORT).json()
    second = client.post(
        "/reports", json={**VALID_REPORT, "name": "Bob", "wind_knots": 30}
    ).json()
    items = client.get("/reports").json()["reports"]
    assert [i["id"] for i in items] == [
        second["report"]["id"], first["report"]["id"]
    ]
    newest, oldest = items
    for item, posted in ((newest, second), (oldest, first)):
        assert {k: item[k] for k in REPORT_FIELDS} == posted["report"]
        for field in RESULT_SUMMARY_FIELDS:
            assert item[field] == posted["result"][field]
    assert newest["verdict"] == "no_go"
    assert newest["severity"] == "low"
    assert newest["effective_severity"] == "high"
    assert oldest["verdict"] == "go"
    assert oldest["severity"] == "low"


def test_create_app_with_fake_provider_uses_demo_script():
    # conftest sets MODEL_PROVIDER=fake and DATABASE_PATH to a temp file.
    app = create_app()
    with TestClient(app) as c:
        assert isinstance(app.state.model_client, FakeModel)
        resp = c.post("/reports", json=VALID_REPORT)
    assert resp.status_code == 201
    body = resp.json()
    assert body["result"]["subsystem"] == "propulsion"
    assert body["result"]["severity"] == "medium"
    assert body["result"]["classify_fallback"] is False
    assert body["result"]["verdict"] == "go"
    assert body["trace"][0]["detail"]["reason"] == (
        "Demo mode reply, no real model was called."
    )
