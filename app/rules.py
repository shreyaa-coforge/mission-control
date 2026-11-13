"""Deterministic launch rules. Plain Python only: no model or network calls."""

from dataclasses import dataclass, field

WIND_MAX_KNOTS = 25
TEMP_MIN_C = 4
FUEL_MIN_PCT = 98
FUEL_MAX_PCT = 100


@dataclass
class Violation:
    reading: str  # "wind_knots", "temp_c", or "fuel_pct"
    value: float
    limit: str
    message: str


@dataclass
class LimitResult:
    verdict: str  # "go" or "no_go"
    violations: list[Violation] = field(default_factory=list)


def _fmt(value: float) -> str:
    """Format a number without a trailing '.0' (25.0 -> '25', 25.1 -> '25.1')."""
    return f"{value:g}"


def check_limits(wind_knots: float, temp_c: float, fuel_pct: float) -> LimitResult:
    """Check every reading against its launch limit and collect all violations."""
    violations: list[Violation] = []

    if wind_knots > WIND_MAX_KNOTS:
        violations.append(
            Violation(
                reading="wind_knots",
                value=wind_knots,
                limit=f"<= {WIND_MAX_KNOTS} knots",
                message=(
                    f"Wind {_fmt(wind_knots)} knots exceeds max "
                    f"{WIND_MAX_KNOTS} knots"
                ),
            )
        )

    if temp_c < TEMP_MIN_C:
        violations.append(
            Violation(
                reading="temp_c",
                value=temp_c,
                limit=f">= {TEMP_MIN_C} C",
                message=f"Temp {_fmt(temp_c)} C is below min {TEMP_MIN_C} C",
            )
        )

    if fuel_pct < FUEL_MIN_PCT or fuel_pct > FUEL_MAX_PCT:
        violations.append(
            Violation(
                reading="fuel_pct",
                value=fuel_pct,
                limit=f"{FUEL_MIN_PCT} to {FUEL_MAX_PCT} %",
                message=(
                    f"Fuel {_fmt(fuel_pct)} % is outside "
                    f"{FUEL_MIN_PCT} to {FUEL_MAX_PCT} %"
                ),
            )
        )

    verdict = "no_go" if violations else "go"
    return LimitResult(verdict=verdict, violations=violations)


BRIEF_BY_MINUTES = {"high": 5, "medium": 15, "low": 30}
SAFEST_BRIEF_BY_MINUTES = BRIEF_BY_MINUTES["high"]


def brief_by_minutes(severity: str, has_violation: bool) -> int:
    """Return the brief-by time in minutes for a severity.

    Any violation forces the high-severity time. Severity is normalised
    (lowercased, stripped); any unknown or non-string value falls back to the
    safest (shortest) time.
    """
    if has_violation:
        return SAFEST_BRIEF_BY_MINUTES
    if isinstance(severity, str):
        severity = severity.lower().strip()
    return BRIEF_BY_MINUTES.get(severity, SAFEST_BRIEF_BY_MINUTES)


# US dollars per 1 million tokens, keyed by model role.
# PLACEHOLDERS: update these from the Azure OpenAI pricing page for the
# actual deployments before relying on the cost figures.
PRICES_PER_MILLION = {
    "fast": {"input": 0.15, "output": 0.60},
    "strong": {"input": 2.50, "output": 10.00},
}


def step_cost(
    model_role: str,
    input_tokens: int,
    output_tokens: int,
    prices: dict | None = None,
) -> float:
    """Return the US dollar cost of one model step, rounded to 6 decimals."""
    if prices is None:
        prices = PRICES_PER_MILLION
    if model_role not in prices:
        raise ValueError(f"Unknown model role: {model_role!r}")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts must not be negative")
    rate = prices[model_role]
    cost = (
        input_tokens / 1_000_000 * rate["input"]
        + output_tokens / 1_000_000 * rate["output"]
    )
    return round(cost, 6)


def report_cost(steps: list[dict], prices: dict | None = None) -> float:
    """Return the total cost of a report's steps, rounded to 6 decimals.

    Steps whose model_role is None are plain code steps and cost 0.
    """
    total = 0.0
    for step in steps:
        if step["model_role"] is None:
            continue
        total += step_cost(
            step["model_role"], step["input_tokens"], step["output_tokens"], prices
        )
    return round(total, 6)
