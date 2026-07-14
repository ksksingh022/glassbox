# Glassbox

**Agent = Model + Harness.** The model is a swappable commodity behind one interface (`LLMProvider`). Everything else — instructions, tools, sandbox, verification, orchestration, tracing, UI — is the harness, built by hand in vanilla Python so the whole thing is visible instead of a black box.

This is **Phase 1**: a single agent solves a coding kata end-to-end — read problem → write code → execute in a sandbox → verify against a deterministic oracle → retry on failure → report — with every step traced live to a browser UI over SSE.

Full build plan: [`HARNESS_PLAN.md`](HARNESS_PLAN.md). Design decisions with rationale: [`DECISIONS.md`](DECISIONS.md).

## Primitives in Phase 1

| # | Primitive | File |
|---|---|---|
| P1 | Provider seam | [`harness/providers/`](harness/providers/) |
| P2 | Instructions | [`harness/instructions.py`](harness/instructions.py) |
| P3 | Tools + approval gate | [`harness/tools/`](harness/tools/) |
| P4 | Sandbox | [`harness/sandbox/executor.py`](harness/sandbox/executor.py) |
| P5 | Verification (oracle) | [`harness/verification/oracle.py`](harness/verification/oracle.py) |
| P6 | Orchestrator (loop + retry) | [`harness/orchestrator.py`](harness/orchestrator.py) |
| P7 | Tracing | [`harness/tracing/`](harness/tracing/) |
| P8 | UI (basic) | [`ui/`](ui/) |

## System design

### Component architecture

```mermaid
flowchart TD
    subgraph Browser
        UI["ui/app.js<br/>animated graph + inspector"]
    end

    UI -- "POST /solve" --> API
    API -. "GET /trace-stream/:id (SSE)" .-> UI

    subgraph "api/main.py (FastAPI)"
        API["/solve · /trace-stream/:id · /runs/:id"]
    end

    API -- "asyncio.to_thread" --> ORCH

    subgraph "harness/ (vanilla Python, no framework)"
        ORCH["Orchestrator (P6)<br/>outer attempt loop + inner tool-call loop"]
        INSTR["InstructionBuilder (P2)"]
        PROV["LLMProvider (P1)<br/>OpenAICompatProvider · FakeProvider"]
        TOOLS["6 Tools (P3)<br/>run_tests · run_code · trace_execution<br/>lookup_docs · analyze_complexity · algorithm_hint"]
        GATE["ApprovalGate (P3)"]
        SBX["SandboxExecutor (P4)<br/>SubprocessSandbox"]
        ORACLE["TestOracle (P5)"]
        TRACER["Tracer (P7)<br/>sync spans, async subscribers"]
    end

    ORCH --> INSTR
    ORCH --> PROV
    ORCH --> GATE
    GATE --> TOOLS
    TOOLS --> SBX
    TOOLS --> ORACLE
    ORCH -. "every span/event" .-> TRACER
    TRACER -. "call_soon_threadsafe" .-> API

    PROV -- "HTTPS / localhost" --> MODEL[("OpenRouter or<br/>local Ollama")]
```

**What's actually novel here** (worth leading with in an interview): the **Orchestrator is the only stateful piece**, and every primitive it calls is a thin, swappable, independently-testable class — `LLMProvider`, `SandboxExecutor`, and every `Tool` are ABCs with exactly one method each. Nothing imports a framework; `harness/` has zero dependency on FastAPI, so the entire agent logic is unit-testable (and *is* unit-tested — see `tests/`) without an HTTP server in the loop at all.

### Request lifecycle — one `/solve` call, in full

