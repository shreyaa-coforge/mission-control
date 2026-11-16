"""Report pipeline: runs each step in order and records a trace.

The model classifies the report and runs three note checks (injection,
missing details, panic). The verdict, violations, brief-by time and cost are
all computed in plain Python by app/rules.py. The checks only raise review
flags; they never change the verdict and never approve anything.

After the checks, the draft and review loop writes the briefing prose. The
reviewer can only mark a draft "passed". The pipeline never approves a
briefing: only a human, through POST /briefings/{id}/approve, can do that.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app import rules
from app.model_client import ModelClient
from app.steps.briefing import (
    BriefingResult,
    RoundRecord,
    render_briefing,
    run_briefing_loop,
)
from app.steps.checks import CheckResult, run_all_checks
from app.steps.classify import classify_report


@dataclass
class TraceStep:
    position: int
    step: str  # e.g. "classify", "check_limits", "brief_by"
    kind: str  # "model" or "code"
    model_role: str | None  # None for code steps
    input_tokens: int
    output_tokens: int
    cost: float
    summary: str  # one short human-readable line
    detail: dict = field(default_factory=dict)  # step-specific console data


@dataclass
class PipelineResult:
    subsystem: str
    severity: str
    effective_severity: str
    classify_fallback: bool
    verdict: str
    violations: list[dict]
    brief_by_minutes: int
    trace: list[TraceStep]
    total_cost: float
    # "injection" / "missing" / "panic" -> {flagged, reason, details, failed}
    checks: dict[str, dict] = field(default_factory=dict)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    # headline, summary, recommended_action, rounds_used, review_passed,
    # last_feedback, draft_fallback. None only for results built by hand.
    # There is deliberately no status here: approval lives in the briefings
    # table and is set only by a human.
    briefing: dict | None = None
    briefing_text: str = ""


def _limits_summary(verdict: str, violations: list[rules.Violation]) -> str:
    if not violations:
        return verdict
    return f"{verdict}: " + "; ".join(v.message for v in violations)


_CHECK_LABELS = {
    "injection": "Hidden instructions",
    "missing": "Missing details",
    "panic": "Panic tone",
}

_CHECK_SUMMARY_PREFIX = {
    "injection": "instruction attempt",
    "panic": "alarmist tone",
}


def _check_summary(check: CheckResult) -> str:
    if check.failed:
        return "check failed"
    if not check.flagged:
        return "clear"
    if check.check == "missing":
        # e.g. "flagged: missing time, component"
        items = [
            d.removeprefix("no ").removesuffix(" given").strip()
            for d in check.details
        ]
        return "flagged: missing " + (", ".join(items) if items else "details")
    return f"flagged: {_CHECK_SUMMARY_PREFIX[check.check]}"


def _review_reasons(checks: list[CheckResult]) -> list[str]:
    """Plain-sentence reasons a human must look at this report.

    Only an injection flag or a failed check sends a report to review.
    Missing-details and panic flags alone are shown but do not.
    """
    reasons: list[str] = []
    for check in checks:
        if check.failed:
            reasons.append(
                f"{_CHECK_LABELS[check.check]} check could not be completed"
            )
        elif check.check == "injection" and check.flagged:
            evidence = (
                check.pattern_hits[0] if check.pattern_hits else check.reason
            )
            reasons.append(f"Possible hidden instructions in note: {evidence}")
    return reasons


def _tokens(response) -> tuple[int, int]:
    if response is None:
        return 0, 0
    return response.input_tokens or 0, response.output_tokens or 0


def _review_summary(record: RoundRecord) -> str:
    if record.review is None:
        return "review failed"
    if record.review.passed:
        return "passed"
    first = record.review.issues[0] if record.review.issues else record.review.feedback
    return f"not passed: {first}"


def _briefing_trace_steps(
    briefing: BriefingResult, start: int
) -> list[TraceStep]:
    """One draft step per round, plus a review step if the reviewer ran."""
    steps: list[TraceStep] = []
    for record in briefing.rounds:
        n = record.round_no
        in_tokens, out_tokens = _tokens(record.draft_response)
        steps.append(
            TraceStep(
                position=start + len(steps),
                step=f"draft_round_{n}",
                kind="model",
                model_role="strong",
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cost=rules.step_cost("strong", in_tokens, out_tokens),
                summary="draft ready" if record.draft else "draft invalid",
                detail={
                    "round": n,
                    "draft": record.draft.model_dump() if record.draft else None,
                    "error": record.draft_error,
                },
            )
        )
        # The reviewer is skipped when the draft was invalid.
        reviewer_called = (
            record.review is not None or record.review_error is not None
        )
        if not reviewer_called:
            continue
        in_tokens, out_tokens = _tokens(record.review_response)
        steps.append(
            TraceStep(
                position=start + len(steps),
                step=f"review_round_{n}",
                kind="model",
                model_role="strong",
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cost=rules.step_cost("strong", in_tokens, out_tokens),
                summary=_review_summary(record),
                detail={
                    "round": n,
                    "passed": record.review.passed if record.review else False,
                    "feedback": record.review.feedback if record.review else None,
                    "issues": list(record.review.issues) if record.review else [],
                    "error": record.review_error,
                },
            )
        )
    return steps


async def run_pipeline(client: ModelClient, report: dict) -> PipelineResult:
    trace: list[TraceStep] = []

    # a) classify (model step). classify_report never raises on model
    # failure; it returns fallback values instead.
    classified = await classify_report(client, report)
    response = classified.response
    in_tokens = response.input_tokens if response else 0
    out_tokens = response.output_tokens if response else 0
    trace.append(
        TraceStep(
            position=len(trace),
            step="classify",
            kind="model",
            model_role="fast",
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cost=rules.step_cost("fast", in_tokens, out_tokens),
            summary=f"{classified.subsystem} / {classified.severity}"
            + (" (fallback)" if classified.fallback_used else ""),
            detail={
                "subsystem": classified.subsystem,
                "severity": classified.severity,
                "reason": classified.reason,
                "fallback_used": classified.fallback_used,
                "fallback_fields": list(classified.fallback_fields),
                "error": classified.error,
            },
        )
    )

    # b) check_limits (code step).
    # IMPORTANT: verdict and violations come ONLY from rules.check_limits on
    # the three readings. Nothing from the model reply may influence them.
    limits = rules.check_limits(
        report["wind_knots"], report["temp_c"], report["fuel_pct"]
    )
    verdict = limits.verdict
    violations = [asdict(v) for v in limits.violations]
    trace.append(
        TraceStep(
            position=len(trace),
            step="check_limits",
            kind="code",
            model_role=None,
            input_tokens=0,
            output_tokens=0,
            cost=0.0,
            summary=_limits_summary(verdict, limits.violations),
            detail={"verdict": verdict, "violations": violations},
        )
    )

    # c) brief_by (code step). Any violation counts as high severity.
    has_violation = bool(violations)
    severity = classified.severity
    effective_severity = (
        "high" if has_violation or severity == "high" else severity
    )
    minutes = rules.brief_by_minutes(severity, has_violation)
    trace.append(
        TraceStep(
            position=len(trace),
            step="brief_by",
            kind="code",
            model_role=None,
            input_tokens=0,
            output_tokens=0,
            cost=0.0,
            summary=f"{minutes} min ({effective_severity})",
            detail={
                "severity": severity,
                "effective_severity": effective_severity,
                "has_violation": has_violation,
                "minutes": minutes,
            },
        )
    )

    # d) three note checks (model steps), run in parallel. They only raise
    # review flags. NOTHING from a check may touch verdict, violations,
    # severity, effective_severity, or brief_by_minutes, which are already
    # final above.
    check_results = await run_all_checks(client, report)
    for check in check_results:
        response = check.response
        in_tokens = response.input_tokens if response else 0
        out_tokens = response.output_tokens if response else 0
        detail = {
            "flagged": check.flagged,
            "reason": check.reason,
            "details": list(check.details),
            "failed": check.failed,
            "error": check.error,
            "elapsed_ms": check.elapsed_ms,
            "parallel_group": "checks",
        }
        if check.check == "injection":
            detail["pattern_hits"] = list(check.pattern_hits)
        trace.append(
            TraceStep(
                position=len(trace),
                step=check.step,
                kind="model",
                model_role="fast",
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cost=rules.step_cost("fast", in_tokens, out_tokens),
                summary=_check_summary(check),
                detail=detail,
            )
        )

    checks = {
        c.check: {
            "flagged": c.flagged,
            "reason": c.reason,
            "details": list(c.details),
            "failed": c.failed,
        }
        for c in check_results
    }
    review_reasons = _review_reasons(check_results)
    needs_review = bool(review_reasons)

    # e) draft and review loop (strong model steps). The model writes only
    # prose; render_briefing() puts the code-computed facts into the text.
    # NOTHING here may touch verdict, violations, severity or brief_by.
    facts = {
        "verdict": verdict,
        "violations": violations,
        "brief_by_minutes": minutes,
        "subsystem": classified.subsystem,
        "effective_severity": effective_severity,
        "needs_review": needs_review,
        "review_reasons": review_reasons,
        "checks": checks,
    }
    briefing_result = await run_briefing_loop(client, report, facts)
    trace.extend(_briefing_trace_steps(briefing_result, start=len(trace)))

    # NEVER APPROVE HERE. A reviewer "passed" mark is not an approval. The
    # pipeline only produces a draft; it is saved with status "pending" and
    # only a human, via POST /briefings/{id}/approve, can approve it.
    briefing = {
        "headline": briefing_result.headline,
        "summary": briefing_result.summary,
        "recommended_action": briefing_result.recommended_action,
        "rounds_used": briefing_result.rounds_used,
        "review_passed": briefing_result.review_passed,
        "last_feedback": briefing_result.last_feedback,
        "draft_fallback": briefing_result.draft_fallback,
    }
    briefing_text = render_briefing(briefing_result, facts)

    # Includes the strong draft and review steps.
    total_cost = rules.report_cost([asdict(s) for s in trace])

    return PipelineResult(
        subsystem=classified.subsystem,
        severity=severity,
        effective_severity=effective_severity,
        classify_fallback=classified.fallback_used,
        verdict=verdict,
        violations=violations,
        brief_by_minutes=minutes,
        trace=trace,
        total_cost=total_cost,
        checks=checks,
        needs_review=needs_review,
        review_reasons=review_reasons,
        briefing=briefing,
        briefing_text=briefing_text,
    )
