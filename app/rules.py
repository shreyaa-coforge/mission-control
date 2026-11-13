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
