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
