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
from harness.memory.store import AttemptStore
from harness.models import Kata, RunResult
from harness.orchestrator import Orchestrator
from harness.orchestrator_multi import MultiAgentOrchestrator
from harness.providers.base import LLMProvider
from harness.providers.fake import FakeProvider
from harness.providers.openai_compat import OpenAICompatProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.skills.loader import SkillLoader
from harness.subagents.coder import CoderSubagent
from harness.subagents.planner import PlannerSubagent
from harness.subagents.tester import TesterSubagent
from harness.tools.algorithm_hint import AlgorithmHintTool
from harness.tools.base import ApprovalGate
from harness.tools.complexity_analysis import AnalyzeComplexityTool
from harness.tools.docs_lookup import DocsLookupTool
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tools.schemas import RUN_TESTS_SCHEMA
from harness.tools.trace_execution import TraceExecutionTool
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
        # Local inference on modest hardware (especially a "thinking" model
        # emitting a long chain-of-thought) can genuinely take minutes per
        # call — a much longer read timeout than OpenRouter's cloud default,
        # overridable via OLLAMA_TIMEOUT_S.
        return OpenAICompatProvider(
            api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b-instruct"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            timeout_s=float(os.environ.get("OLLAMA_TIMEOUT_S", "300")),
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
_SKILL_LOADER = SkillLoader()
# Durable memory (P14) — a real file on disk so history survives a process
# restart, unlike `_RUNS` below. Railway's disk is ephemeral (see plan
# §5.2 / §6); this is Phase 3a scope (SQLite) only, documented in
# DECISIONS.md rather than silently limited.
_MEMORY = AttemptStore()

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


def _build_single_agent_orchestrator(tracer: Tracer, gate: ApprovalGate) -> Orchestrator:
    run_tests_tool = RunTestsTool(sandbox=_SANDBOX, oracle=_ORACLE, tracer=tracer)
    run_code_tool = RunCodeTool(sandbox=_SANDBOX, tracer=tracer)
    trace_execution_tool = TraceExecutionTool(sandbox=_SANDBOX, tracer=tracer)
    return Orchestrator(
        provider=_PROVIDER, run_tests_tool=run_tests_tool, run_code_tool=run_code_tool,
        trace_execution_tool=trace_execution_tool, lookup_docs_tool=DocsLookupTool(),
        analyze_complexity_tool=AnalyzeComplexityTool(), algorithm_hint_tool=AlgorithmHintTool(),
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )


def _build_multi_agent_orchestrator(tracer: Tracer, gate: ApprovalGate, run_id: str) -> MultiAgentOrchestrator:
    # The Coder gets the same run_tests tool as the single-agent orchestrator
    # (so its ToolCallLoop can self-check mid-attempt) but oracle precedence
    # (Decision #5) is enforced by the Tester's own authoritative run_tests
    # call, not by the Coder's optional one.
    run_tests_tool = RunTestsTool(sandbox=_SANDBOX, oracle=_ORACLE, tracer=tracer)
    run_code_tool = RunCodeTool(sandbox=_SANDBOX, tracer=tracer)
    registry = {"run_tests": (run_tests_tool, RUN_TESTS_SCHEMA)}
    return MultiAgentOrchestrator(
        planner=PlannerSubagent(provider=_PROVIDER, tracer=tracer),
        coder=CoderSubagent(provider=_PROVIDER, registry=registry, tracer=tracer, approval_gate=gate),
        tester=TesterSubagent(
            provider=_PROVIDER, run_tests_tool=run_tests_tool, run_code_tool=run_code_tool,
            tracer=tracer, approval_gate=gate,
        ),
        tracer=tracer, skill_loader=_SKILL_LOADER, memory=_MEMORY, session_id=run_id,
    )


def _record_single_agent_attempts(kata: Kata, run_id: str, result: RunResult) -> None:
    for record in result.attempts:
        failure_reason = None
        if not record.report.oracle_passed and record.report.failed_cases:
            fc = record.report.failed_cases[0]
            failure_reason = fc.error or f"input={fc.input!r} expected={fc.expected!r} got={fc.actual!r}"
        _MEMORY.record_attempt(
            kata_id=kata.id, kata_category=kata.category, session_id=run_id,
            attempt_no=record.attempt_no, passed=record.report.oracle_passed,
            duration_ms=0.0,
            tokens_used=record.completion.input_tokens + record.completion.output_tokens,
            trace_id=run_id, failure_reason=failure_reason,
        )


@app.post("/solve")
async def solve(request: Request):
    body = await request.json()
    kata_id = body.get("kata_id")
    max_attempts = int(body.get("max_attempts", 3))
    mode = body.get("mode", "single")
    if mode not in ("single", "multi"):
        raise HTTPException(status_code=400, detail="mode must be 'single' or 'multi'")
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
    orchestrator = (
        _build_multi_agent_orchestrator(tracer, gate, run_id)
        if mode == "multi" else _build_single_agent_orchestrator(tracer, gate)
    )

    async def _run() -> None:
        entry = _RUNS[run_id]
        try:
            result = await asyncio.to_thread(orchestrator.solve, kata, max_attempts)
            entry.result = result
            # MultiAgentOrchestrator records each attempt to memory itself
            # (it needs the recall hint mid-run); the single-agent path has
            # no such need internally, so it's recorded here instead, once
            # the run is done — still "every attempt, from either
            # orchestrator, is recorded" (plan §5.2).
            if mode == "single":
                _record_single_agent_attempts(kata, run_id, result)
        except Exception as exc:  # noqa: BLE001 — surfaced to the client, not swallowed
            entry.error = str(exc)
            tracer.finish()

    task = asyncio.create_task(_run())
    _RUNS[run_id] = _RunEntry(tracer=tracer, task=task)

    return {"run_id": run_id, "kata_id": kata_id, "mode": mode}


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


@app.get("/history")
def get_history(limit: int = 50):
    return _MEMORY.recent(limit=limit)


@app.get("/stats")
def get_stats():
    return _MEMORY.stats()


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)
