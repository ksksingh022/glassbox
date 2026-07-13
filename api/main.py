"""FastAPI harness service.

`POST /solve` kicks off a run in a background thread (the provider call is
blocking) and returns immediately with a `run_id`. `GET /trace-stream/{id}`
streams that run's trace events live over SSE — this is what makes the UI's
architecture graph light up node-by-node instead of just showing a final
result. `GET /runs/{id}` polls for the final `RunResult`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from harness.instructions import InstructionBuilder
from harness.models import Kata, RunResult
from harness.orchestrator import Orchestrator
from harness.providers.base import LLMProvider
from harness.providers.fake import FakeProvider
from harness.providers.openai_compat import OpenAICompatProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.base import ApprovalGate
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load, load_all
from harness.verification.oracle import TestOracle

app = FastAPI(title="Glassbox Harness API")

_UI_DIR = Path(__file__).parent.parent / "ui"
if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.get("/")
def root():
    return RedirectResponse(url="/ui/index.html")

# ---------------------------------------------------------------------------
# Provider selection (Decision #3: one OpenAICompatProvider covers OpenRouter
# and friends via config; falls back to the deterministic FakeProvider so the
# demo works without an API key).
# ---------------------------------------------------------------------------


def _build_provider() -> LLMProvider:
    selected = os.environ.get("LLM_PROVIDER", "").strip().lower()

    if selected == "fake":
        return FakeProvider()

    if selected == "ollama":
        # Ollama serves an OpenAI-compatible endpoint locally — same
        # OpenAICompatProvider, just pointed at localhost with no real key.
        # This is the second, genuinely non-OpenRouter wire target proving
        # the provider seam is a real seam (Decision #3).
        return OpenAICompatProvider(
            api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b-instruct"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        )

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return FakeProvider()
    return OpenAICompatProvider(
        api_key=api_key,
        model=os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


_PROVIDER = _build_provider()
_SANDBOX = SubprocessSandbox()
_ORACLE = TestOracle()

# ---------------------------------------------------------------------------
# In-memory run registry (Railway disk/process is ephemeral in Phase 1 —
# durable state is Phase 3's P14).
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _RunEntry:
    tracer: Tracer
    task: asyncio.Task
    result: RunResult | None = None
    error: str | None = None


_RUNS: dict[str, _RunEntry] = {}

# ---------------------------------------------------------------------------
# Rate limiting (Decision #2): fixed kata set only, no user-supplied
# problems, plus a per-IP and global cap to protect the free LLM quota.
# ---------------------------------------------------------------------------

_PER_IP_LIMIT = 5
_PER_IP_WINDOW_S = 10 * 60
_GLOBAL_DAILY_LIMIT = 200

_ip_hits: dict[str, deque] = defaultdict(deque)
_global_hits: deque = deque()


_LOCAL_IPS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _check_rate_limit(ip: str) -> None:
    # The limit exists to protect a public deployment's free-tier LLM quota
    # (Decision #2) — it has no purpose against yourself on localhost, where
    # it just gets in the way of iterating. Real external IPs are still
    # gated exactly as designed.
    if ip in _LOCAL_IPS:
        return

    now = time.time()

    hits = _ip_hits[ip]
    while hits and now - hits[0] > _PER_IP_WINDOW_S:
        hits.popleft()
    if len(hits) >= _PER_IP_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit: too many runs from this IP, try again later")

    while _global_hits and now - _global_hits[0] > 86400:
        _global_hits.popleft()
    if len(_global_hits) >= _GLOBAL_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit: global daily cap reached")

    hits.append(now)
    _global_hits.append(now)


def _kata_summary(kata: Kata) -> dict:
    return {
        "id": kata.id, "title": kata.title, "category": kata.category,
        "difficulty": kata.difficulty, "test_case_count": len(kata.test_cases),
    }


@app.get("/katas")
def list_katas():
    return [_kata_summary(k) for k in load_all().values()]


@app.post("/solve")
async def solve(request: Request):
    body = await request.json()
    kata_id = body.get("kata_id")
    max_attempts = int(body.get("max_attempts", 3))
    if not kata_id:
        raise HTTPException(status_code=400, detail="kata_id is required")

    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    try:
        kata = load(kata_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown kata_id: {kata_id}")

    run_id = uuid.uuid4().hex
    tracer = Tracer(run_id=run_id)
    tracer.bind_loop(asyncio.get_event_loop())

    gate = ApprovalGate(tracer=tracer, policy=os.environ.get("APPROVAL_POLICY", "auto"))
    run_tests_tool = RunTestsTool(sandbox=_SANDBOX, oracle=_ORACLE, tracer=tracer)
    orchestrator = Orchestrator(
        provider=_PROVIDER, run_tests_tool=run_tests_tool, approval_gate=gate,
        tracer=tracer, instruction_builder=InstructionBuilder(),
    )

    async def _run() -> None:
        entry = _RUNS[run_id]
        try:
            result = await asyncio.to_thread(orchestrator.solve, kata, max_attempts)
            entry.result = result
        except Exception as exc:  # noqa: BLE001 — surfaced to the client, not swallowed
            entry.error = str(exc)
            tracer.finish()

    task = asyncio.create_task(_run())
    _RUNS[run_id] = _RunEntry(tracer=tracer, task=task)

    return {"run_id": run_id, "kata_id": kata_id}


@app.get("/trace-stream/{run_id}")
async def trace_stream(run_id: str):
    entry = _RUNS.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown run_id")

    async def event_gen():
        async for event in entry.tracer.subscribe():
            yield f"data: {_json(event.to_dict())}\n\n"
        final = {"final": True, "error": entry.error}
        yield f"data: {_json(final)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    entry = _RUNS.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    if entry.error:
        return {"status": "error", "error": entry.error}
    if entry.result is None:
        return {"status": "running"}
    return {"status": "done", "result": dataclasses.asdict(entry.result)}


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)
