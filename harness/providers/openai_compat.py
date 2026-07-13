"""OpenAI-compatible chat-completions provider.

OpenRouter, Groq, Together, DeepSeek, and Gemini's OpenAI-compat endpoint
all speak the same chat-completions wire dialect, so one class covers all
of them via configuration alone (Decision #3). Default config points at an
OpenRouter `:free` model.

Item 1 (verified): a single `OPENROUTER_API_KEY` is all that's needed. The
only required header is `Authorization: Bearer <key>`; OpenRouter routes the
request to the configured free model. The optional `HTTP-Referer` / `X-Title`
headers below are purely for OpenRouter's app-ranking board and are not
required for the call to succeed.

Item 2: OpenRouter sometimes routes a request to a moderation/safety model,
which answers with a bare verdict like `User Safety: safe` instead of solving
the kata. We detect that shape and re-issue the call so it lands on a real
model, counting the retries so the harness can surface them in the trace.
"""
from __future__ import annotations

import re
import time

import httpx

from harness.models import Completion, Message
from harness.providers.base import LLMProvider

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

# HTTP statuses worth a transient retry (rate limit / upstream hiccups).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# A safety/moderation verdict looks like "User Safety: safe" (or unsafe /
# flagged / a bare "safe"). It is never a valid kata solution, so treat it
# as a signal to re-route to a real model.
_SAFETY_PREFIX = re.compile(
    r"^\s*user\s+safety\s*[:=]\s*(safe|unsafe|flagged|violation|blocked)\b",
    re.IGNORECASE,
)
_BARE_VERDICTS = {"safe", "unsafe", "user safety: safe", "user safety safe"}


def looks_like_safety_verdict(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _SAFETY_PREFIX.match(t):
        return True
    normalized = t.lower().rstrip(".").strip()
    return len(t) <= 24 and normalized in _BARE_VERDICTS


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 60.0,
        max_safety_retries: int = 3,
        max_transient_retries: int = 4,
        max_backoff_s: float = 8.0,
        referer: str = "https://github.com/glassbox-harness",
        title: str = "Glassbox Harness",
    ):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_safety_retries = max_safety_retries
        self._max_transient_retries = max_transient_retries
        self._max_backoff_s = max_backoff_s
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                # Optional OpenRouter ranking headers — safe to send, not required.
                "HTTP-Referer": referer,
                "X-Title": title,
            },
            timeout=timeout_s,
        )

    def complete(self, messages: list[Message], **kwargs) -> Completion:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": kwargs.get("temperature", 0.2),
        }

        start = time.monotonic()
        safety_retries = 0
        transient_retries = 0
        # Generous overall cap so a pathological mix of transient + safety
        # responses can't spin forever.
        max_iters = self._max_safety_retries + self._max_transient_retries + 2

        for _ in range(max_iters):
            try:
                resp = self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError:
                if transient_retries < self._max_transient_retries:
                    transient_retries += 1
                    time.sleep(0.5 * transient_retries)
                    continue
                raise

            if resp.status_code in _RETRYABLE_STATUS and transient_retries < self._max_transient_retries:
                transient_retries += 1
                # Free-tier 429s are common; honor Retry-After when present,
                # otherwise exponential backoff, capped so /solve can't hang.
                backoff = min(0.5 * (2 ** transient_retries), self._max_backoff_s)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        backoff = min(float(retry_after), self._max_backoff_s)
                    except ValueError:
                        pass
                time.sleep(backoff)
                continue

            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"].get("content") or ""

            if looks_like_safety_verdict(text) and safety_retries < self._max_safety_retries:
                # Routed to the safety model — re-issue so a real model answers.
                safety_retries += 1
                time.sleep(0.2)
                continue

            usage = data.get("usage", {})
            latency_ms = (time.monotonic() - start) * 1000
            return Completion(
                text=text,
                model_name=data.get("model", self._model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                latency_ms=latency_ms,
                safety_retries=safety_retries,
            )

        # Retries exhausted (kept hitting the safety model). Return the last
        # verdict so the failure is visible in the trace rather than silently
        # swallowed — the oracle will fail the attempt.
        latency_ms = (time.monotonic() - start) * 1000
        return Completion(
            text=text,
            model_name=data.get("model", self._model),
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
            latency_ms=latency_ms,
            safety_retries=safety_retries,
        )
