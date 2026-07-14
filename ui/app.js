"use strict";
const SVGNS = "http://www.w3.org/2000/svg";

// ---------------------------------------------------------------------------
// Layout — the harness ring wraps around the model core at (500,340).
// ---------------------------------------------------------------------------
const CENTER = { x: 500, y: 340 };
const MODEL_R = 72;

const NODES = {
  orchestrator: { x: 500, y: 78,  title: "Orchestrator",  tag: "loop + retry", p: "P6" },
  instructions: { x: 830, y: 168, title: "Instructions",  tag: "prompt build", p: "P2" },
  provider:     { x: 908, y: 340, title: "Provider seam", tag: "LLM client",   p: "P1" },
  approval:     { x: 830, y: 512, title: "Approval gate", tag: "run_tests",    p: "P3" },
  tools:        { x: 500, y: 602, title: "Tools",         tag: "run_tests",    p: "P3" },
  sandbox:      { x: 170, y: 512, title: "Sandbox",       tag: "subprocess",   p: "P4" },
  oracle:       { x: 92,  y: 340, title: "Oracle",        tag: "verify",       p: "P5" },
};
const NW = 152, NH = 60;

// Ring segments (clockwise) + the provider→model spur + the outer retry arc
// (a new attempt) + the inner tool-loop arc (the model calling another tool
// within the SAME attempt, without a fresh Instructions build).
const SEGMENTS = [
  ["orchestrator", "instructions"],
  ["instructions", "provider"],
  ["provider", "approval"],
  ["approval", "tools"],
  ["tools", "sandbox"],
  ["sandbox", "oracle"],
  ["oracle", "orchestrator", "retry"],
  ["provider", "model", "spur"],
  ["tools", "provider", "toolloop"],
];

const conns = {}; // "a->b" -> { el, p0, p1, p2 }
const nodeEls = {};

function nodeCenter(id) {
  if (id === "model") return { x: CENTER.x, y: CENTER.y };
  return { x: NODES[id].x, y: NODES[id].y };
}

// Control point: bow the segment outward from the center for a ring look.
// The tool-loop arc bows INWARD instead (negative), so it visually reads as
// a shortcut through the inside of the ring, distinct from the outer retry arc.
function controlPoint(a, b, kind) {
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const dir = { x: mid.x - CENTER.x, y: mid.y - CENTER.y };
  const len = Math.hypot(dir.x, dir.y) || 1;
  const bow = kind === "retry" ? 78 : kind === "spur" ? 0 : kind === "toolloop" ? -55 : 46;
  return { x: mid.x + (dir.x / len) * bow, y: mid.y + (dir.y / len) * bow };
}

function qbez(p0, p1, p2, t) {
  const u = 1 - t;
  return {
    x: u * u * p0.x + 2 * u * t * p1.x + t * t * p2.x,
    y: u * u * p0.y + 2 * u * t * p1.y + t * t * p2.y,
  };
}

// ---------------------------------------------------------------------------
// Build the board
// ---------------------------------------------------------------------------
function buildBoard() {
  const connG = document.getElementById("conns");
  for (const [from, to, kind] of SEGMENTS) {
    let a = nodeCenter(from), b = nodeCenter(to);
    // For the spur, stop at the model's edge instead of its center.
    if (kind === "spur") {
      const dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy);
      b = { x: b.x + (dx / d) * MODEL_R, y: b.y + (dy / d) * MODEL_R };
    }
    const p1 = controlPoint(a, b, kind);
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", `M ${a.x} ${a.y} Q ${p1.x} ${p1.y} ${b.x} ${b.y}`);
    path.setAttribute("class", "conn" + (kind ? " " + kind : ""));
    connG.appendChild(path);
    conns[`${from}->${to}`] = { el: path, p0: a, p1, p2: b };
  }

  const nodesG = document.getElementById("nodes");
  for (const [id, n] of Object.entries(NODES)) {
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "node idle");
    g.dataset.id = id;
    const x = n.x - NW / 2, y = n.y - NH / 2;
    g.innerHTML = `
      <rect class="box" x="${x}" y="${y}" width="${NW}" height="${NH}" rx="12"></rect>
      <text class="pnum" x="${x + 12}" y="${y + 18}">${n.p}</text>
      <text class="title" x="${n.x}" y="${n.y + 2}" text-anchor="middle">${n.title}</text>
      <text class="tag" x="${n.x}" y="${n.y + 18}" text-anchor="middle">${n.tag}</text>`;
    nodesG.appendChild(g);
    nodeEls[id] = g;
    g.addEventListener("click", () => openInspector(id));
  }
}

