"""Input schema for launch reports and plain-language validation errors.

Only input sanity is checked here. Whether a launch is go or no-go is decided
by app/rules.py, never by this module.
"""

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

# Sanity ranges for the three readings: (min, max), inclusive.
#
# These ranges only catch broken input (typos, unit mix-ups, garbage values).
# They are deliberately much WIDER than the launch limits in app/rules.py
# (wind <= 25, temp >= 4, 98 <= fuel <= 100). Unsafe but physically real
# readings such as fuel 100.1, fuel 97.9, wind 30 or temp 3.9 must be ACCEPTED
# here so they reach the rules engine, which turns them into violations and a
# "no_go" verdict. Rejecting them at the schema would hide the danger instead
# of reporting it.
READING_RANGES: dict[str, tuple[float, float]] = {
    "wind_knots": (0, 200),
    "temp_c": (-90, 60),
    "fuel_pct": (0, 120),
}

# Maximum lengths for text fields (after whitespace is stripped). Minimum is 1.
TEXT_MAX_LENGTHS: dict[str, int] = {
    "name": 100,
    "station": 100,
    "note": 2000,
    "approved_by": 100,
}


class ReportIn(BaseModel):
    """A launch readiness report as submitted by a station."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=TEXT_MAX_LENGTHS["name"])
    station: str = Field(min_length=1, max_length=TEXT_MAX_LENGTHS["station"])
    wind_knots: float = Field(
        ge=READING_RANGES["wind_knots"][0],
        le=READING_RANGES["wind_knots"][1],
        allow_inf_nan=False,
    )
    temp_c: float = Field(
        ge=READING_RANGES["temp_c"][0],
        le=READING_RANGES["temp_c"][1],
        allow_inf_nan=False,
    )
    fuel_pct: float = Field(
        ge=READING_RANGES["fuel_pct"][0],
        le=READING_RANGES["fuel_pct"][1],
        allow_inf_nan=False,
    )
    note: str = Field(min_length=1, max_length=TEXT_MAX_LENGTHS["note"])

    @field_validator("wind_knots", "temp_c", "fuel_pct", mode="before")
    @classmethod
    def _reject_bool(cls, value: Any) -> Any:
        # bool is a subclass of int, so without this true/false would be
        # silently accepted as 1/0.
        if isinstance(value, bool):
            raise PydanticCustomError("bool_not_number", "boolean is not a number")
        return value


# --- Classification of a report by the model -----------------------------

Subsystem = Literal[
    "propulsion",
    "avionics",
    "telemetry",
    "guidance",
    "power",
    "thermal",
    "structures",
    "ground_systems",
    "weather",
    "other",
]
Severity = Literal["high", "medium", "low"]

ALLOWED_SUBSYSTEMS: tuple[str, ...] = get_args(Subsystem)
ALLOWED_SEVERITIES: tuple[str, ...] = get_args(Severity)
REASON_MAX_LENGTH = 300


class Classification(BaseModel):
    """Subsystem and severity for a report, as suggested by the model.

    Extra keys in the model reply (for example "verdict") are ignored: they
    are dropped on validation and never stored or used. The verdict is
    decided only by app/rules.py.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    subsystem: Subsystem
    severity: Severity
    # One sentence on why.
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)


# --- Pre-flight checks on a report note (injection, missing, panic) ------

CHECK_REASON_MAX_LENGTH = 300
CHECK_DETAILS_MAX_ITEMS = 10
CHECK_DETAIL_MAX_LENGTH = 200


class CheckReply(BaseModel):
    """The model's reply for one of the three note checks.

    ``flagged`` is strict: only a real JSON true/false is accepted, so a
    string such as "yes" or a number such as 1 is invalid. Extra keys are
    ignored.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    flagged: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=CHECK_REASON_MAX_LENGTH)
    details: list[
        Annotated[str, Field(max_length=CHECK_DETAIL_MAX_LENGTH)]
    ] = Field(default_factory=list, max_length=CHECK_DETAILS_MAX_ITEMS)


# --- Briefing draft and review (model writes prose only) ----------------

DRAFT_HEADLINE_MAX_LENGTH = 120
DRAFT_SUMMARY_MAX_LENGTH = 800
DRAFT_ACTION_MAX_LENGTH = 300
REVIEW_FEEDBACK_MAX_LENGTH = 500
REVIEW_ISSUES_MAX_ITEMS = 10


class DraftReply(BaseModel):
    """Briefing prose written by the model. Extra keys (for example
    "verdict") are ignored: the verdict and every other fact are rendered
    by code, never taken from the model."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    headline: str = Field(min_length=1, max_length=DRAFT_HEADLINE_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=DRAFT_SUMMARY_MAX_LENGTH)
    recommended_action: str = Field(
        min_length=1, max_length=DRAFT_ACTION_MAX_LENGTH
    )


