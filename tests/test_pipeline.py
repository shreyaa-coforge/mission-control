"""Tests for the pipeline backbone in app/pipeline.py."""

import json

from app import rules
from app.model_client import FakeModel, ModelError
from app.pipeline import run_pipeline

IN_TOKENS = 120
OUT_TOKENS = 40

SAFE = {"wind_knots": 10.0, "temp_c": 15.0, "fuel_pct": 99.0}


def _report(**readings) -> dict:
    return {
        "name": "Ada",
        "station": "Pad 39A",
        **SAFE,
        **readings,
        "note": "Turbopump vibration on engine 2.",
    }


def _reply(severity: str, subsystem: str = "propulsion", **extra) -> str:
    return json.dumps(
        {"subsystem": subsystem, "severity": severity, "reason": "Test.", **extra}
    )


async def _run(classify_entry, **readings):
    model = FakeModel(
        {"classify": classify_entry},
        input_tokens=IN_TOKENS,
        output_tokens=OUT_TOKENS,
    )
    return await run_pipeline(model, _report(**readings))


async def test_safe_high_is_go_5():
    result = await _run(_reply("high"))
    assert result.verdict == "go"
    assert result.violations == []
    assert result.brief_by_minutes == 5
    assert result.effective_severity == "high"


async def test_safe_medium_is_go_15():
    result = await _run(_reply("medium"))
    assert result.verdict == "go"
    assert result.brief_by_minutes == 15
    assert result.effective_severity == "medium"


async def test_safe_low_is_go_30():
    result = await _run(_reply("low"))
    assert result.verdict == "go"
    assert result.brief_by_minutes == 30
    assert result.effective_severity == "low"


async def test_wind_violation_forces_high():
    result = await _run(_reply("low"), wind_knots=30)
    assert result.verdict == "no_go"
    assert result.severity == "low"
    assert result.effective_severity == "high"
    assert result.brief_by_minutes == 5
    assert [v["reading"] for v in result.violations] == ["wind_knots"]


async def test_fuel_over_100_is_no_go():
    result = await _run(_reply("medium"), fuel_pct=100.1)
    assert result.verdict == "no_go"
    assert result.brief_by_minutes == 5
    assert [v["reading"] for v in result.violations] == ["fuel_pct"]


async def test_invalid_reply_uses_fallback():
    result = await _run("not json at all")
    assert result.classify_fallback is True
    assert result.subsystem == "unknown"
    assert result.severity == "high"
    assert result.brief_by_minutes == 5
    assert result.verdict == "go"


async def test_verdict_never_from_model():
    result = await _run(_reply("low", verdict="go", violations=[]), wind_knots=30)
    assert result.verdict == "no_go"
    assert result.severity == "low"
    assert len(result.violations) == 1


async def test_model_failure_still_completes():
    result = await _run(ModelError("boom"), temp_c=2)
    assert result.classify_fallback is True
    assert result.subsystem == "unknown"
    assert result.severity == "high"
    assert result.verdict == "no_go"
    assert [v["reading"] for v in result.violations] == ["temp_c"]
    assert result.brief_by_minutes == 5
    classify = result.trace[0]
    assert classify.input_tokens == 0
    assert classify.output_tokens == 0
    assert classify.cost == 0
    assert "boom" in classify.detail["error"]


async def test_trace_shape():
    result = await _run(_reply("medium"))
    assert [s.step for s in result.trace] == ["classify", "check_limits", "brief_by"]
    assert [s.position for s in result.trace] == [0, 1, 2]

    classify, limits, brief = result.trace
    assert classify.kind == "model"
    assert classify.model_role == "fast"
    assert classify.input_tokens == IN_TOKENS
    assert classify.output_tokens == OUT_TOKENS
    assert classify.summary == "propulsion / medium"
    assert classify.detail["fallback_used"] is False

    for step in (limits, brief):
        assert step.kind == "code"
        assert step.model_role is None
        assert step.input_tokens == 0
        assert step.output_tokens == 0
        assert step.cost == 0

    assert limits.detail == {"verdict": "go", "violations": []}
    assert brief.detail == {
        "severity": "medium",
        "effective_severity": "medium",
        "has_violation": False,
        "minutes": 15,
    }


async def test_total_cost_matches_fast_step_cost():
    result = await _run(_reply("low"))
    expected = rules.step_cost("fast", IN_TOKENS, OUT_TOKENS)
    assert result.total_cost == expected
    assert result.trace[0].cost == expected
