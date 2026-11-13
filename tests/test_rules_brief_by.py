import pytest

from app.rules import brief_by_minutes


@pytest.mark.parametrize(
    "severity, has_violation, expected",
    [
        pytest.param("high", False, 5, id="high_no_violation"),
        pytest.param("medium", False, 15, id="medium_no_violation"),
        pytest.param("low", False, 30, id="low_no_violation"),
        pytest.param("low", True, 5, id="low_with_violation"),
        pytest.param("medium", True, 5, id="medium_with_violation"),
        pytest.param("high", True, 5, id="high_with_violation"),
        pytest.param("HIGH", False, 5, id="uppercase_high"),
        pytest.param(" Medium ", False, 15, id="padded_mixed_case_medium"),
        pytest.param("banana", False, 5, id="unknown_falls_back"),
        pytest.param(None, False, 5, id="none_falls_back"),
    ],
)
def test_brief_by_minutes(severity, has_violation, expected):
    assert brief_by_minutes(severity, has_violation) == expected
