"""Tests for the front end pages, static assets, and the API data contract
the console relies on. Uses a FakeModel: no network, no API key."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_client import FakeModel

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"

CHECK_REPLY = json.dumps({"flagged": False, "reason": "Clear.", "details": []})
SCRIPT = {
    "classify": json.dumps(
        {"subsystem": "avionics", "severity": "low", "reason": "Sensor noise."}
    ),
    "check_injection": CHECK_REPLY,
    "check_missing": CHECK_REPLY,
    "check_panic": CHECK_REPLY,
    "draft": json.dumps(
        {
            "headline": "Avionics noise",
            "summary": "Minor sensor noise.",
            "recommended_action": "Monitor.",
        }
    ),
    "review": json.dumps({"passed": True, "feedback": "Fine.", "issues": []}),
}

VALID_REPORT = {
    "name": "Alice",
    "station": "Pad 39A",
    "wind_knots": 30,
    "temp_c": 18.0,
    "fuel_pct": 99.0,
    "note": "<b>Sensor</b> noise on bus 2 at 14:02, ongoing, 3 dB.",
}

TRACE_FIELDS = (
    "position", "step", "kind", "input_tokens", "output_tokens", "cost",
    "summary",
)


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "ui.db"), model_client=FakeModel(script=SCRIPT))
    with TestClient(app) as c:
        yield c


def test_console_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text
    assert 'id="reports-list"' in html
    assert 'id="report-detail"' in html
    assert '<script src="/static/common.js">' in html
    assert '<script src="/static/console.js">' in html
    assert 'href="/file"' in html


def test_file_page_served(client):
    resp = client.get("/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text
    for name in ("name", "station", "wind_knots", "temp_c", "fuel_pct"):
        assert re.search(rf'<input[^>]*name="{name}"', html), name
    assert re.search(r'<textarea[^>]*name="note"', html)
    assert "novalidate" in html
    assert '<script src="/static/file.js">' in html
    assert "Limits: wind 25 knots or less, temp 4 C or more, fuel 98 to 100 %" in html


def test_file_page_fields_reference_error_elements(client):
    html = client.get("/file").text
    for name in ("name", "station", "wind_knots", "temp_c", "fuel_pct", "note"):
        assert f'id="{name}-error"' in html
        assert re.search(
            rf'name="{name}"[^>]*aria-describedby="[^"]*{name}-error', html
        ), name


@pytest.mark.parametrize(
    "path", ["styles.css", "common.js", "file.js", "console.js"]
)
def test_static_assets_served(client, path):
    resp = client.get(f"/static/{path}")
    assert resp.status_code == 200
    assert resp.text.strip()


def test_pages_use_no_external_resources():
    for name in ("index.html", "file.html", "styles.css"):
        text = (STATIC_DIR / name).read_text()
        assert "http://" not in text and "https://" not in text, name


@pytest.mark.parametrize("name", ["console.js", "file.js", "common.js"])
def test_innerhtml_only_used_for_static_markup(name):
    for lineno, line in enumerate((STATIC_DIR / name).read_text().splitlines(), 1):
        if "innerHTML" in line or "outerHTML" in line or "insertAdjacentHTML" in line:
            assert "// static markup" in line, f"{name}:{lineno}: {line.strip()}"


def test_trace_steps_have_fields_cards_rely_on(client):
    created = client.post("/reports", json=VALID_REPORT)
    assert created.status_code == 201
    report_id = created.json()["report"]["id"]

    body = client.get(f"/reports/{report_id}").json()
    trace = body["trace"]
    assert trace
    for step in trace:
        for field in TRACE_FIELDS:
            assert field in step, (step.get("step"), field)
        assert step["kind"] in ("model", "code")
        assert isinstance(step["cost"], (int, float))
    positions = [s["position"] for s in trace]
    assert positions == sorted(positions)


def test_report_detail_total_cost_is_number(client):
    report_id = client.post("/reports", json=VALID_REPORT).json()["report"]["id"]
    body = client.get(f"/reports/{report_id}").json()
    total_cost = body["result"]["total_cost"]
    assert isinstance(total_cost, (int, float)) and not isinstance(total_cost, bool)


def test_report_detail_has_fields_console_renders(client):
    report_id = client.post("/reports", json=VALID_REPORT).json()["report"]["id"]
    body = client.get(f"/reports/{report_id}").json()
    result = body["result"]
    for key in ("verdict", "violations", "brief_by_minutes", "subsystem",
                "severity", "effective_severity", "classify_fallback",
                "needs_review", "review_reasons", "checks"):
        assert key in result, key
    assert result["verdict"] == "no_go"  # wind 30 > 25
    for key in ("status", "rounds_used", "review_passed", "full_text"):
        assert key in body["briefing"], key
    # The raw note is returned unchanged; the console shows it as text.
    assert body["report"]["note"] == VALID_REPORT["note"]
