"""Tests for the ReportIn input schema and plain-language error formatting."""

import math

import pytest
from pydantic import ValidationError

from app.schemas import ReportIn, format_validation_errors

FIELDS = ["name", "station", "wind_knots", "temp_c", "fuel_pct", "note"]
READINGS = ["wind_knots", "temp_c", "fuel_pct"]


def valid_report(**overrides):
    data = {
        "name": "Ada Lovelace",
        "station": "Pad 39A",
        "wind_knots": 12.5,
        "temp_c": 18.0,
        "fuel_pct": 99.0,
        "note": "All systems nominal.",
    }
    data.update(overrides)
    return data


def errors_for(data) -> dict:
    with pytest.raises(ValidationError) as excinfo:
        ReportIn(**data)
    return format_validation_errors(excinfo.value)


def messages_by_field(data) -> dict[str, str]:
    return {d["field"]: d["message"] for d in errors_for(data)["details"]}


# --- Valid input --------------------------------------------------------


def test_valid_report_passes():
    report = ReportIn(**valid_report())
    assert report.wind_knots == 12.5
    assert report.temp_c == 18.0
    assert report.fuel_pct == 99.0


def test_whitespace_is_stripped():
    report = ReportIn(
        **valid_report(name="  Ada  ", station="\tPad 39A\n", note="  ok  ")
    )
    assert report.name == "Ada"
    assert report.station == "Pad 39A"
    assert report.note == "ok"


# --- Missing and blank fields -------------------------------------------


@pytest.mark.parametrize("field", FIELDS)
def test_missing_field_is_required(field):
    data = valid_report()
    del data[field]
    assert messages_by_field(data) == {field: f"{field} is required"}


@pytest.mark.parametrize("field", ["name", "station", "note"])
def test_blank_text_is_rejected(field):
    assert messages_by_field(valid_report(**{field: "   "})) == {
        field: f"{field} cannot be blank"
    }


@pytest.mark.parametrize("field,limit", [("name", 100), ("station", 100), ("note", 2000)])
def test_text_too_long_is_rejected(field, limit):
    assert ReportIn(**valid_report(**{field: "x" * limit}))
    assert messages_by_field(valid_report(**{field: "x" * (limit + 1)})) == {
        field: f"{field} must be {limit} characters or fewer"
    }


# --- Reading type checks -------------------------------------------------


@pytest.mark.parametrize("field", READINGS)
def test_non_numeric_reading_is_rejected(field):
    assert messages_by_field(valid_report(**{field: "abc"})) == {
        field: f"{field} must be a number"
    }


@pytest.mark.parametrize("field", READINGS)
@pytest.mark.parametrize("value", [True, False])
def test_boolean_reading_is_rejected(field, value):
    assert messages_by_field(valid_report(**{field: value})) == {
        field: f"{field} must be a number"
    }


@pytest.mark.parametrize("field", READINGS)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nan_and_infinity_are_rejected(field, value):
    assert messages_by_field(valid_report(**{field: value})) == {
        field: f"{field} must be a finite number"
    }


# --- Sanity ranges -------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("wind_knots", -1, "wind_knots must be between 0 and 200"),
        ("wind_knots", 201, "wind_knots must be between 0 and 200"),
        ("temp_c", -91, "temp_c must be between -90 and 60"),
        ("temp_c", 61, "temp_c must be between -90 and 60"),
        ("fuel_pct", -1, "fuel_pct must be between 0 and 120"),
        ("fuel_pct", 121, "fuel_pct must be between 0 and 120"),
    ],
)
def test_out_of_sanity_range_is_rejected(field, value, expected):
    msg = messages_by_field(valid_report(**{field: value}))[field]
    assert "must be between" in msg
    assert msg == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("fuel_pct", 100.1),
        ("fuel_pct", 97.9),
        ("wind_knots", 30),
        ("temp_c", 3.9),
    ],
)
def test_unsafe_but_real_readings_are_accepted(field, value):
    # These break the launch limits but are real readings: the schema must let
    # them through so rules.py can judge them and return "no_go".
    report = ReportIn(**valid_report(**{field: value}))
    assert getattr(report, field) == value


# --- Extra fields and multiple errors -----------------------------------


def test_extra_field_is_rejected():
    assert messages_by_field(valid_report(color="red")) == {
        "color": "unexpected field: color"
    }


def test_two_bad_fields_give_two_entries():
    result = errors_for(valid_report(fuel_pct=121, name=""))
    assert len(result["details"]) == 2
    assert {d["field"] for d in result["details"]} == {"fuel_pct", "name"}


def test_format_has_exactly_error_and_details_keys():
    result = errors_for(valid_report(fuel_pct=121))
    assert set(result.keys()) == {"error", "details"}
    assert result["error"] == "Invalid report"
    assert result["details"] == [
        {"field": "fuel_pct", "message": "fuel_pct must be between 0 and 120"}
    ]


def test_details_contain_no_pydantic_internals():
    result = errors_for({"wind_knots": "abc", "extra": 1})
    for entry in result["details"]:
        assert set(entry.keys()) == {"field", "message"}
        assert "pydantic" not in entry["message"].lower()
        assert "errors.pydantic.dev" not in entry["message"]
