"""Report pipeline: runs each step in order and records a trace.

The model classifies the report and runs three note checks (injection,
missing details, panic). The verdict, violations, brief-by time and cost are
all computed in plain Python by app/rules.py. The checks only raise review
flags; they never change the verdict and never approve anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app import rules
from app.model_client import ModelClient
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

    # A later branch will add the draft and review loop here.

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
    )