// ---------------------------------------------------------------------------
// Primitive animations
// ---------------------------------------------------------------------------
const packet = document.getElementById("packet");
const modelEl = document.getElementById("model");
const easeInOut = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
const dwell = (ms) => new Promise((r) => setTimeout(r, ms));

function tween(dur, onUpdate) {
  // Drive with rAF for smoothness, but also arm a timer each step: if the
  // tab is backgrounded (rAF throttled or paused), the timer still advances
  // the tween to completion so the flow never stalls mid-travel.
  return new Promise((res) => {
    const t0 = performance.now();
    let done = false, raf = 0, timer = 0;
    function step() {
      if (done) return;
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      const p = Math.min(1, (performance.now() - t0) / dur);
      onUpdate(easeInOut(p));
      if (p < 1) {
        raf = requestAnimationFrame(step);
        timer = setTimeout(step, 100);
      } else {
        done = true;
        res();
      }
    }
    step();
  });
}

function setPacketPos(pt) {
  packet.setAttribute("transform", `translate(${pt.x} ${pt.y})`);
}
function showPacketAt(id) {
  packet.classList.remove("hidden");
  setPacketPos(nodeCenter(id));
}
function setNode(id, state) {
  const el = nodeEls[id];
  if (!el) return;
  el.classList.remove("idle", "active", "exec", "done", "warn");
  el.classList.add(state);
}
function resetRing() {
  for (const id of Object.keys(NODES)) setNode(id, "idle");
}

async function travel(from, to, opts = {}) {
  let conn = conns[`${from}->${to}`];
  let reverse = false;
  if (!conn) { conn = conns[`${to}->${from}`]; reverse = true; }
  if (!conn) { setPacketPos(nodeCenter(to)); return; }

  const isRetry = conn.el.classList.contains("retry");
  const isToolLoop = conn.el.classList.contains("toolloop");
  packet.classList.toggle("retry", isRetry);
  packet.classList.toggle("toolloop", isToolLoop);
  conn.el.classList.add("flowing");
  await tween(opts.dur || 470, (t) => {
    const tt = reverse ? 1 - t : t;
    setPacketPos(qbez(conn.p0, conn.p1, conn.p2, tt));
  });
  conn.el.classList.remove("flowing");
  conn.el.classList.add("traveled");
  packet.classList.remove("retry", "toolloop");
}

function modelThink(on) { modelEl.classList.toggle("think", on); }
function setModelInfo(name, stat) {
  if (name) document.getElementById("model-name").textContent = name;
  if (stat !== undefined) document.getElementById("model-stat").textContent = stat;
}

// ---------------------------------------------------------------------------
// Async action queue — SSE events enqueue actions; one consumer plays them
// in order, so instant events still animate smoothly (min dwell per stage)
// and a slow real LLM call simply holds the "thinking" state until its ok.
// ---------------------------------------------------------------------------
let queue = [], waiter = null, runToken = 0;

// Tool-call round tracking — an "attempt" (Python's outer retry loop) can
// now contain several inner rounds of Provider->Tools before it finalizes
// (the model deciding to call run_tests/trace_execution/etc. on its own).
// Without this, every round looked identical to a fresh attempt, which is
// what made the graph look like it was looping uncontrollably.
let toolRoundInAttempt = 0;
let lastToolModelInitiated = false;

function pushAction(fn) {
  queue.push(fn);
  if (waiter) { const w = waiter; waiter = null; w(); }
}
function nextAction() {
  if (queue.length) return Promise.resolve(queue.shift());
  return new Promise((res) => { waiter = () => res(queue.shift()); });
}
async function runConsumer(myToken) {
  while (myToken === runToken) {
    const fn = await nextAction();
    if (myToken !== runToken) return;
    try { await fn(); } catch (e) { console.error(e); }
  }
}

// ---------------------------------------------------------------------------
// Inspector: click a node to see that primitive's real input/output for the
// current run. Every trace event (with full attrs) is kept in memory as it
// streams in; clicking a node filters to that primitive's spans, grouped by
// span_id (a span's "started" event = input, its "ok"/"error" event = output).
// ---------------------------------------------------------------------------
let currentRunEvents = [];

