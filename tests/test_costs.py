"""P7 — cost estimation."""
from harness.costs import estimate_cost


def test_free_model_is_zero_cost():
    assert estimate_cost("fake/scripted-v1", input_tokens=1000, output_tokens=1000) == 0.0
    assert estimate_cost("meta-llama/llama-3.3-70b-instruct:free", 500, 500) == 0.0


def test_unknown_model_defaults_to_zero():
    assert estimate_cost("some/unlisted-model", 1000, 1000) == 0.0


def test_paid_model_computes_nonzero_cost():
    cost = estimate_cost("gpt-4o-mini-example-paid", input_tokens=1000, output_tokens=1000)
    assert cost > 0.0
    assert cost == round(0.15 + 0.60, 6)