class ReviewReply(BaseModel):
    """The reviewer model's verdict on a draft. ``passed`` only means the
    draft passed review; it is never an approval. ``passed`` is strict, so
    "yes" or 1 is invalid. Extra keys are ignored."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    passed: bool = Field(strict=True)
    feedback: str = Field(min_length=1, max_length=REVIEW_FEEDBACK_MAX_LENGTH)
    issues: list[str] = Field(
        default_factory=list, max_length=REVIEW_ISSUES_MAX_ITEMS
    )


# --- Human approval of a briefing ---------------------------------------


class ApproveRequest(BaseModel):
    """Body for POST /briefings/{report_id}/approve, sent by a person.

    ``reviewed`` is strict: only a real JSON true confirms the person
    reviewed a flagged report, so "yes" or 1 is rejected.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    approved_by: str = Field(
        min_length=1, max_length=TEXT_MAX_LENGTHS["approved_by"]
    )
    reviewed: bool = Field(default=False, strict=True)


# --- Plain-language error formatting -------------------------------------

_NOT_A_NUMBER = {"float_type", "float_parsing", "bool_not_number"}
_NOT_FINITE = {"finite_number"}
_OUT_OF_RANGE = {"greater_than_equal", "less_than_equal", "greater_than", "less_than"}
_NOT_TEXT = {"string_type"}
_TOO_SHORT = {"string_too_short"}
_TOO_LONG = {"string_too_long"}
_NOT_BOOL = {"bool_type", "bool_parsing"}

# Location prefixes FastAPI adds in front of the field name.
_LOC_PREFIXES = {"body", "query", "path", "header", "cookie"}


def _field_from_loc(loc: tuple) -> str:
    parts = [str(p) for p in loc]
    if parts and parts[0] in _LOC_PREFIXES and len(parts) > 1:
        parts = parts[1:]
    return ".".join(parts) if parts else "body"


def _message_for(field: str, err_type: str) -> str:
    if err_type == "missing":
        return f"{field} is required"
    if err_type == "extra_forbidden":
        return f"unexpected field: {field}"
    if err_type in _NOT_A_NUMBER:
        return f"{field} must be a number"
    if err_type in _NOT_FINITE:
        return f"{field} must be a finite number"
    if err_type in _OUT_OF_RANGE and field in READING_RANGES:
        lo, hi = READING_RANGES[field]
        return f"{field} must be between {lo:g} and {hi:g}"
    if err_type in _NOT_BOOL:
        return f"{field} must be true or false"
    if err_type in _NOT_TEXT:
        return f"{field} must be text"
    if err_type in _TOO_SHORT:
        return f"{field} cannot be blank"
    if err_type in _TOO_LONG and field in TEXT_MAX_LENGTHS:
        return f"{field} must be {TEXT_MAX_LENGTHS[field]} characters or fewer"
    if err_type == "json_invalid":
        return "request body must be valid JSON"
    if err_type in {"model_type", "model_attributes_type", "dict_type"}:
        return "request body must be a JSON object"
    return f"{field} is invalid"


def format_validation_errors(exc: Any, error: str = "Invalid report") -> dict:
    """Turn a Pydantic ValidationError or FastAPI RequestValidationError into
    {"error": error, "details": [{"field": ..., "message": ...}]}.

    Each bad field appears once, in plain words, with no Pydantic internals.
    """
    if isinstance(exc, ValidationError) or hasattr(exc, "errors"):
        raw = exc.errors()
    else:
        raw = []

    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for err in raw:
        err_type = err.get("type", "")
        loc = tuple(err.get("loc", ()))
        if err_type == "json_invalid" or not loc or loc == ("body",):
            field = "body"
        else:
            field = _field_from_loc(loc)
        if field in seen:
            continue
        seen.add(field)
        details.append({"field": field, "message": _message_for(field, err_type)})

    return {"error": error, "details": details}
