"""Tests for the pipeline backbone in app/pipeline.py."""

import json

import pytest

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


def _check(flagged: bool = False, reason: str = "Test.", details=()) -> str:
    return json.dumps(
        {"flagged": flagged, "reason": reason, "details": list(details)}
    )


CLEAR_CHECKS = {
    "check_injection": _check(),
    "check_missing": _check(),
    "check_panic": _check(),
}

CHECK_STEPS = ["check_injection", "check_missing", "check_panic"]


def _draft(headline: str = "Engine 2 vibration") -> str:
    return json.dumps(
        {
            "headline": headline,
            "summary": "Vibration slightly high.",
            "recommended_action": "Inspect turbopump 2.",
        }
    )


def _review(passed: bool, feedback: str = "Fine.", issues=()) -> str:
    return json.dumps(
        {"passed": passed, "feedback": feedback, "issues": list(issues)}
    )


BRIEFING_SCRIPT = {"draft": _draft(), "review": _review(True)}
BRIEFING_STEPS = ["draft_round_1", "review_round_1"]


def _model(classify_entry, delay: float = 0.0, **check_overrides) -> FakeModel:
    return FakeModel(
        {
            "classify": classify_entry,
            **CLEAR_CHECKS,
            **BRIEFING_SCRIPT,
            **check_overrides,
        },
        input_tokens=IN_TOKENS,
        output_tokens=OUT_TOKENS,
        delay=delay,
    )


async def _run(classify_entry, **readings):
    return await run_pipeline(_model(classify_entry), _report(**readings))


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
    assert [s.step for s in result.trace] == [
        "classify", "check_limits", "brief_by", *CHECK_STEPS, *BRIEFING_STEPS
    ]
    assert [s.position for s in result.trace] == [0, 1, 2, 3, 4, 5, 6, 7]

    classify, limits, brief = result.trace[:3]
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
    strong = rules.step_cost("strong", IN_TOKENS, OUT_TOKENS)
    # Four fast model steps (classify plus the three checks) and two strong
    # ones (draft and review, round 1).
    assert result.total_cost == pytest.approx(4 * expected + 2 * strong)
    assert result.trace[0].cost == expected


# --- Three parallel checks ------------------------------------------------


async def test_trace_has_six_steps_in_order():
    result = await _run(_reply("medium"))
    # The first six steps; the draft and review steps follow them.
    assert [s.step for s in result.trace[:6]] == [
        "classify", "check_limits", "brief_by",
        "check_injection", "check_missing", "check_panic",
    ]


async def test_check_steps_share_parallel_group_and_shape():
    result = await _run(_reply("medium"))
    checks = result.trace[3:6]
    for step in checks:
        assert step.kind == "model"
        assert step.model_role == "fast"
        assert step.input_tokens == IN_TOKENS
        assert step.output_tokens == OUT_TOKENS
        assert step.cost == rules.step_cost("fast", IN_TOKENS, OUT_TOKENS)
        assert step.summary == "clear"
        assert step.detail["parallel_group"] == "checks"
        for key in ("flagged", "reason", "details", "failed", "error",
                    "elapsed_ms"):
            assert key in step.detail
    assert "pattern_hits" in checks[0].detail
    assert result.needs_review is False
    assert result.review_reasons == []
    assert set(result.checks) == {"injection", "missing", "panic"}


async def test_checks_overlap_in_pipeline():
    model = _model(_reply("low"), delay=0.02)
    await run_pipeline(model, _report())
    assert model.max_in_flight == 3


async def test_failed_missing_check_sets_needs_review():
    model = _model(_reply("low"), check_missing=ModelError("down"))
    result = await run_pipeline(model, _report())
    assert result.checks["missing"]["failed"] is True
    assert result.needs_review is True
    assert "Missing details check could not be completed" in result.review_reasons
    missing_step = next(s for s in result.trace if s.step == "check_missing")
    assert missing_step.summary == "check failed"
    assert missing_step.input_tokens == 0
    assert missing_step.cost == 0
    # The verdict is untouched by the failed check.
    assert result.verdict == "go"


async def test_panic_flag_alone_does_not_need_review():
    model = _model(
        _reply("low"),
        check_panic=_check(True, "Very alarmist.", ["DISASTER!!!"]),
    )
    result = await run_pipeline(model, _report())
    assert result.checks["panic"]["flagged"] is True
    assert result.needs_review is False
    assert result.review_reasons == []
    panic_step = next(s for s in result.trace if s.step == "check_panic")
    assert panic_step.summary == "flagged: alarmist tone"


async def test_missing_flag_alone_does_not_need_review():
    model = _model(
        _reply("low"),
        check_missing=_check(True, "Vague.", ["no time given", "component"]),
    )
    result = await run_pipeline(model, _report())
    assert result.checks["missing"]["flagged"] is True
    assert result.needs_review is False
    missing_step = next(s for s in result.trace if s.step == "check_missing")
    assert missing_step.summary == "flagged: missing time, component"


