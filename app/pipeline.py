"""Report pipeline: runs each step in order and records a trace.

The model only classifies. The verdict, violations, brief-by time and cost
are all computed in plain Python by app/rules.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app import rules
from app.model_client import ModelClient
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


def _limits_summary(verdict: str, violations: list[rules.Violation]) -> str:
    if not violations:
        return verdict
    return f"{verdict}: " + "; ".join(v.message for v in violations)


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

    # Later branches will add the three checks and the draft and review
    # loop here.

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
    )
