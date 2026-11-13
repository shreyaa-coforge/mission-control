import pytest

from app.rules import report_cost, step_cost

TEST_PRICES = {
    "fast": {"input": 1.0, "output": 2.0},
    "strong": {"input": 10.0, "output": 20.0},
}


def test_fast_one_million_each_way():
    assert step_cost("fast", 1_000_000, 1_000_000, TEST_PRICES) == 3.0


def test_strong_partial_million():
    assert step_cost("strong", 500_000, 250_000, TEST_PRICES) == 10.0


def test_zero_tokens_costs_nothing():
    assert step_cost("fast", 0, 0, TEST_PRICES) == 0.0


def test_unknown_role_raises():
    with pytest.raises(ValueError):
        step_cost("medium", 100, 100, TEST_PRICES)


@pytest.mark.parametrize("input_tokens, output_tokens", [(-1, 0), (0, -1)])
def test_negative_tokens_raise(input_tokens, output_tokens):
    with pytest.raises(ValueError):
        step_cost("fast", input_tokens, output_tokens, TEST_PRICES)


def test_report_cost_sums_model_steps_and_skips_code_steps():
    steps = [
        {"model_role": "fast", "input_tokens": 1_000_000, "output_tokens": 1_000_000},
        {"model_role": "strong", "input_tokens": 500_000, "output_tokens": 250_000},
        {"model_role": None, "input_tokens": 0, "output_tokens": 0},
    ]
    expected = step_cost("fast", 1_000_000, 1_000_000, TEST_PRICES) + step_cost(
        "strong", 500_000, 250_000, TEST_PRICES
    )
    assert report_cost(steps, TEST_PRICES) == expected == 13.0


def test_report_cost_empty_list():
    assert report_cost([], TEST_PRICES) == 0.0