const KIND_TO_NODE = {
  instructions: "instructions",
  "gen_ai.completion": "provider",
  approval_gate: "approval",
  "gen_ai.tool": "tools",
  sandbox_exec: "sandbox",
  verification: "oracle",
  orchestrator: "orchestrator",
  run: "orchestrator",
};

function fmtValue(key, val) {
  if (val == null) return `<span class="v-null">—</span>`;
  if (key === "messages" && Array.isArray(val)) {
    return val.map((m) =>
      `<div class="msg"><div class="msg-role">${escapeHtml(m.role)}</div><pre>${escapeHtml(m.content)}</pre></div>`
    ).join("");
  }
  if (key === "failed_cases" && Array.isArray(val)) {
    if (!val.length) return `<span class="v-null">none</span>`;
    return val.map((fc) => {
      const detail = fc.error
        ? `raised ${escapeHtml(fc.error)}`
        : `expected ${escapeHtml(JSON.stringify(fc.expected))} got ${escapeHtml(JSON.stringify(fc.actual))}`;
      return `<div class="fc-row">input=${escapeHtml(JSON.stringify(fc.input))} — ${detail}</div>`;
    }).join("");
  }
  if (["code", "script", "response_text", "stdout", "stderr", "kata_prompt"].includes(key) && typeof val === "string") {
    return `<pre>${escapeHtml(val) || "(empty)"}</pre>`;
  }
  if (typeof val === "string") {
    if (val.length > 100 || val.includes("\n")) return `<pre>${escapeHtml(val)}</pre>`;
    return escapeHtml(val);
  }
  if (typeof val === "object") return `<pre>${escapeHtml(JSON.stringify(val, null, 2))}</pre>`;
  return escapeHtml(String(val));
}

function fmtAttrs(attrs, skipKeys) {
  const entries = Object.entries(attrs || {}).filter(([k]) => !skipKeys || !skipKeys.has(k));
  if (!entries.length) return `<div class="kv-empty">none</div>`;
  return entries.map(([k, v]) =>
    `<div class="kv"><div class="kv-k">${escapeHtml(k)}</div><div class="kv-v">${fmtValue(k, v)}</div></div>`
  ).join("");
}

function buildInspectorBody(nodeId) {
  const spans = new Map(); // span_id -> { name, start, end }
  for (const evt of currentRunEvents) {
    if (KIND_TO_NODE[evt.kind] !== nodeId) continue;
    if (evt.kind === "orchestrator" && evt.name !== "attempt") continue; // skip nested spans already shown elsewhere
    if (!spans.has(evt.span_id)) spans.set(evt.span_id, { name: evt.name, start: null, end: null });
    const s = spans.get(evt.span_id);
    if (evt.status === "started") s.start = evt;
    else s.end = evt;
  }

  const groups = [...spans.values()].sort((a, b) => {
    const ta = (a.start || a.end).timestamp, tb = (b.start || b.end).timestamp;
    return tb - ta; // most recent first
  });

  if (!groups.length) {
    return `<div class="insp-empty">No calls to this primitive yet in the current run.</div>`;
  }

  return groups.map((g) => {
    const startAttrs = (g.start && g.start.attrs) || {};
    const endAttrs = (g.end && g.end.attrs) || {};
    const outputOnly = {};
    for (const [k, v] of Object.entries(endAttrs)) {
      if (!(k in startAttrs) || JSON.stringify(startAttrs[k]) !== JSON.stringify(v)) outputOnly[k] = v;
    }
    const attemptNo = startAttrs.attempt_no ?? endAttrs.attempt_no;
    const status = g.end ? g.end.status : "started";
    const dur = g.end && g.end.duration_ms != null ? `${g.end.duration_ms.toFixed(1)}ms` : "";
    const cardTitle = attemptNo != null
      ? (g.name === "attempt" ? `attempt ${attemptNo}` : `${g.name} · attempt ${attemptNo}`)
      : g.name;
    return `
      <div class="insp-card">
        <div class="insp-card-head">
          <span class="insp-card-title">${escapeHtml(cardTitle)}</span>
          <span class="insp-card-meta"><span class="st ${status}">${status}</span>${dur ? ` · ${dur}` : ""}</span>
        </div>
        <div class="insp-section"><h4>Input</h4>${fmtAttrs(startAttrs)}</div>
        <div class="insp-section"><h4>Output</h4>${fmtAttrs(outputOnly)}</div>
      </div>`;
  }).join("");
}