async def test_injection_flag_summary_and_reason():
    model = _model(
        _reply("low"),
        check_injection=_check(True, "Tells the AI what to do.", ["do it"]),
    )
    result = await run_pipeline(model, _report())
    step = next(s for s in result.trace if s.step == "check_injection")
    assert step.summary == "flagged: instruction attempt"
    assert result.needs_review is True
    assert result.review_reasons == [
        "Possible hidden instructions in note: Tells the AI what to do."
    ]


async def test_total_cost_is_sum_of_four_model_steps():
    result = await _run(_reply("low"))
    model_steps = [s for s in result.trace if s.kind == "model"]
    fast_steps = [s for s in model_steps if s.model_role == "fast"]
    assert [s.step for s in fast_steps] == ["classify", *CHECK_STEPS]
    expected = sum(
        rules.step_cost(s.model_role, s.input_tokens, s.output_tokens)
        for s in model_steps
    )
    assert result.total_cost == pytest.approx(expected)


async def test_checks_never_touch_verdict_or_brief_by():
    flagged_all = _model(
        _reply("low"),
        check_injection=_check(True, "x"),
        check_missing=_check(True, "x"),
        check_panic=_check(True, "x"),
    )
    failed_all = _model(
        _reply("low"),
        check_injection=ModelError("x"),
        check_missing=ModelError("x"),
        check_panic=ModelError("x"),
    )
    baseline = await _run(_reply("low"))
    for model in (flagged_all, failed_all):
        result = await run_pipeline(model, _report())
        assert result.verdict == baseline.verdict
        assert result.violations == baseline.violations
        assert result.severity == baseline.severity
        assert result.effective_severity == baseline.effective_severity
        assert result.brief_by_minutes == baseline.brief_by_minutes


# --- Draft and review loop --------------------------------------------------


async def test_review_passing_round_one_gives_eight_steps():
    result = await _run(_reply("medium"))
    assert [s.step for s in result.trace] == [
        "classify", "check_limits", "brief_by", *CHECK_STEPS,
        "draft_round_1", "review_round_1",
    ]
    draft, review = result.trace[6:]
    for step in (draft, review):
        assert step.kind == "model"
        assert step.model_role == "strong"
        assert step.input_tokens == IN_TOKENS
        assert step.output_tokens == OUT_TOKENS
        assert step.cost == rules.step_cost("strong", IN_TOKENS, OUT_TOKENS)
    assert draft.summary == "draft ready"
    assert draft.detail["draft"]["headline"] == "Engine 2 vibration"
    assert review.summary == "passed"
    assert result.briefing["review_passed"] is True
    assert result.briefing["rounds_used"] == 1
    assert result.briefing["draft_fallback"] is False
    assert "status" not in result.briefing


async def test_review_always_failing_gives_three_rounds():
    model = _model(
        _reply("medium"),
        review=_review(False, "Too vague.", ["Mention the engine."]),
    )
    result = await run_pipeline(model, _report())
    assert len(result.trace) == 12
    assert [s.step for s in result.trace[6:]] == [
        "draft_round_1", "review_round_1",
        "draft_round_2", "review_round_2",
        "draft_round_3", "review_round_3",
    ]
    for step in result.trace[6:]:
        if step.step.startswith("review"):
            assert step.summary == "not passed: Mention the engine."
            assert step.detail["issues"] == ["Mention the engine."]
    assert result.briefing["review_passed"] is False
    assert result.briefing["rounds_used"] == 3


async def test_invalid_draft_skips_review_step():
    model = _model(_reply("medium"), draft="not json")
    result = await run_pipeline(model, _report())
    assert [s.step for s in result.trace[6:]] == [
        "draft_round_1", "draft_round_2", "draft_round_3",
    ]
    assert all(s.summary == "draft invalid" for s in result.trace[6:])
    assert all(s.detail["error"] for s in result.trace[6:])
    assert result.briefing["draft_fallback"] is True


async def test_total_cost_is_sum_of_every_model_step():
    model = _model(
        _reply("medium"),
        review=_review(False, "Too vague.", ["Mention the engine."]),
    )
    result = await run_pipeline(model, _report())
    model_steps = [s for s in result.trace if s.kind == "model"]
    assert {s.model_role for s in model_steps} == {"fast", "strong"}
    expected = sum(
        rules.step_cost(s.model_role, s.input_tokens, s.output_tokens)
        for s in model_steps
    )
    assert result.total_cost == pytest.approx(expected)


async def test_briefing_text_starts_with_code_verdict_line():
    # The draft headline claims GO; the code verdict line still says NO_GO.
    model = _model(_reply("low"), draft=_draft(headline="All GO for launch"))
    result = await run_pipeline(model, _report(wind_knots=30))
    assert result.verdict == "no_go"
    assert result.briefing_text.startswith("VERDICT: NO_GO\n")