This is the sequence I'd actually draw on a whiteboard. It covers both loops: the **outer** attempt-retry loop (Decision: oracle precedence) and the **inner** tool-call loop (the newer function-calling addition).

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI (api/main.py)
    participant O as Orchestrator
    participant P as LLMProvider
    participant T as Tool (e.g. run_tests)
    participant S as Sandbox
    participant Or as Oracle
    participant Tr as Tracer

    B->>A: POST /solve {kata_id, max_attempts}
    A->>A: create Tracer(run_id), bind to event loop
    A-->>B: {run_id}  (returns immediately)
    A->>O: asyncio.to_thread(orchestrator.solve, kata)
    B->>A: GET /trace-stream/:run_id (SSE, opens in parallel)

    loop until oracle_passed or max_attempts
        O->>Tr: span "attempt" (attempt_no)
        O->>Tr: span "instructions" → messages built
        loop until model answers with no tool_calls (budget: 5)
            O->>P: complete(messages, tools=[6 schemas])
            P-->>O: Completion(text, tool_calls?)
            alt model requested a tool
                O->>T: run tool with model's arguments
                T->>S: execute in sandbox (if run_tests/run_code/trace_execution)
                S-->>T: ExecResult
                T-->>O: ToolResult
                O->>O: append assistant + tool-result messages, loop again
            else model answered with final code
                O->>O: break loop
            end
        end
        O->>Or: run_tests(final code) — authoritative, unless model's<br/>last tool call already covered this exact code
        Or-->>O: VerificationReport
        O->>Tr: attempt span closes (oracle_passed, score)
    end

    O-->>A: RunResult
    Tr-->>A: final SSE event
    A-->>B: SSE stream closes — GET /runs/:id for the full result
```

### Concurrency model — the one genuinely tricky part

`Orchestrator.solve()` is **synchronous** (blocking HTTP calls to the LLM, blocking `subprocess.run` for the sandbox) and runs inside `asyncio.to_thread(...)`, off the event loop. Meanwhile the SSE stream (`GET /trace-stream/:id`) is **async**, living on the event loop, in a *different* task than the one running the orchestrator's thread.

`Tracer` is the bridge. Every `span()`/`event()` call happens synchronously, on the orchestrator's worker thread — but the SSE subscribers are `asyncio.Queue`s that only the event loop can safely push into. `Tracer.bind_loop()` stashes a reference to the event loop at run start, and every emission goes through `loop.call_soon_threadsafe(queue.put_nowait, event)` — the one call in this codebase that's actually about thread safety rather than business logic. Skip it and the UI would either deadlock or silently drop events depending on timing.

A second, smaller wrinkle: a **new** SSE connection to an **already-finished** run still works, because `Tracer.subscribe()` replays `self._events` (the full history) before switching to live delivery — that's what let me debug a real production incident by reconnecting to a completed run's trace stream and reading back the exact payload that was sent to the LLM right before it failed, without reproducing anything.

## The harness visualizer

The UI (`ui/`) is the point of the project: the **model sits in the center** and the **seven harness primitives ring around it**. Only the provider seam (P1) touches the model — a dotted spur — which is the whole "Agent = Model + Harness" thesis made literal. Hit **Solve** and a glowing packet travels the ring; each primitive lights up as the request passes through it, the core spins while the model is "thinking," the oracle turns red on a failed attempt, and the retry loop animates the packet back to the orchestrator for the next attempt. Every edge is instrumented by the Tracer (P7) and streamed to the page over SSE. The animation is driven by real trace events but paced with a minimum dwell per stage so even a sub-100ms run is watchable, and it holds the "thinking" state for as long as a real (slow) LLM call takes.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# No API key needed — falls back to a scripted FakeProvider automatically.
uvicorn api.main:app --reload --port 8000
# open http://127.0.0.1:8000/ui/index.html
```

To watch the safety-retry visual without a live key, also set `GLASSBOX_SIMULATE_SAFETY=1` (see item 2 below).

### Using a real OpenRouter model (items 1 & 2)

**A single `OPENROUTER_API_KEY` is all you need** — copy `.env.example` to `.env`, set the key (free at openrouter.ai), and launch with uvicorn's `--env-file`:

