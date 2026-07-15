"""P7 — cost estimation.

Every deployed provider in this repo is free (`:free` OpenRouter models,
local Ollama, or `FakeProvider`), so this always evaluates to $0.00 today.
It's computed anyway and attached to every `gen_ai.completion` span as
`gen_ai.usage.cost_usd` — "compute it even at $0" is the production habit
the plan asks for (plan §5.3), and the price table is what a paid model
slots into without touching any span-emitting code.
"""
from __future__ import annotations

# model_name -> (usd per 1K input tokens, usd per 1K output tokens).
# Free-tier and local models are exactly $0; a hypothetical paid model is
# listed to prove the table isn't hardcoded to always return zero.
_PRICE_PER_1K: dict[str, tuple[float, float]] = {
    "fake/scripted-v1": (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "gpt-4o-mini-example-paid": (0.15, 0.60),
}

_DEFAULT_PRICE = (0.0, 0.0)


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = _PRICE_PER_1K.get(model_name, _DEFAULT_PRICE)
    cost = (input_tokens / 1000) * price_in + (output_tokens / 1000) * price_out
    return round(cost, 6)