// Single reusable modal — used both for the node inspector and for
// expanding a single log line's full event detail, so both look and behave
// the same way (scrollable body, click-outside/Escape/× to close).
let modalEl = null;
function ensureModal() {
  if (modalEl) return modalEl;
  const el = document.createElement("div");
  el.className = "insp-overlay hidden";
  el.innerHTML = `
    <div class="insp-panel">
      <div class="insp-head">
        <div>
          <div class="insp-tag" id="insp-tag"></div>
          <div class="insp-title" id="insp-title"></div>
        </div>
        <button class="insp-close" aria-label="close">&times;</button>
      </div>
      <div class="insp-body" id="insp-body"></div>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener("click", (e) => { if (e.target === el) closeModal(); });
  el.querySelector(".insp-close").addEventListener("click", closeModal);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  modalEl = el;
  return el;
}
function closeModal() {
  if (modalEl) modalEl.classList.add("hidden");
}
function openModal(tag, title, bodyHtml) {
  const el = ensureModal();
  document.getElementById("insp-tag").textContent = tag;
  document.getElementById("insp-title").textContent = title;
  document.getElementById("insp-body").innerHTML = bodyHtml;
  el.classList.remove("hidden");
}
function openInspector(nodeId) {
  const n = NODES[nodeId];
  openModal(`${n.p} · ${n.tag}`, n.title, buildInspectorBody(nodeId));
}

// ---------------------------------------------------------------------------
// Event → action mapping
// ---------------------------------------------------------------------------
function handleEvent(evt) {
  currentRunEvents.push(evt);
  appendLog(evt);
  const a = evt.attrs || {};
  const key = `${evt.kind}:${evt.status}`;

  switch (key) {
    case "run:started":
      pushAction(async () => { setStatus("running"); showPacketAt("orchestrator"); });
      break;

    case "orchestrator:started":
      if (evt.name !== "attempt") break;
      pushAction(async () => {
        const n = a.attempt_no || 1;
        setAttempt(n);
        toolRoundInAttempt = 0;
        setToolRound(0);
        if (n > 1) {
          setNode("orchestrator", "idle");
          await travel("oracle", "orchestrator"); // retry loop
          resetRing();
        }
        setNode("orchestrator", "active");
      });
      break;

    case "instructions:ok":
      pushAction(async () => {
        setNode("orchestrator", "done");
        await travel("orchestrator", "instructions");
        setNode("instructions", "active");
        await dwell(160);
      });
      break;

    case "gen_ai.completion:started":
      pushAction(async () => {
        toolRoundInAttempt++;
        setToolRound(toolRoundInAttempt);
        if (toolRoundInAttempt === 1) {
          // First round of this attempt: the normal Instructions -> Provider path.
          setNode("instructions", "done");
          await travel("instructions", "provider");
        } else {
          // The model is being asked again after seeing a tool result —
          // it's coming back from Tools, not from a fresh Instructions build.
          setNode("tools", "done");
          await travel("tools", "provider");
        }
        setNode("provider", "active");
        await travel("provider", "model");
        modelThink(true);
      });
      break;

    case "gen_ai.completion:ok":
      pushAction(async () => {
        const model = a["gen_ai.response.model"] || "LLM";
        const toks = (a["gen_ai.usage.output_tokens"] ?? "?");
        const lat = a["latency_ms"] != null ? `${Math.round(a["latency_ms"])}ms` : "";
        setModelInfo(model, `${toks} out · ${lat}`);
        modelThink(false);
        await travel("model", "provider");
        const sr = a["safety_retries"] || 0;
        if (sr > 0) {
          modelEl.classList.add("warnflash");
          logNote(`safety-retry ×${sr} — re-routed past the moderation model`, "safety");
          await dwell(500);
          modelEl.classList.remove("warnflash");
        }
      });
      break;

    case "approval_gate:ok":
      pushAction(async () => {
        setNode("provider", "done");
        await travel("provider", "approval");
        setNode("approval", "active");
        await dwell(220);
        setNode("approval", "done");
      });
      break;

    case "gen_ai.tool:started":
      pushAction(async () => {
        lastToolModelInitiated = !!a.model_initiated;
        nodeEls.tools.classList.toggle("selfcheck", lastToolModelInitiated);
        await travel("approval", "tools");
        setNode("tools", "active");
        await dwell(120);
      });
      break;

    case "sandbox_exec:started":
      pushAction(async () => {
        setNode("tools", "done");
        nodeEls.sandbox.classList.toggle("selfcheck", lastToolModelInitiated);
        await travel("tools", "sandbox");
        setNode("sandbox", "exec");
      });
      break;

    case "gen_ai.tool:ok":
      // lookup_docs / algorithm_hint / analyze_complexity never touch the
      // sandbox — nothing else would ever mark this node "done" for them.
      // Harmless no-op for tools that DO use the sandbox (already "done").
      pushAction(async () => { setNode("tools", "done"); });
      break;

    case "verification:ok":
      pushAction(async () => {
        setNode("sandbox", "done");
        await travel("sandbox", "oracle");
        setNode("oracle", "active");
        await dwell(180);
        setNode("oracle", a.oracle_passed ? "done" : "warn");
      });
      break;

    case "run:ok":
      pushAction(async () => { modelThink(false); });
      break;
  }
}

// ---------------------------------------------------------------------------
// Log + status + result panels
// ---------------------------------------------------------------------------
const logEl = document.getElementById("log");
let logCount = 0;

function appendLog(evt) {
  if (logEl.querySelector(".empty")) logEl.innerHTML = "";
  const line = document.createElement("div");
  line.className = "log-line";
  const dur = evt.duration_ms != null ? `${evt.duration_ms.toFixed(1)}ms` : "";
  line.innerHTML =
    `<span class="st ${evt.status}">${evt.status}</span>` +
    `<span class="nm">${evt.name}</span><span class="du">${dur}</span>`;
  line.title = "click to open event detail";
  line.addEventListener("click", () => openLogDetail(evt));
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
  document.getElementById("log-count").textContent = `${++logCount} events`;
}

function openLogDetail(evt) {
  const meta = {
    span_id: evt.span_id, parent_span_id: evt.parent_span_id,
    kind: evt.kind, timestamp: evt.timestamp,
  };
  const body = `
    <div class="insp-card">
      <div class="insp-card-head">
        <span class="insp-card-title">${escapeHtml(evt.name)}</span>
        <span class="insp-card-meta"><span class="st ${evt.status}">${evt.status}</span>${evt.duration_ms != null ? ` · ${evt.duration_ms.toFixed(1)}ms` : ""}</span>
      </div>
      <div class="insp-section"><h4>Meta</h4>${fmtAttrs(meta)}</div>
      <div class="insp-section"><h4>Attrs</h4>${fmtAttrs(evt.attrs)}</div>
    </div>`;
  openModal(evt.kind, evt.name, body);
}
function logNote(text, cls) {
  const line = document.createElement("div");
  line.className = "log-line " + (cls || "");
  line.innerHTML = `<span class="st ok">note</span><span class="nm">${text}</span>`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(s) {
  const pill = document.getElementById("status-pill");
  pill.className = "pill " + s;
  pill.textContent = s;
}
function setAttempt(n) {
  document.getElementById("attempt-counter").innerHTML = `attempt <b>${n}</b>`;
}
function setToolRound(n) {
  const el = document.getElementById("tool-round");
  if (!el) return;
  if (n > 1) {
    el.textContent = `· tool round ${n}`;
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// Classic LCS-based line diff (no library — kata code is a few dozen lines,
// so the O(n*m) table is trivial). Returns an ordered list of
// {type: "same"|"add"|"remove", text} ops, git-diff style.
function diffLines(oldText, newText) {
  const a = oldText.split("\n"), b = newText.split("\n");
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ type: "same", text: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: "remove", text: a[i] }); i++; }
    else { ops.push({ type: "add", text: b[j] }); j++; }
  }
  while (i < n) { ops.push({ type: "remove", text: a[i++] }); }
  while (j < m) { ops.push({ type: "add", text: b[j++] }); }
  return ops;
}

const DIFF_MARK = { same: " ", add: "+", remove: "-" };
function renderDiff(oldText, newText) {
  const ops = diffLines(oldText, newText);
  return `<pre class="diff">` + ops.map((op) =>
    `<span class="diff-line diff-${op.type}">${DIFF_MARK[op.type]} ${escapeHtml(op.text)}</span>`
  ).join("\n") + `</pre>`;
}
// Two-pane variant of the same diff: left pane is the old code with removed
// lines marked red (added lines omitted — they don't exist in the old
// version), right pane is the new code with added lines marked green
// (removed lines omitted). Used for side-by-side compare mode.
function renderDiffPane(oldText, newText, side) {
  const ops = diffLines(oldText, newText);
  const keep = side === "left" ? (t) => t !== "add" : (t) => t !== "remove";
  return `<pre class="diff">` + ops.filter((op) => keep(op.type)).map((op) =>
    `<span class="diff-line diff-${op.type}">${DIFF_MARK[op.type]} ${escapeHtml(op.text)}</span>`
  ).join("\n") + `</pre>`;
}

function failCasesHtml(at) {
  if (at.report.oracle_passed) return "";
  return at.report.failed_cases.slice(0, 5).map((fc) => {
    const detail = fc.error
      ? `raised ${fc.error}`
      : `expected ${JSON.stringify(fc.expected)} got ${JSON.stringify(fc.actual)}`;
    return `<div class="fail-case">input=${JSON.stringify(fc.input)} — ${detail}</div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Attempt cards: collapsed by default, click to expand. At most two can be
// expanded at once — expanding a third evicts the oldest-opened one. With
// exactly two open, they render side by side with a direct diff between
// those two attempts (not just each vs. its immediate predecessor).
// ---------------------------------------------------------------------------
let lastResult = null;
let openAttempts = []; // attempt_no values, in the order they were opened

function renderResult(result) {
  setStatus(result.passed ? "passed" : "failed");
  lastResult = result;
  openAttempts = [];
  renderAttempts();
}

function toggleAttempt(attemptNo) {
  const idx = openAttempts.indexOf(attemptNo);
  if (idx !== -1) {
    openAttempts.splice(idx, 1);
  } else {
    openAttempts.push(attemptNo);
    if (openAttempts.length > 2) openAttempts.shift(); // cap at 2, evict oldest
  }
  renderAttempts();
}

function attemptRowHtml(at, prev, isOpen) {
  const ok = at.report.oracle_passed;
  const sr = at.completion && at.completion.safety_retries;
  let html = `<div class="attempt ${isOpen ? "open" : ""}">
    <div class="head" data-attempt="${at.attempt_no}">
      <span class="head-left">
        <span class="chev">&#9656;</span>
        <span>Attempt ${at.attempt_no}</span>
        ${isOpen ? (prev ? `<span class="diff-badge">diff vs attempt ${prev.attempt_no}</span>` : `<span class="diff-badge">first attempt</span>`) : ""}
        ${sr ? ` <span style="color:var(--exec);font-size:11px">· safety-retry ×${sr}</span>` : ""}
      </span>
      <span class="verd ${ok ? "pass" : "fail"}">${ok ? "pass" : "fail"}</span>
    </div>`;
  if (isOpen) {
    html += `<div class="attempt-body">`;
    html += prev ? renderDiff(prev.code, at.code) : `<pre>${escapeHtml(at.code)}</pre>`;
    html += failCasesHtml(at);
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function comparePaneHtml(at, other, side) {
  const ok = at.report.oracle_passed;
  const sr = at.completion && at.completion.safety_retries;
  return `<div class="compare-pane">
    <div class="head" data-attempt="${at.attempt_no}">
      <span class="head-left">
        <span class="chev">&#9662;</span>
        <span>Attempt ${at.attempt_no}</span>
        ${sr ? ` <span style="color:var(--exec);font-size:11px">· safety-retry ×${sr}</span>` : ""}
      </span>
      <span class="verd ${ok ? "pass" : "fail"}">${ok ? "pass" : "fail"}</span>
    </div>
    <div class="attempt-body">
      ${renderDiffPane(side === "left" ? at.code : other.code, side === "left" ? other.code : at.code, side)}
      ${failCasesHtml(at)}
    </div>
  </div>`;
}

function renderAttempts() {
  const result = lastResult;
  const el = document.getElementById("result");
  let html =
    `<div class="verdict ${result.passed ? "pass" : "fail"}">` +
    `${result.passed ? "PASSED" : "FAILED"}` +
    `<span class="tag">${result.attempts.length} attempt(s) · ${result.total_duration_ms.toFixed(0)}ms</span></div>` +
    `<div class="attempts-grid">`;

  if (openAttempts.length === 2) {
    const [a, b] = [...openAttempts].sort((x, y) => x - y)
      .map((n) => result.attempts.find((at) => at.attempt_no === n));
    html += `<div class="compare-label">comparing attempt ${a.attempt_no} &rarr; attempt ${b.attempt_no}</div>`;
    html += `<div class="compare-grid">${comparePaneHtml(a, b, "left")}${comparePaneHtml(b, a, "right")}</div>`;
    result.attempts.forEach((at) => {
      if (at.attempt_no === a.attempt_no || at.attempt_no === b.attempt_no) return;
      html += attemptRowHtml(at, null, false);
    });
  } else {
    result.attempts.forEach((at, idx) => {
      const isOpen = openAttempts.includes(at.attempt_no);
      const prev = idx > 0 ? result.attempts[idx - 1] : null;
      html += attemptRowHtml(at, prev, isOpen);
    });
  }

  html += `</div>`;
  el.innerHTML = html;

  el.querySelectorAll(".head[data-attempt]").forEach((headEl) => {
    headEl.addEventListener("click", () => toggleAttempt(parseInt(headEl.dataset.attempt, 10)));
  });
}

// ---------------------------------------------------------------------------
// Run lifecycle
// ---------------------------------------------------------------------------
let katas = [];
let currentES = null;

async function loadKatas() {
  const res = await fetch("/katas");
  katas = await res.json();
  const sel = document.getElementById("kata-select");
  sel.innerHTML = katas.map((k) => `<option value="${k.id}">${k.title}</option>`).join("");
  // default to binary_search — the one that shows the retry story
  if (katas.some((k) => k.id === "binary_search")) sel.value = "binary_search";
  updateKataMeta();
}
function updateKataMeta() {
  const k = katas.find((k) => k.id === document.getElementById("kata-select").value);
  if (k) document.getElementById("kata-meta").textContent =
    `${k.category} · ${k.difficulty} · ${k.test_case_count} test cases`;
}

function resetBoard() {
  runToken++;
  queue = []; waiter = null; logCount = 0;
  currentRunEvents = [];
  toolRoundInAttempt = 0;
  lastToolModelInitiated = false;
  closeModal();
  resetRing();
  setNode("orchestrator", "idle");
  nodeEls.tools.classList.remove("selfcheck");
  nodeEls.sandbox.classList.remove("selfcheck");
  modelThink(false);
  modelEl.classList.remove("warnflash");
  setModelInfo("swappable LLM", "");
  packet.classList.remove("retry", "toolloop");
  packet.classList.add("hidden");
  for (const c of Object.values(conns)) c.el.classList.remove("flowing", "traveled");
  logEl.innerHTML = '<div class="empty">Connecting…</div>';
  document.getElementById("result").innerHTML = '<div class="empty">Running…</div>';
  document.getElementById("log-count").textContent = "";
  setAttempt("—");
  setToolRound(0);
}

async function solve() {
  const btn = document.getElementById("solve-btn");
  btn.disabled = true;
  if (currentES) currentES.close();
  resetBoard();
  const myToken = runToken;
  runConsumer(myToken);

  let runId;
  try {
    const res = await fetch("/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kata_id: document.getElementById("kata-select").value,
        max_attempts: parseInt(document.getElementById("max-attempts").value, 10) || 3,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      setStatus("failed");
      document.getElementById("result").innerHTML =
        `<div class="verdict fail">ERROR<span class="tag">${err.detail || res.statusText}</span></div>`;
      btn.disabled = false;
      return;
    }
    runId = (await res.json()).run_id;
  } catch (e) {
    setStatus("failed");
    document.getElementById("result").innerHTML = `<div class="verdict fail">ERROR<span class="tag">${e}</span></div>`;
    btn.disabled = false;
    return;
  }

  const es = new EventSource(`/trace-stream/${runId}`);
  currentES = es;
  logEl.innerHTML = "";
  es.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.final) {
      es.close();
      // Render the verdict as the final queued action so it lands after the
      // flow animation has played through to the oracle.
      pushAction(async () => {
        const r = await fetch(`/runs/${runId}`).then((x) => x.json());
        if (r.status === "done") renderResult(r.result);
        else if (r.status === "error") {
          setStatus("failed");
          document.getElementById("result").innerHTML =
            `<div class="verdict fail">ERROR<span class="tag">${r.error}</span></div>`;
        }
        btn.disabled = false;
      });
      return;
    }
    handleEvent(data);
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
}

document.getElementById("kata-select").addEventListener("change", updateKataMeta);
document.getElementById("solve-btn").addEventListener("click", solve);
buildBoard();
loadKatas();