```bash
uvicorn api.main:app --env-file .env --reload --port 8000
```

(`--env-file` works because `uvicorn[standard]` bundles python-dotenv — no `export` needed.) The only required header is `Authorization: Bearer <key>`; OpenRouter routes the request to the configured free model (`OPENROUTER_MODEL`, default `meta-llama/llama-3.3-70b-instruct:free`). The `HTTP-Referer`/`X-Title` headers we send are optional ranking metadata, not auth.

**Free-tier rate limits:** OpenRouter's `:free` models are aggressively throttled and share a per-account daily cap (roughly 50 requests/day under $10 of credits, higher above). A `429 Too Many Requests` means the key authenticated fine but you've hit that cap — wait for the reset, switch `OPENROUTER_MODEL` to another `:free` model, or add a little credit. The provider rides out brief 429s automatically (honors `Retry-After`, exponential backoff, `max_transient_retries=4`), but it can't beat a hard daily quota.

OpenRouter sometimes routes a free request to a **moderation/safety model**, which answers with a bare verdict like `User Safety: safe` instead of solving the kata. `OpenAICompatProvider` detects that response shape and **re-issues the call** (up to `max_safety_retries`, default 3) so the request lands on a real model. The number of re-routes is counted on `Completion.safety_retries`, surfaced as a `safety_retries` span attribute, and shown in both the live trace ("safety-retry ×N — re-routed past the moderation model") and the per-attempt result card. See [`harness/providers/openai_compat.py`](harness/providers/openai_compat.py) and [`tests/test_provider_safety.py`](tests/test_provider_safety.py).

(`.env` isn't auto-loaded — export the vars, or use your shell's env loader — kept dependency-light for Phase 1.)

Run tests:

```bash
pytest tests/ -v
```

## The retry story

`binary_search` is the kata most likely to need a second attempt (classic off-by-one). With `FakeProvider` this is scripted deterministically: attempt 1 uses `<` in the loop condition (misses the last element), attempt 2 fixes it to `<=`. Pick "Binary Search" in the UI and click Solve to watch it fail then pass, with the structured failure (`expected 5 got -1`) visibly fed back into attempt 2's prompt.

## Sandbox limits — honestly

`SubprocessSandbox` runs submissions as a fresh `python3` subprocess with `resource.setrlimit` for CPU time and address space, plus a hard wall-clock `timeout` that kills the process if it hangs. This is **not** a hermetic security boundary — it shares the host filesystem and kernel, and `RLIMIT_AS` isn't reliably enforced on macOS (verified in `tests/test_sandbox.py`, where the wall-clock timeout is what actually catches an unbounded-memory loop in practice on this dev machine). It's adequate for running a curated, fixed set of katas with no user-supplied problems (see Decision #2) and for demoing the "sandbox kills bad code" story — it is not adequate for running arbitrary untrusted code from the public internet unmodified. `DockerSandbox` (same `SandboxExecutor` interface, container isolation) is the intended upgrade path, deferred past Phase 1.

## Katas

Six, in [`harness/verification/katas/`](harness/verification/katas/): `reverse_string` (warm-up), `fizzbuzz_variant` (custom rules, not the classic — a model can't pattern-match its way through), `binary_search` (off-by-one trap, the scripted retry demo under `FakeProvider`), `balanced_brackets` (stack logic), `merge_intervals` (sorting + edge cases), `regex_matching` (LeetCode #10 — hard; `.`/`*` backtracking-DP boundary conditions are genuinely easy for a small local model to get wrong on the first try, so it's the best kata for watching a *real* retry against a live provider like Ollama). All oracles are pure `expected == actual` — no katas with multiple valid outputs, per plan §3.4.

## Deployment

Not yet deployed — Phase 1's exit criteria include a Railway/Vercel deploy; this session covers the local build. `api/main.py` serves both the FastAPI backend and the static UI (`/ui`) from one process, which fits a single Railway service.
