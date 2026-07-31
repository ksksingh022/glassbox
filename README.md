# Glassbox

**Agent = Model + Harness.** The model is a swappable commodity behind one interface (`LLMProvider`). Everything else — instructions, tools, sandbox, verification, orchestration, tracing, UI — is the harness, built by hand in vanilla Python so the whole thing is visible instead of a black box.

Give it a real problem — `leetcode 295`, a LeetCode URL, or pasted problem text — and the harness fetches it, works out what "correct" and "fast enough" mean for that problem, then solves it. All 19 primitives from [`spec/HARNESS_PLAN.md`](spec/HARNESS_PLAN.md) are implemented, and every step is traced live to a browser UI over SSE.

```
"leetcode 295"
  ↓  P15 fetch          LeetCode GraphQL / pasted text / curated kata
  ↓  P16 extract        statement → entry point, constraints, ground-truth cases
  ↓  P17 budget         "n ≤ 5·10⁴ → target O(n log n); O(n²) will time out"
  ↓  P18 test-gen       ≤10 extra edge/happy cases that respect the constraints
  ↓
 Planner → Coder → Tester ──→ Oracle
     ↑                          ↓
     └──── retry w/ feedback ───┤
                                ↓  P19 judge (only on a generated-case disagreement)
                          bad test? real bug? unclear?
```

**The claim this is built to demonstrate:** a small quantized local model can land a LeetCode Hard *because of* the scaffolding around it — the harness works out the acceptable complexity from the constraints and tells the model up front, generates edge cases the published examples don't cover, catches the mistake, and hands back a structured diff to try again. The UI is built to make that legible: the question, the derived oracle, the loop structure, and the exact cost in time, tokens and dollars.

Full build plan: [`spec/HARNESS_PLAN.md`](spec/HARNESS_PLAN.md). Design decisions with rationale: [`DECISIONS.md`](DECISIONS.md).

## All 19 primitives

| # | Primitive | Phase | File |
|---|---|---|---|
| P1 | Provider seam | 1 | [`harness/providers/`](harness/providers/) |
| P2 | Instructions | 1 | [`harness/instructions.py`](harness/instructions.py) |
| P3 | Tools + approval gate | 1 | [`harness/tools/`](harness/tools/) |
| P4 | Sandbox | 1 | [`harness/sandbox/executor.py`](harness/sandbox/executor.py) |
| P5 | Verification (oracle) | 1 | [`harness/verification/oracle.py`](harness/verification/oracle.py) |
| P6 | Orchestration (single- and multi-agent) | 1 / 2 | [`harness/orchestrator.py`](harness/orchestrator.py), [`harness/orchestrator_multi.py`](harness/orchestrator_multi.py) |
| P7 | Tracing (full OTel-flavored conformance + cost) | 1 / 3 | [`harness/tracing/`](harness/tracing/), [`harness/costs.py`](harness/costs.py) |
| P8 | UI (a fixed flowchart with real loop-back edges + a narrated story feed + History dashboard) | 1 / 3 | [`ui/`](ui/) |
| P9 | History (per-session attempt state) | 2 | [`harness/context/history.py`](harness/context/history.py) |
| P10 | Context delivery (structured failure feedback) | 2 | [`harness/context/failure_formatter.py`](harness/context/failure_formatter.py) |
| P11 | Context management (trimming/compaction) | 2 | [`harness/context/context_manager.py`](harness/context/context_manager.py) |
| P12 | Subagents (planner / coder / tester) | 2 | [`harness/subagents/`](harness/subagents/), [`harness/tool_loop.py`](harness/tool_loop.py) |
| P13 | Skills (on-demand SKILL.md loading) | 3 | [`skills/`](skills/), [`harness/skills/loader.py`](harness/skills/loader.py) |
| P14 | Durable state + memory (cross-session attempt log + recall) | 3 | [`harness/memory/store.py`](harness/memory/store.py) |
| P15 | Problem fetching (LeetCode / pasted / curated) | 4 | [`harness/problems/`](harness/problems/), [`harness/tools/fetch_problem.py`](harness/tools/fetch_problem.py) |
| P16 | Extraction (statement → runnable spec + oracle) | 4 | [`harness/subagents/extractor.py`](harness/subagents/extractor.py) |
| P17 | Complexity budget (constraints → acceptable Big-O) | 4 | [`harness/complexity_budget.py`](harness/complexity_budget.py) |
| P18 | Test-case generation (constraint-aware edge cases) | 4 | [`harness/subagents/testgen.py`](harness/subagents/testgen.py) |
| P19 | Judge (adjudicates generated-case disagreements) | 4 | [`harness/subagents/judge.py`](harness/subagents/judge.py) |

