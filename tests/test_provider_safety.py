"""Item 2 — safety-verdict detection + retry-to-a-real-model."""
import httpx

from harness.models import Message
from harness.providers.openai_compat import OpenAICompatProvider, looks_like_safety_verdict


def test_detects_safety_verdicts():
    assert looks_like_safety_verdict("User Safety: safe")
    assert looks_like_safety_verdict("user safety = unsafe")
    assert looks_like_safety_verdict("safe")
    assert looks_like_safety_verdict("Safe.")


def test_ignores_real_answers():
    assert not looks_like_safety_verdict("```python\ndef f(): return 1\n```")
    assert not looks_like_safety_verdict("The function is safe to call because...")
    assert not looks_like_safety_verdict("")


def _resp(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test/model",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def test_retries_past_safety_model_until_real_answer():
    # First two calls hit the safety model, third returns real code.
    replies = iter([
        _resp("User Safety: safe"),
        _resp("User Safety: safe"),
        _resp("```python\ndef f():\n    return 1\n```"),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies)

    provider = OpenAICompatProvider(api_key="x", max_safety_retries=3)
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")

    completion = provider.complete([Message(role="user", content="solve it")])
    assert "def f()" in completion.text
    assert completion.safety_retries == 2


def test_gives_up_after_max_safety_retries_but_surfaces_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return _resp("User Safety: safe")

    provider = OpenAICompatProvider(api_key="x", max_safety_retries=2)
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")

    completion = provider.complete([Message(role="user", content="solve it")])
    assert completion.safety_retries == 2
    assert looks_like_safety_verdict(completion.text)
