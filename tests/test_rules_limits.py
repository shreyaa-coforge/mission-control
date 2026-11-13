import pytest

from app.rules import check_limits

# Safe baseline values; each single-limit case changes only one reading.
SAFE_WIND = 10
SAFE_TEMP = 15
SAFE_FUEL = 99


@pytest.mark.parametrize(
    "wind, temp, fuel, expected_verdict, expected_readings",
    [
        pytest.param(SAFE_WIND, SAFE_TEMP, SAFE_FUEL, "go", [], id="all_safe"),
        pytest.param(25, SAFE_TEMP, SAFE_FUEL, "go", [], id="wind_exactly_25"),
        pytest.param(25.1, SAFE_TEMP, SAFE_FUEL, "no_go", ["wind_knots"], id="wind_25_1"),
        pytest.param(SAFE_WIND, 4, SAFE_FUEL, "go", [], id="temp_exactly_4"),
        pytest.param(SAFE_WIND, 3.9, SAFE_FUEL, "no_go", ["temp_c"], id="temp_3_9"),
        pytest.param(SAFE_WIND, SAFE_TEMP, 98, "go", [], id="fuel_exactly_98"),
        pytest.param(SAFE_WIND, SAFE_TEMP, 97.9, "no_go", ["fuel_pct"], id="fuel_97_9"),
        pytest.param(SAFE_WIND, SAFE_TEMP, 100, "go", [], id="fuel_exactly_100"),
        pytest.param(SAFE_WIND, SAFE_TEMP, 100.1, "no_go", ["fuel_pct"], id="fuel_100_1"),
        pytest.param(
            30, 0, 50, "no_go", ["wind_knots", "temp_c", "fuel_pct"], id="all_three_bad"
        ),
    ],
)
def test_check_limits(wind, temp, fuel, expected_verdict, expected_readings):
    result = check_limits(wind_knots=wind, temp_c=temp, fuel_pct=fuel)

    assert result.verdict == expected_verdict
    assert len(result.violations) == len(expected_readings)
    assert [v.reading for v in result.violations] == expected_readings