`POST /solve {problem_ref, mode: "single" | "multi"}` picks which orchestrator runs — see [DECISIONS.md #9](DECISIONS.md) for why both exist rather than one replacing the other. `GET /problem?ref=…` runs fetch+extract+budget alone, so the UI can show the question and its derived oracle before solving starts.

### How "correct" and "fast enough" get decided

The interesting part of a dynamic-problem harness isn't the solving — it's that the harness has to **bootstrap its own oracle from an untrusted problem statement**, and then be honest about how much it trusts what it derived.

- **Ground truth comes from the statement, parsed mechanically.** LeetCode publishes the exact signature (its python3 starter snippet) and the correct answer for every worked example. Both are parsed deterministically — for live problems 1, 20, 42 and 295 the entire extraction runs with **no model call**. The LLM is a fallback for pasted/unusual problems, and which route ran is shown in the UI, because "parsed from the statement" and "written by the model" are different trust levels.
- **Complexity is a table, not an opinion.** `n ≤ 5·10⁴` → target `O(n log n)`, `O(n²)` will time out. Derived deterministically and injected into the Coder's prompt, then cross-checked against the `analyze_complexity` tool.
- **Generated tests can fail a run, but never on a guess.** The test-gen agent invents ~10 extra constraint-respecting cases, but it doesn't *know* their answers. So a disagreement goes to the Judge, which rules `bad_test` (discard), `real_bug` (retry), or `uncertain` — and defaults to `uncertain` on any silence or garbage. A hallucinated test gets thrown out instead of failing correct code. Full ladder in [DECISIONS.md #23–25](DECISIONS.md).

### Multi-agent flow (P12)

```mermaid
flowchart LR
    O["Orchestrator<br/>(attempt-retry loop)"] --> PL["Planner<br/>problem → step plan, no code"]
    PL --> CO["Coder<br/>plan + skills → code<br/>(ToolCallLoop, same 6 tools)"]
    CO --> TE["Tester<br/>oracle (authoritative) +<br/>edge cases (advisory)"]
    TE -- "pass" --> DONE(("done"))
    TE -- "fail (< 2x)" --> CO
    TE -- "fail (2x in a row)" --> PL
```

Each subagent call is a `tracer.span("subagent", ...)` carrying only a **distilled** `SubagentResult` (plan steps / code / verdict) back to the orchestrator — never the raw message transcript it used internally (see DECISIONS.md #9-#12 and the plan's explicit "#1 thing people get wrong with subagents" warning).

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

    B->>A: POST /solve {problem_ref, max_attempts}
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

The UI (`ui/`) is the point of the project: **one fixed flowchart, drawn once per run**, not redrawn per attempt. Two earlier designs both got this wrong — a ring around a central model core made every attempt retrace the same positions (no way to tell which retry was live), and a "one row per attempt" list flattened the loop structure into repeated boxes instead of showing it as a loop. The current design draws the harness as a real flowchart with real loop-back edges: a purple arrow carries the packet from Oracle back up to the top on a retry, a blue arrow carries it from Tester straight back to Coder on a same-plan retry (no replan), and an amber arrow carries it from wherever the model is being asked again (a tool-calling round, the Tester's edge-case sweep) back up to Provider. Each loop edge has a live counter badge (`retry ×2`, `same-plan retry ×1`, `model asked again ×3`) instead of a separate node per occurrence. Alongside the flowchart, the **Story** panel narrates the run in plain English ("Retrying — attempt 2, with the previous failure fed back into the prompt", "Tester ran the official oracle — every case passed") instead of a raw event log — the goal is that a stranger can watch a small quantized model solve a hard problem and understand *why* it worked: which mistake the harness caught, what feedback it gave, and how many tries it took. The narration is grouped by attempt (current expanded, past collapsed), and the full raw trace is still one click away per attempt via a "show raw trace" toggle. Every node/edge/story-line is instrumented by the Tracer (P7) and streamed to the page over SSE; click-to-inspect on any node shows every real call to that primitive across the whole run, most recent first, each labeled with which attempt it happened in.

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

In the UI you type a problem reference (or click a quick-pick). Four panels then tell the whole story:

- **Problem** — the fetched statement, its constraints, the **derived complexity budget**, and every test case labelled `truth` vs `gen` with the generator's one-line rationale, so it's obvious what the agent is being graded on and how much that grading is trusted.
- **Harness** — the flowchart. Fetch→Extract→Budget→Test-gen run once as a prelude; the solving loop follows, with retries and tool-loops drawn as real loop-back arrows carrying live counters.
- **Cost of solving** — wall time, attempts, tokens in/out, dollar cost, and the **model-vs-sandbox-vs-harness time split**, plus a per-agent token table (planner/coder/tester/testgen/judge).
- **Story** — the run narrated in plain English, grouped per attempt, with the raw trace one click away.

The **mode selector** redraws the solving half of the flowchart: `single agent` uses Orchestrator→Instructions, `multi agent` uses Planner→Coder→…→Tester→Judge. Provider/Approval/Tools/Sandbox/Oracle are shared substrate in both. The **History** tab reads `GET /stats` + `GET /history`, backed by the SQLite store at `data/glassbox.db` (gitignored — created on first run).

## The retry story

`binary_search` is the kata most likely to need a second attempt (classic off-by-one). With `FakeProvider` this is scripted deterministically: attempt 1 uses `<` in the loop condition (misses the last element), attempt 2 fixes it to `<=`. Pick "Binary Search" in the UI and click Solve to watch it fail then pass, with the structured failure (`expected 5 got -1`) visibly fed back into attempt 2's prompt.

## Sandbox limits — honestly

`SubprocessSandbox` runs submissions as a fresh `python3` subprocess with `resource.setrlimit` for CPU time and address space, a hard wall-clock `timeout` that kills the process if it hangs, and a prepended guard that neuters `socket` so submissions can't make network calls.

This is **not** a hermetic security boundary, and the details matter:

- It shares the host filesystem and kernel.
- `RLIMIT_AS` isn't reliably enforced on macOS — the wall-clock timeout is what actually catches an unbounded-memory loop there (verified in `tests/test_sandbox.py`).
- The network guard is a **guardrail, not a boundary**. It blocks accidental and casual network use by in-process Python; code that deliberately wants out could reimport the C module or spawn a subprocess.

**A correction worth recording:** through Phases 1–3 this README and `DECISIONS.md` both claimed the sandbox had "no network", on the strength of `env={"PATH": ...}`. That was simply wrong — clearing the environment strips inherited *proxy config*, not outbound sockets. It was caught in Phase 4 by actually testing it: a `urllib` call from inside the sandbox reached leetcode.com and returned a 403. Hence the guard, the honest scoping above, and the regression tests. `DockerSandbox` with `--network none` (same `SandboxExecutor` interface, real container isolation) remains the intended upgrade path.

Phase 4 also widened the input surface — problems now come from the public internet — so `LeetCodeSource` enforces a host allowlist (`leetcode.com` only) on every URL it will touch, tested against cloud-metadata and localhost addresses.

## Problem input

Anything the fetcher recognizes:

| input | resolves via |
|---|---|
| `295`, `leetcode 295`, `lc 295` | LeetCode number → slug (cached index) |
| `find-median-from-data-stream` | LeetCode slug |
| `https://leetcode.com/problems/two-sum/` | LeetCode URL (host-allowlisted) |
| a pasted problem statement | `PastedTextSource` — always works, no network |
| `binary_search`, `merge_intervals`, … | the 7 curated katas, fully offline |

The curated katas are kept deliberately: they're the only problems that run with **no network and no API key** (the `FakeProvider` demo), and they're what most of the test suite exercises. `binary_search` is scripted to fail attempt 1 and pass attempt 2, which is how the retry story stays demoable offline.

## Deployment

Not yet deployed — the plan's exit criteria for both phases include a Railway/Vercel deploy; this covers the local build for Phase 1 through Phase 3, verified end-to-end in a browser preview (single-agent run, multi-agent run with the escalation/skill/memory story, and the History dashboard). `api/main.py` serves both the FastAPI backend and the static UI (`/ui`) from one process, which fits a single Railway service. Durable memory (P14) is SQLite on local disk — Railway's disk is ephemeral, so a real deploy needs the Postgres/Supabase swap noted as explicitly out of scope in [DECISIONS.md #15](DECISIONS.md).

## What's honestly reduced scope in this pass

- **Deployment.** Everything above runs and is verified locally; no Railway/Vercel/Supabase deploy yet (see above).
- **P14 is SQLite only** (Phase 3a). Postgres/Supabase (3b) isn't done.
- **Skill/memory events have no dedicated flowchart node.** `skill_loaded`, `memory_write`, and `memory_recall` (P13/P14) narrate in the Story panel with full attrs on click, same as every other event, but don't get their own animated node the way Planner/Coder/Tester do.
- **LeetCode has no public API contract.** The GraphQL endpoint used by `LeetCodeSource` is undocumented and can change or start blocking at any time. That's the genuinely fragile part of this design — it's why `ProblemSource` is a strategy with `PastedTextSource` as an always-works fallback, and why the problem index is cached to disk. Premium-only problems return no content and fail with a clear message rather than a confusing empty oracle.
- **The Judge is itself an LLM and can be wrong.** It's bounded — never consulted on ground-truth cases, defaults to `uncertain` on silence or garbage — but a confidently wrong `real_bug` verdict would still cause a spurious retry. Every verdict is traced and rendered so it's auditable rather than invisible.
- **Extraction quality gates everything downstream.** A bad extraction means a bad oracle. That's why the deterministic path is preferred, why the route taken is labelled in the UI, and why the extracted cases are shown rather than kept internal.
