"use strict";
const SVGNS = "http://www.w3.org/2000/svg";

// ---------------------------------------------------------------------------
// Flowchart: ONE persistent diagram per run (not redrawn per attempt). Loops
// are real loop-back edges with live counters — a retry travels the purple
// "retry" edge back up to the top, a same-plan retry travels a shorter blue
// edge, and the model being asked again mid-attempt (tool-calling, or the
// Tester's edge-case sweep) travels the amber "tool loop" edge back up to
// Provider. This is what a ring (repeats the same path, no back-edges) and
// per-attempt lanes (flattens the loop into repeated rows) both failed to
// show: the loop *structure* itself.
// ---------------------------------------------------------------------------
const NODE_META = {
  fetch: { title: "Fetch", tag: "get the problem", p: "P15" },
  extract: { title: "Extract", tag: "→ runnable spec", p: "P16" },
  budget: { title: "Budget", tag: "constraints → Big-O", p: "P17" },
  testgen: { title: "Test-gen", tag: "edge cases", p: "P18" },
  orchestrator: { title: "Orchestrator", tag: "loop + retry", p: "P6" },
  instructions: { title: "Instructions", tag: "prompt build", p: "P2" },
  planner: { title: "Planner", tag: "step plan", p: "P12" },
  coder: { title: "Coder", tag: "plan → code", p: "P12" },
  provider: { title: "Provider seam", tag: "LLM client", p: "P1" },
  approval: { title: "Approval gate", tag: "run_tests", p: "P3" },
  tools: { title: "Tools", tag: "run_tests", p: "P3" },
  sandbox: { title: "Sandbox", tag: "subprocess", p: "P4" },
  tester: { title: "Tester", tag: "verify", p: "P12" },
  oracle: { title: "Oracle", tag: "ground truth", p: "P5" },
  judge: { title: "Judge", tag: "bad test or real bug?", p: "P19" },
};
// The Phase 4 prelude (fetch → extract → budget → test-gen) runs once per run
// in both modes; solving differs.
const PRELUDE = ["fetch", "extract", "budget", "testgen"];
const ORDER_SINGLE = [...PRELUDE, "orchestrator", "instructions", "provider", "approval", "tools", "sandbox", "oracle"];
const ORDER_MULTI = [...PRELUDE, "planner", "coder", "provider", "approval", "tools", "sandbox", "tester", "oracle", "judge"];
// Nodes the model can be asked FROM as the first call of a fresh context
// (a straight main-path edge into Provider). Any other source (Tools on a
// tool-loop round, Tester's edge-case call) travels the loop-back edge.
const MAIN_PATH_INTO_PROVIDER = new Set(["instructions", "planner", "coder"]);

const CX = 470, TOP = 60, ROW_H = 106, NW = 220, NH = 58, BOARD_W = 1020;

let currentOrder = ORDER_SINGLE;
let nodePos = {};
let SEGMENTS = [];
const conns = {}; // "from->to" -> { el, type: "line"|"cubic", p0, c1?, c2?, p2, kind }
const nodeEls = {};
let loopBadgeBg = {}; // kind -> <rect> pill behind that loop's counter label

// Loop-back edges are routed through a dedicated vertical "lane" offset from
// the main spine (a cubic bezier whose control points share the lane's x),
// rather than a single symmetric bow — this is what makes them read as
// distinct side lanes (like a real flowchart draws back-edges) instead of
// one arc crossing behind the node column.
function computeLayout(mode) {
  currentOrder = mode === "multi" ? ORDER_MULTI : ORDER_SINGLE;
  nodePos = {};
  currentOrder.forEach((id, i) => { nodePos[id] = { x: CX, y: TOP + i * ROW_H }; });

  const segs = [];
  for (let i = 0; i < currentOrder.length - 1; i++) segs.push([currentOrder[i], currentOrder[i + 1], null, 0]);
  if (mode === "multi") {
    segs.push(["oracle", "planner", "retry", -360]);
    segs.push(["tester", "coder", "sameplan", -210]);
    segs.push(["tools", "provider", "toolloop", 280]);
    segs.push(["tester", "provider", "toolloop", 380]);
  } else {
    segs.push(["oracle", "orchestrator", "retry", -300]);
    segs.push(["tools", "provider", "toolloop", 280]);
  }
  SEGMENTS = segs;
  return TOP + currentOrder.length * ROW_H + 40;
}

function nodeCenter(id) { return nodePos[id]; }

// Where a ray from `center` toward `towardPoint` exits the node's box —
// edges connect at the box boundary, not the center, so nothing renders
// hidden underneath a (later-in-DOM, opaque) node.
function clipToBox(center, towardPoint, halfW, halfH) {
  const dx = towardPoint.x - center.x, dy = towardPoint.y - center.y;
  if (dx === 0 && dy === 0) return { x: center.x, y: center.y };
  const tx = dx !== 0 ? halfW / Math.abs(dx) : Infinity;
  const ty = dy !== 0 ? halfH / Math.abs(dy) : Infinity;
  const t = Math.min(tx, ty, 1);
  return { x: center.x + dx * t, y: center.y + dy * t };
}

function cubicPoint(p0, c1, c2, p3, t) {
  const u = 1 - t;
  return {
    x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p3.y,
  };
}
function pointOnConn(conn, t) {
  if (conn.type === "cubic") return cubicPoint(conn.p0, conn.c1, conn.c2, conn.p2, t);
  const u = 1 - t;
  return { x: conn.p0.x * u + conn.p2.x * t, y: conn.p0.y * u + conn.p2.y * t };
}

function buildBoard(mode) {
  const height = computeLayout(mode);
  document.getElementById("board").setAttribute("viewBox", `0 0 ${BOARD_W} ${height}`);

  const connG = document.getElementById("conns");
  const nodesG = document.getElementById("nodes");
  const badgesG = document.getElementById("loop-badges");
  connG.innerHTML = ""; nodesG.innerHTML = ""; badgesG.innerHTML = "";
  for (const k of Object.keys(conns)) delete conns[k];
  for (const k of Object.keys(nodeEls)) delete nodeEls[k];
  loopBadgeBg = {};

  for (const [from, to, kind, laneOffset] of SEGMENTS) {
    const aCenter = nodeCenter(from), bCenter = nodeCenter(to);
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("class", "conn" + (kind ? " " + kind : ""));
    connG.appendChild(path);

    if (!kind) {
      const p0 = clipToBox(aCenter, bCenter, NW / 2, NH / 2);
      const p2 = clipToBox(bCenter, aCenter, NW / 2, NH / 2);
      path.setAttribute("d", `M ${p0.x} ${p0.y} L ${p2.x} ${p2.y}`);
      conns[`${from}->${to}`] = { el: path, type: "line", p0, p2, kind };
    } else {
      const laneX = CX + laneOffset;
      const c1 = { x: laneX, y: aCenter.y }, c2 = { x: laneX, y: bCenter.y };
      const p0 = clipToBox(aCenter, c1, NW / 2, NH / 2);
      const p2 = clipToBox(bCenter, c2, NW / 2, NH / 2);
      path.setAttribute("d", `M ${p0.x} ${p0.y} C ${c1.x} ${c1.y} ${c2.x} ${c2.y} ${p2.x} ${p2.y}`);
      conns[`${from}->${to}`] = { el: path, type: "cubic", p0, c1, c2, p2, kind };
    }
  }

  const badgeKinds = new Set();
  for (const [from, to, kind] of SEGMENTS) {
    if (!kind || badgeKinds.has(kind)) continue;
    badgeKinds.add(kind);
    const conn = conns[`${from}->${to}`];
    const mid = pointOnConn(conn, 0.5);

    const bg = document.createElementNS(SVGNS, "rect");
    bg.setAttribute("class", `loop-badge-bg loop-badge-bg-${kind} hidden`);
    bg.setAttribute("rx", 6);
    badgesG.appendChild(bg);

    const text = document.createElementNS(SVGNS, "text");
    text.setAttribute("class", `loop-badge loop-badge-${kind} hidden`);
    text.setAttribute("x", mid.x);
    text.setAttribute("y", mid.y);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "middle");
    text.id = `badge-${kind}`;
    badgesG.appendChild(text);
    loopBadgeBg[kind] = bg;
  }

  for (const id of currentOrder) {
    const n = nodePos[id], meta = NODE_META[id];
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "node idle");
    g.dataset.id = id;
    const x = n.x - NW / 2, y = n.y - NH / 2;
    g.innerHTML = `
      <rect class="box" x="${x}" y="${y}" width="${NW}" height="${NH}" rx="14"></rect>
      <text class="pnum" x="${x + 12}" y="${y + 18}">${meta.p}</text>
      <text class="title" x="${n.x}" y="${n.y + 2}" text-anchor="middle">${meta.title}</text>
      <text class="tag" x="${n.x}" y="${n.y + 18}" text-anchor="middle">${meta.tag}</text>`;
    nodesG.appendChild(g);
    nodeEls[id] = g;
    g.addEventListener("click", () => openInspector(id));
  }
}

// ---------------------------------------------------------------------------
// Primitive animation
// ---------------------------------------------------------------------------
const packet = document.getElementById("packet");
const easeInOut = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
const dwell = (ms) => new Promise((r) => setTimeout(r, ms));

function tween(dur, onUpdate) {
  return new Promise((res) => {
    const t0 = performance.now();
    let done = false, raf = 0, timer = 0;
    function step() {
      if (done) return;
      cancelAnimationFrame(raf); clearTimeout(timer);
      const p = Math.min(1, (performance.now() - t0) / dur);
      onUpdate(easeInOut(p));
      if (p < 1) { raf = requestAnimationFrame(step); timer = setTimeout(step, 100); }
      else { done = true; res(); }
    }
    step();
  });
}
function setPacketPos(pt) { packet.setAttribute("transform", `translate(${pt.x} ${pt.y})`); }
function setNode(id, state) {
  const el = nodeEls[id];
  if (!el) return;
  el.classList.remove("idle", "active", "exec", "done", "warn");
  el.classList.add(state);
}
function resetNodes() { for (const id of currentOrder) setNode(id, "idle"); }

// A retry re-runs the *solving* loop only. The prelude (fetch/extract/budget/
// test-gen) happens once per run, so clearing it on every retry would wrongly
// suggest the problem was re-fetched each time.
function resetSolvingNodes() {
  for (const id of currentOrder) {
    if (!PRELUDE.includes(id)) setNode(id, "idle");
  }
}

async function travel(from, to) {
  let conn = conns[`${from}->${to}`];
  let reverse = false;
  if (!conn) { conn = conns[`${to}->${from}`]; reverse = true; }
  if (!conn) { packet.classList.remove("hidden"); setPacketPos(nodeCenter(to)); return; }

  packet.classList.remove("hidden");
  packet.classList.toggle("retry", conn.kind === "retry");
  packet.classList.toggle("sameplan", conn.kind === "sameplan");
  packet.classList.toggle("toolloop", conn.kind === "toolloop");
  conn.el.classList.add("flowing");
  await tween(conn.kind ? 620 : 420, (t) => {
    const tt = reverse ? 1 - t : t;
    setPacketPos(pointOnConn(conn, tt));
  });
  conn.el.classList.remove("flowing");
  conn.el.classList.add("traveled");
  packet.classList.remove("retry", "sameplan", "toolloop");
}

function setLoopBadge(kind, text) {
  const el = document.getElementById(`badge-${kind}`);
  const bg = loopBadgeBg[kind];
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("hidden", !text);
  if (!bg) return;
  if (!text) { bg.classList.add("hidden"); return; }
  bg.classList.remove("hidden");
  // Size the pill to the actual rendered text so the label reads cleanly
  // regardless of what's behind it, instead of a fixed guessed width.
  const box = el.getBBox();
  const padX = 8, padY = 4;
  bg.setAttribute("x", box.x - padX);
  bg.setAttribute("y", box.y - padY);
  bg.setAttribute("width", box.width + padX * 2);
  bg.setAttribute("height", box.height + padY * 2);
}
function flashLoopEdge(kind) {
  const el = document.getElementById(`badge-${kind}`);
  if (!el) return;
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 500);
}

const modelDotEl = document.getElementById("model-dot");
function modelThink(on) { modelDotEl.classList.toggle("think", on); }
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

let toolRoundInAttempt = 0;
let lastToolModelInitiated = false;
let currentMode = "single";
let plannerJustRan = false;
let retryLoopCount = 0;
let sameplanLoopCount = 0;
let toolLoopCount = 0;

// Synchronous event tagging — every event is stamped with which attempt (and,
// in multi mode, which subagent role) it belongs to the instant it arrives,
// independent of the animation queue's pacing. Several event kinds
// (approval_gate, gen_ai.tool, sandbox_exec, ...) don't carry attempt_no in
// their own attrs, so this is what lets the story feed and the inspector
// still attribute them correctly.
let currentAttemptNo = 0;
let currentSubagentRole = null;
function tagEvent(evt) {
  const a = evt.attrs || {};
  if (evt.kind === "orchestrator" && evt.name === "attempt" && evt.status === "started") {
    currentAttemptNo = a.attempt_no || currentAttemptNo + 1;
    currentSubagentRole = null;
  }
  if (evt.kind === "subagent" && evt.status === "started") currentSubagentRole = a.role;
  evt._attemptNo = currentAttemptNo;
  evt._role = currentSubagentRole;
}

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
// Inspector: click a node to see every real call to that primitive across
// the whole run (there's only one node instance now), most recent first,
// each card labeled with which attempt it happened in.
// ---------------------------------------------------------------------------
let currentRunEvents = [];

// The Phase 4 prelude kinds map 1:1 onto their own nodes in both modes.
const KIND_TO_NODE_PRELUDE = {
  fetch: "fetch", extract: "extract", budget: "budget", testgen: "testgen", judge: "judge",
};
const KIND_TO_NODE_SINGLE = {
  ...KIND_TO_NODE_PRELUDE,
  instructions: "instructions", "gen_ai.completion": "provider", approval_gate: "approval",
  "gen_ai.tool": "tools", sandbox_exec: "sandbox", verification: "oracle",
  orchestrator: "orchestrator", run: "orchestrator",
};
const KIND_TO_NODE_MULTI = {
  ...KIND_TO_NODE_PRELUDE,
  "gen_ai.completion": "provider", approval_gate: "approval",
  "gen_ai.tool": "tools", sandbox_exec: "sandbox", verification: "oracle",
  orchestrator: "planner", run: "planner",
};
function kindToNode(kind) {
  return (currentMode === "multi" ? KIND_TO_NODE_MULTI : KIND_TO_NODE_SINGLE)[kind];
}

const SUBAGENT_ROLES = new Set(["planner", "coder", "tester"]);
function buildSubagentInspectorBody(role) {
  const spans = currentRunEvents.filter((e) => e.kind === "subagent" && (e.attrs || {}).role === role);
  const started = spans.filter((e) => e.status === "started").slice().reverse();
  if (!started.length) return `<div class="insp-empty">No calls to this subagent yet in the current run.</div>`;
  return started.map((s) => {
    const end = spans.find((e) => e.span_id === s.span_id && e.status !== "started");
    const dur = end && end.duration_ms != null ? `${end.duration_ms.toFixed(1)}ms` : "";
    const status = end ? end.status : "started";
    return `
      <div class="insp-card">
        <div class="insp-card-head">
          <span class="insp-card-title">${role} · attempt ${s.attrs.attempt_no ?? "?"}</span>
          <span class="insp-card-meta"><span class="st ${status}">${status}</span>${dur ? ` · ${dur}` : ""}</span>
        </div>
        <div class="insp-section"><h4>Input</h4>${fmtAttrs(s.attrs)}</div>
        <div class="insp-section"><h4>Output</h4>${fmtAttrs((end && end.attrs) || {})}</div>
      </div>`;
  }).join("");
}

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
  const spans = new Map(); // span_id -> { name, start, end, attemptNo }
  for (const evt of currentRunEvents) {
    if (kindToNode(evt.kind) !== nodeId) continue;
    if (evt.kind === "orchestrator" && evt.name !== "attempt") continue;
    if (!spans.has(evt.span_id)) spans.set(evt.span_id, { name: evt.name, start: null, end: null, attemptNo: evt._attemptNo });
    const s = spans.get(evt.span_id);
    if (evt.status === "started") s.start = evt; else s.end = evt;
  }
  const groups = [...spans.values()].sort((a, b) => (b.start || b.end).timestamp - (a.start || a.end).timestamp);
  if (!groups.length) return `<div class="insp-empty">No calls to this primitive yet in the current run.</div>`;

  return groups.map((g) => {
    const startAttrs = (g.start && g.start.attrs) || {};
    const endAttrs = (g.end && g.end.attrs) || {};
    const outputOnly = {};
    for (const [k, v] of Object.entries(endAttrs)) {
      if (!(k in startAttrs) || JSON.stringify(startAttrs[k]) !== JSON.stringify(v)) outputOnly[k] = v;
    }
    const status = g.end ? g.end.status : "started";
    const dur = g.end && g.end.duration_ms != null ? `${g.end.duration_ms.toFixed(1)}ms` : "";
    const cardTitle = g.name === "attempt" ? `attempt ${g.attemptNo}` : `${g.name} · attempt ${g.attemptNo}`;
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
// expanding a single event's full detail, so both look and behave the
// same way (scrollable body, click-outside/Escape/× to close).
let modalEl = null;
function ensureModal() {
  if (modalEl) return modalEl;
  const el = document.createElement("div");
  el.className = "insp-overlay hidden";
  el.innerHTML = `
    <div class="insp-panel">
      <div class="insp-head">
        <div><div class="insp-tag" id="insp-tag"></div><div class="insp-title" id="insp-title"></div></div>
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
function closeModal() { if (modalEl) modalEl.classList.add("hidden"); }
function openModal(tag, title, bodyHtml) {
  const el = ensureModal();
  document.getElementById("insp-tag").textContent = tag;
  document.getElementById("insp-title").textContent = title;
  document.getElementById("insp-body").innerHTML = bodyHtml;
  el.classList.remove("hidden");
}
function openInspector(nodeId) {
  const meta = NODE_META[nodeId];
  const body = SUBAGENT_ROLES.has(nodeId) ? buildSubagentInspectorBody(nodeId) : buildInspectorBody(nodeId);
  openModal(`${meta.p} · ${meta.tag}`, meta.title, body);
}

// ---------------------------------------------------------------------------
// Story narration — turns raw trace events into the plain-English sentences
// that explain why a small model can land a hard problem: the harness caught
// its mistake, told it exactly what was wrong, and made it try again. Not
// every event narrates (sandbox spin-up, individual approval gates); the
// full raw trace is always one click away via each attempt's "raw trace" toggle.
// ---------------------------------------------------------------------------
function narrate(evt) {
  const a = evt.attrs || {};

  // ---- Phase 4 prelude: how the harness worked out what "correct" means ----
  if (evt.kind === "fetch" && evt.status === "ok") {
    if (a.ok === false) return { icon: "🚫", text: `Could not fetch the problem: ${a.error}` };
    const diff = a.difficulty && a.difficulty !== "unknown" ? ` (${a.difficulty})` : "";
    return { icon: "🌐", text: `Fetched "${a.title}"${diff} from ${a.source}.` };
  }
  if (evt.kind === "extract" && evt.status === "ok") {
    const how = a.extraction_method === "deterministic"
      ? "parsed straight out of the statement — no model call needed"
      : "extracted by the model from the raw statement";
    const entry = a.kind === "design" ? `class ${a.entry_point}` : `${a.entry_point}()`;
    return { icon: "🔍", text: `Worked out the entry point (${entry}) and ${a.official_case_count} ground-truth test case(s) — ${how}.` };
  }
  if (evt.kind === "budget" && evt.status === "ok") {
    if (!a.max_n) {
      return { icon: "📐", text: "No input bound stated, so no complexity target could be derived." };
    }
    const slow = (a.too_slow || []).length ? ` — ${(a.too_slow || []).join(", ")} would time out` : "";
    return { icon: "📐", text: `Constraints allow n up to ${Number(a.max_n).toLocaleString()}, so the Coder is told to target ${a.acceptable} or better${slow}.` };
  }
  if (evt.kind === "testgen" && evt.status === "ok") {
    const dropped = a.dropped_count
      ? ` (${a.dropped_count} discarded for violating the constraints)` : "";
    if (!a.kept_count) return { icon: "🧪", text: `No extra test cases were generated${dropped}.` };
    return { icon: "🧪", text: `Generated ${a.kept_count} extra edge/happy-path case(s) that respect the stated constraints${dropped}.` };
  }
  if (evt.kind === "judge" && evt.status === "ok") {
    const v = a.verdicts || {};
    const parts = [];
    if (v.bad_test) parts.push(`${v.bad_test} were bad test case(s), discarded`);
    if (v.real_bug) parts.push(`${v.real_bug} exposed a real bug`);
    if (v.uncertain) parts.push(`${v.uncertain} were inconclusive`);
    return { icon: "⚖️", text: `Judge reviewed ${a.disagreement_count} disagreement(s) on generated cases: ${parts.join(", ") || "no verdict"}.` };
  }

  if (evt.kind === "orchestrator" && evt.name === "attempt" && evt.status === "started") {
    const n = a.attempt_no || evt._attemptNo;
    return n === 1
      ? { icon: "▶️", text: "Asking the model to solve it from scratch." }
      : { icon: "🔁", text: `Retrying — attempt ${n}, with the previous failure fed back into the prompt.` };
  }
  if (evt.kind === "subagent" && evt.status === "ok") {
    const role = a.role;
    if (role === "planner") {
      const n = (a.plan_steps || []).length;
      return { icon: "🗺️", text: `Planner drafted a ${n}-step plan — no code yet, just a strategy.` };
    }
    if (role === "coder") {
      const tools = (a.tools_used || []).filter((t) => t !== "run_tests");
      return { icon: "👨‍💻", text: `Coder wrote an implementation${tools.length ? ` (self-checked with: ${tools.join(", ")})` : ""}.` };
    }
    if (role === "tester") {
      return a.oracle_passed
        ? { icon: "✅", text: "Tester ran the official oracle — every case passed." }
        : { icon: "❌", text: `Tester ran the official oracle — ${a.score != null ? Math.round((1 - a.score) * 100) + "%" : "some"} of cases failed.` };
    }
  }
  if (evt.kind === "gen_ai.tool" && evt.status === "started") {
    return a.model_initiated
      ? { icon: "🔧", text: `Model chose to call \`${a["gen_ai.tool.name"]}\` itself, to double-check its own answer before committing.` }
      : { icon: "🛡️", text: `Harness runs its own authoritative \`${a["gen_ai.tool.name"]}\` check — the model's word alone is never trusted.` };
  }
  if (evt.kind === "verification" && evt.status === "ok") {
    // The Tester verifies each tier separately, so this fires twice per
    // attempt. Only the ground-truth run decides pass/fail; the generated
    // run is a different (weaker) claim and is narrated as such.
    if (a.case_tier === "generated") {
      return a.oracle_passed
        ? { icon: "🧪", text: "The generated edge cases all agreed with the code too." }
        : { icon: "🤔", text: `${a.failed_case_count ?? "Some"} generated case(s) disagreed with the code — sending them to the Judge, since a generated case can be wrong itself.` };
    }
    return a.oracle_passed
      ? { icon: "✅", text: "Oracle verdict on the ground-truth cases: all passed." }
      : { icon: "❌", text: `Oracle verdict on the ground-truth cases: ${a.failed_case_count ?? "some"} failed — this is what gets fed back to the model.` };
  }
  if (evt.kind === "skill" && evt.name === "skill_loaded") {
    return { icon: "📘", text: `Loaded the "${a.skill}" technique notes into context (kata category: ${a.kata_category}) — extra know-how the base model doesn't have baked in.` };
  }
  if (evt.kind === "memory" && evt.name === "memory_recall") {
    return { icon: "🧠", text: `Recalled a hint from a previous run: ${a.hint}` };
  }
  if (evt.kind === "gen_ai.completion" && evt.status === "ok" && (a.safety_retries || 0) > 0) {
    return { icon: "⚠️", text: `The provider routed the request to a safety/moderation model instead of a real one — the harness detected that and retried ×${a.safety_retries}.` };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Event → action mapping
// ---------------------------------------------------------------------------
function handleEvent(evt) {
  tagEvent(evt);
  currentRunEvents.push(evt);
  appendLog(evt);
  const a = evt.attrs || {};
  const key = `${evt.kind}:${evt.status}`;

  switch (key) {
    case "run:started":
      pushAction(async () => { setStatus("running"); });
      break;

    // ---- Phase 4 prelude: fetch -> extract -> budget -> test-gen ----
    case "fetch:started":
      pushAction(async () => { setStatus("running"); setNode("fetch", "active"); });
      break;
    case "fetch:ok":
      pushAction(async () => {
        setNode("fetch", a.ok === false ? "warn" : "done");
        await dwell(120);
      });
      break;

    case "extract:started":
      pushAction(async () => { await travel("fetch", "extract"); setNode("extract", "active"); });
      break;
    case "extract:ok":
      pushAction(async () => { setNode("extract", "done"); await dwell(100); });
      break;

    case "budget:started":
      pushAction(async () => {
        // Curated problems skip extraction, so come from whichever ran last.
        const from = nodeEls.extract && nodeEls.extract.classList.contains("idle") ? "fetch" : "extract";
        await travel(from, "budget");
        setNode("budget", "active");
      });
      break;
    case "budget:ok":
      pushAction(async () => {
        setNode("budget", a.max_n ? "done" : "warn");
        await dwell(100);
      });
      break;

    case "testgen:started":
      pushAction(async () => { await travel("budget", "testgen"); setNode("testgen", "active"); });
      break;
    case "testgen:ok":
      pushAction(async () => { setNode("testgen", a.kept_count ? "done" : "warn"); await dwell(100); });
      break;

    case "judge:started":
      pushAction(async () => { await travel("oracle", "judge"); setNode("judge", "active"); });
      break;
    case "judge:ok":
      pushAction(async () => {
        const v = a.verdicts || {};
        setNode("judge", v.real_bug ? "warn" : "done");
        await dwell(140);
      });
      break;

    case "orchestrator:started":
      if (evt.name !== "attempt") break;
      pushAction(async () => {
        const n = a.attempt_no || evt._attemptNo;
        setAttempt(n);
        toolRoundInAttempt = 0;
        setToolRound(0);
        if (n > 1) {
          retryLoopCount++;
          // Only the multi-mode "replanned" retry is decided later (by
          // whether Planner actually runs) — in single mode every retry
          // takes this edge, so animate it now.
          if (currentMode === "single") {
            setNode("oracle", "idle");
            await travel("oracle", "orchestrator");
            resetSolvingNodes();
            setLoopBadge("retry", `retry ×${retryLoopCount}`);
          }
        } else if (currentMode === "single") {
          // First attempt: hand off from the prelude into the solving loop.
          await travel("testgen", "orchestrator");
        }
        if (currentMode === "single") setNode("orchestrator", "active");
      });
      break;

    case "instructions:ok":
      pushAction(async () => {
        setNode("orchestrator", "done");
        await travel("orchestrator", "instructions");
        setNode("instructions", "active");
        await dwell(140);
      });
      break;

    case "subagent:started":
      pushAction(async () => {
        const role = a.role;
        const attemptNo = evt._attemptNo;
        toolRoundInAttempt = 0;
        setToolRound(0);

        if (role === "planner") {
          plannerJustRan = true;
          if (attemptNo > 1) {
            retryLoopCount++;
            setNode("oracle", "idle");
            await travel("oracle", "planner");
            resetSolvingNodes();
            setLoopBadge("retry", `replanned retry ×${retryLoopCount}`);
          } else {
            // First attempt: hand off from the prelude into the solving loop.
            await travel("testgen", "planner");
          }
          setNode("planner", "active");
        } else if (role === "coder") {
          if (plannerJustRan) {
            setNode("planner", "done");
            await travel("planner", "coder");
          } else if (attemptNo > 1) {
            sameplanLoopCount++;
            setNode("tester", "idle");
            for (const id of ["provider", "approval", "tools", "sandbox"]) setNode(id, "idle");
            await travel("tester", "coder");
            setLoopBadge("sameplan", `same-plan retry ×${sameplanLoopCount}`);
          }
          plannerJustRan = false;
          setNode("coder", "active");
        } else if (role === "tester") {
          setNode("coder", "done");
          await travel("coder", "tester");
          setNode("tester", "active");
        }
      });
      break;

    case "subagent:ok":
      pushAction(async () => {
        const role = a.role;
        if (role === "tester") setNode("tester", a.oracle_passed === false ? "warn" : "done");
        else setNode(role, "done");
      });
      break;

    case "gen_ai.completion:started":
      pushAction(async () => {
        toolRoundInAttempt++;
        setToolRound(toolRoundInAttempt);
        const fromNode = toolRoundInAttempt === 1 ? (currentMode === "single" ? "instructions" : evt._role) : "tools";
        const useLoop = !MAIN_PATH_INTO_PROVIDER.has(fromNode);
        setNode(fromNode, "done");
        if (useLoop) {
          toolLoopCount++;
          await travel(fromNode, "provider");
          setLoopBadge("toolloop", `model asked again ×${toolLoopCount}`);
        } else {
          await travel(fromNode, "provider");
        }
        setNode("provider", "active");
        modelThink(true);
      });
      break;

    case "gen_ai.completion:ok":
      pushAction(async () => {
        const model = a["gen_ai.response.model"] || "LLM";
        const toks = a["gen_ai.usage.output_tokens"] ?? "?";
        const lat = a["latency_ms"] != null ? `${Math.round(a["latency_ms"])}ms` : "";
        setModelInfo(model, `${toks} out · ${lat}`);
        modelThink(false);
        const sr = a["safety_retries"] || 0;
        if (sr > 0) {
          modelDotEl.classList.add("warnflash");
          await dwell(500);
          modelDotEl.classList.remove("warnflash");
        }
      });
      break;

    case "approval_gate:ok":
      pushAction(async () => {
        setNode("provider", "done");
        await travel("provider", "approval");
        setNode("approval", "active");
        await dwell(160);
        setNode("approval", "done");
      });
      break;

    case "gen_ai.tool:started":
      pushAction(async () => {
        lastToolModelInitiated = !!a.model_initiated;
        if (nodeEls.tools) nodeEls.tools.classList.toggle("selfcheck", lastToolModelInitiated);
        await travel("approval", "tools");
        setNode("tools", "active");
        await dwell(90);
      });
      break;

    case "sandbox_exec:started":
      pushAction(async () => {
        setNode("tools", "done");
        if (nodeEls.sandbox) nodeEls.sandbox.classList.toggle("selfcheck", lastToolModelInitiated);
        await travel("tools", "sandbox");
        setNode("sandbox", "exec");
      });
      break;

    case "gen_ai.tool:ok":
      pushAction(async () => { setNode("tools", "done"); });
      break;

    case "verification:ok":
      pushAction(async () => {
        setNode("sandbox", "done");
        await travel("sandbox", "oracle");
        setNode("oracle", "active");
        await dwell(150);
        // Only the ground-truth tier decides the Oracle node's verdict. A
        // generated case disagreeing is a question for the Judge, not a
        // failure of the oracle, so it must not paint this node red.
        if (a.case_tier === "generated") {
          setNode("oracle", "done");
          return;
        }
        setNode("oracle", a.oracle_passed ? "done" : "warn");
        // Update this attempt's log-group badge as soon as its verdict is
        // known, rather than waiting for the whole run to finish — a
        // self-check earlier in the same attempt may later be overridden
        // by the harness's own authoritative check, and since events are
        // processed in order, the last verification for this attempt wins.
        const group = logGroups[evt._attemptNo];
        if (group) {
          group.badgeEl.className = "log-group-badge " + (a.oracle_passed ? "pass" : "fail");
          group.badgeEl.textContent = a.oracle_passed ? "pass" : "fail";
        }
      });
      break;

    case "run:ok":
      pushAction(async () => {
        modelThink(false);
        // The last LLM call of a run has no following step to mark Provider
        // done, so it would otherwise be left pulsing after the run ended.
        if (nodeEls.provider && nodeEls.provider.classList.contains("active")) {
          setNode("provider", "done");
        }
      });
      break;
  }
}

// ---------------------------------------------------------------------------
// Story feed (Live Trace panel): narrated sentences grouped by attempt,
// current expanded, past collapsed, raw events one click away per group.
// ---------------------------------------------------------------------------
const logEl = document.getElementById("log");
let logCount = 0;
let logGroups = {}; // attempt_no -> group
let currentLogGroupNo = null;

function ensureLogGroup(attemptNo) {
  if (logGroups[attemptNo]) return logGroups[attemptNo];
  if (logEl.querySelector(".empty")) logEl.innerHTML = "";
  if (currentLogGroupNo != null && logGroups[currentLogGroupNo]) {
    logGroups[currentLogGroupNo].el.classList.add("collapsed"); // previous attempt is finished
  }
  const el = document.createElement("div");
  el.className = "log-group";
  const title = attemptNo === 0 ? "Run" : `Attempt ${attemptNo}`;
  el.innerHTML = `
    <div class="log-group-head">
      <span class="log-group-chev">&#9660;</span>
      <span class="log-group-title">${title}</span>
      <span class="log-group-badge running">running</span>
      <span class="log-group-count">0 events</span>
    </div>
    <div class="log-group-body">
      <div class="story-list"></div>
      <div class="raw-toggle">show raw trace (0)</div>
      <div class="raw-list hidden"></div>
    </div>`;
  logEl.appendChild(el);
  el.querySelector(".log-group-head").addEventListener("click", () => el.classList.toggle("collapsed"));
  const rawToggle = el.querySelector(".raw-toggle");
  const rawList = el.querySelector(".raw-list");
  rawToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    rawList.classList.toggle("hidden");
    rawToggle.textContent = rawList.classList.contains("hidden")
      ? `show raw trace (${group.rawCount})` : `hide raw trace (${group.rawCount})`;
  });
  const group = {
    el, storyEl: el.querySelector(".story-list"), rawEl: rawList, rawToggleEl: rawToggle,
    badgeEl: el.querySelector(".log-group-badge"), countEl: el.querySelector(".log-group-count"), rawCount: 0,
  };
  if (attemptNo === 0) group.badgeEl.classList.add("hidden"); // the top-level "run" span has no pass/fail of its own
  logGroups[attemptNo] = group;
  currentLogGroupNo = attemptNo;
  return group;
}

function appendLog(evt) {
  const group = ensureLogGroup(evt._attemptNo);

  const rawLine = document.createElement("div");
  rawLine.className = "log-line";
  const dur = evt.duration_ms != null ? `${evt.duration_ms.toFixed(1)}ms` : "";
  rawLine.innerHTML = `<span class="st ${evt.status}">${evt.status}</span><span class="nm">${evt.name}</span><span class="du">${dur}</span>`;
  rawLine.title = "click to open event detail";
  rawLine.addEventListener("click", () => openLogDetail(evt));
  group.rawEl.appendChild(rawLine);
  group.rawCount++;
  const hidden = group.rawEl.classList.contains("hidden");
  group.rawToggleEl.textContent = `${hidden ? "show" : "hide"} raw trace (${group.rawCount})`;

  const note = narrate(evt);
  if (note) {
    const storyLine = document.createElement("div");
    storyLine.className = "story-line";
    storyLine.innerHTML = `<span class="story-icon">${note.icon}</span><span class="story-text">${escapeHtml(note.text)}</span>`;
    storyLine.title = "click to see the underlying event";
    storyLine.addEventListener("click", () => openLogDetail(evt));
    group.storyEl.appendChild(storyLine);
  }

  group.countEl.textContent = `${group.rawCount} events`;
  logEl.scrollTop = logEl.scrollHeight;
  document.getElementById("log-count").textContent = `${++logCount} events`;
}

function finalizeLogGroups(result) {
  result.attempts.forEach((at) => {
    const group = logGroups[at.attempt_no];
    if (!group) return;
    const passed = at.report.oracle_passed;
    group.badgeEl.className = "log-group-badge " + (passed ? "pass" : "fail");
    group.badgeEl.textContent = passed ? "pass" : "fail";
  });
}

function openLogDetail(evt) {
  const meta = { span_id: evt.span_id, parent_span_id: evt.parent_span_id, kind: evt.kind, timestamp: evt.timestamp };
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

function setStatus(s) {
  const pill = document.getElementById("status-pill");
  pill.className = "pill " + s;
  pill.textContent = s;
}
function setAttempt(n) { document.getElementById("attempt-counter").innerHTML = `attempt <b>${n}</b>`; }
function setToolRound(n) {
  const el = document.getElementById("tool-round");
  if (!el) return;
  if (n > 1) { el.textContent = `· tool round ${n}`; el.classList.remove("hidden"); }
  else el.classList.add("hidden");
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
let openAttempts = [];

function renderResult(result) {
  setStatus(result.passed ? "passed" : "failed");
  lastResult = result;
  openAttempts = [];
  finalizeLogGroups(result);
  renderAttempts();
}

function toggleAttempt(attemptNo) {
  const idx = openAttempts.indexOf(attemptNo);
  if (idx !== -1) openAttempts.splice(idx, 1);
  else {
    openAttempts.push(attemptNo);
    if (openAttempts.length > 2) openAttempts.shift();
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
let currentES = null;

// ---------------------------------------------------------------------------
// Problem panel — the question itself, plus everything the harness derived
// from it before writing a line of code: the entry point it has to implement,
// what the constraints imply about acceptable complexity, and exactly which
// test cases it will be graded on (labelled by tier, so it's obvious which
// ones are ground truth and which were invented).
// ---------------------------------------------------------------------------
function renderProblem(p) {
  // Be explicit about how the oracle was obtained — "parsed from the
  // statement" and "written by the model" are very different trust levels.
  const METHOD_LABEL = {
    curated: "hand-written · offline",
    deterministic: "parsed from the statement",
    llm: "model-extracted",
    "llm-retry": "model-extracted (retried)",
  };
  document.getElementById("problem-source").textContent =
    `${p.source} · ${METHOD_LABEL[p.extraction_method] || p.extraction_method}`;

  const diff = (p.difficulty || "unknown").toLowerCase();
  const tags = [
    `<span class="tag-chip ${diff}">${escapeHtml(diff)}</span>`,
    `<span class="tag-chip kind">${escapeHtml(p.kind)}</span>`,
    ...(p.topics || []).slice(0, 4).map((t) => `<span class="tag-chip">${escapeHtml(t)}</span>`),
  ].join("");

  const b = p.budget || {};
  const budgetCard = b.acceptable && b.acceptable !== "unknown"
    ? `<div class="budget-card">
         <div><span class="budget-target">${escapeHtml(b.acceptable)}</span> or better
           ${b.max_n ? `<span style="color:var(--muted)"> · n up to ${Number(b.max_n).toLocaleString()}</span>` : ""}</div>
         <div class="budget-why">${escapeHtml(b.reasoning || "")}</div>
         ${(b.too_slow || []).length ? `<div class="budget-slow">too slow: ${escapeHtml((b.too_slow).join(", "))}</div>` : ""}
       </div>`
    : `<div class="budget-card"><div class="budget-why">${escapeHtml(b.reasoning || "No complexity target could be derived.")}</div></div>`;

  const cases = (p.test_cases || []).map((c) => `
    <tr>
      <td><span class="tier ${c.source}">${c.source === "generated" ? "gen" : "truth"}</span></td>
      <td class="val">${escapeHtml(JSON.stringify(c.input))}</td>
      <td class="val">${escapeHtml(JSON.stringify(c.expected))}</td>
      <td class="case-rationale">${escapeHtml(c.rationale || "")}</td>
    </tr>`).join("");

  const truthCount = (p.test_cases || []).filter((c) => c.source !== "generated").length;
  const genCount = (p.test_cases || []).length - truthCount;

  document.getElementById("problem-body").innerHTML = `
    <div class="problem-title">${escapeHtml(p.title)}</div>
    <div class="problem-tags">${tags}
      ${p.url ? `<a class="problem-link" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">view source ↗</a>` : ""}
    </div>
    <div class="statement collapsed" id="statement">${escapeHtml(p.statement_text || "")}</div>
    <span class="statement-toggle" id="statement-toggle">show full statement</span>

    <div class="section-label">Entry point</div>
    <pre style="margin:0">${escapeHtml(p.signature || p.starter_code || "—")}</pre>

    ${(p.constraints || []).length ? `
      <div class="section-label">Constraints</div>
      <ul class="constraint-list">${p.constraints.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}

    <div class="section-label">Complexity budget (derived)</div>
    ${budgetCard}

    <div class="section-label">Test cases · ${truthCount} ground truth + ${genCount} generated</div>
    <table class="cases">
      <thead><tr><th>tier</th><th>input</th><th>expected</th><th>why</th></tr></thead>
      <tbody>${cases || `<tr><td colspan="4" class="case-rationale">none</td></tr>`}</tbody>
    </table>`;

  const stmt = document.getElementById("statement");
  const toggle = document.getElementById("statement-toggle");
  toggle.addEventListener("click", () => {
    stmt.classList.toggle("collapsed");
    toggle.textContent = stmt.classList.contains("collapsed") ? "show full statement" : "show less";
  });

  document.getElementById("problem-meta").textContent =
    `${p.title} · ${diff} · ${p.kind}`;
}

// ---------------------------------------------------------------------------
// Metrics panel — the "what did the harness cost" numbers.
// ---------------------------------------------------------------------------
function fmtMs(ms) {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function renderMetrics(m, result) {
  const pct = (v) => (m.wall_ms ? Math.max(0, (v / m.wall_ms) * 100) : 0);
  const otherMs = Math.max(0, m.wall_ms - m.llm_ms - m.sandbox_ms);

  const agents = Object.entries(m.per_agent || {})
    .sort((a, b) => b[1].total_tokens - a[1].total_tokens)
    .map(([role, u]) => `
      <tr>
        <td class="role">${escapeHtml(role)}</td>
        <td>${u.calls}</td>
        <td>${u.input_tokens.toLocaleString()}</td>
        <td>${u.output_tokens.toLocaleString()}</td>
        <td>${fmtMs(u.latency_ms)}</td>
      </tr>`).join("");

  document.getElementById("metrics-body").innerHTML = `
    <div class="metric-grid">
      <div class="metric"><div class="metric-value">${fmtMs(m.wall_ms)}</div>
        <div class="metric-label">wall time</div></div>
      <div class="metric"><div class="metric-value">${m.attempts}</div>
        <div class="metric-label">attempts</div>
        <div class="metric-sub">${result && result.passed ? "solved" : "unsolved"}</div></div>
      <div class="metric"><div class="metric-value">${m.total_tokens.toLocaleString()}</div>
        <div class="metric-label">tokens</div>
        <div class="metric-sub">${m.input_tokens.toLocaleString()} in · ${m.output_tokens.toLocaleString()} out</div></div>
      <div class="metric highlight"><div class="metric-value">$${m.cost_usd.toFixed(4)}</div>
        <div class="metric-label">cost</div>
        <div class="metric-sub">${escapeHtml((m.models_used || []).join(", ") || "—")}</div></div>
      <div class="metric"><div class="metric-value">${m.llm_calls}</div>
        <div class="metric-label">llm calls</div>
        <div class="metric-sub">${m.sandbox_execs} sandbox runs</div></div>
    </div>

    <div class="section-label">Where the time went</div>
    <div class="split-bar">
      <div class="split-seg llm" style="width:${pct(m.llm_ms)}%">${pct(m.llm_ms) > 12 ? "model" : ""}</div>
      <div class="split-seg sandbox" style="width:${pct(m.sandbox_ms)}%">${pct(m.sandbox_ms) > 12 ? "sandbox" : ""}</div>
      <div class="split-seg other" style="width:${pct(otherMs)}%">${pct(otherMs) > 12 ? "harness" : ""}</div>
    </div>
    <div class="split-key">
      <span><i style="background:var(--accent)"></i>model ${fmtMs(m.llm_ms)}</span>
      <span><i style="background:var(--exec)"></i>sandbox ${fmtMs(m.sandbox_ms)}</span>
      <span><i style="background:#3f4a63"></i>harness ${fmtMs(otherMs)}</span>
    </div>

    <div class="section-label">Tokens by agent</div>
    <table class="agents">
      <thead><tr><th>agent</th><th>calls</th><th>in</th><th>out</th><th>time</th></tr></thead>
      <tbody>${agents || `<tr><td colspan="5">no model calls</td></tr>`}</tbody>
    </table>`;
}

function resetBoard() {
  runToken++;
  queue = []; waiter = null; logCount = 0;
  currentRunEvents = [];
  toolRoundInAttempt = 0;
  lastToolModelInitiated = false;
  plannerJustRan = false;
  currentAttemptNo = 0;
  currentSubagentRole = null;
  retryLoopCount = 0; sameplanLoopCount = 0; toolLoopCount = 0;
  closeModal();

  currentMode = document.getElementById("mode-select").value;
  buildBoard(currentMode);
  resetNodes();
  setLoopBadge("retry", "");
  setLoopBadge("sameplan", "");
  setLoopBadge("toolloop", "");

  logGroups = {};
  currentLogGroupNo = null;

  modelThink(false);
  modelDotEl.classList.remove("warnflash");
  setModelInfo("swappable LLM", "");
  packet.classList.remove("retry", "sameplan", "toolloop");
  packet.classList.add("hidden");
  for (const c of Object.values(conns)) c.el.classList.remove("flowing", "traveled");
  logEl.innerHTML = '<div class="empty">Connecting…</div>';
  document.getElementById("result").innerHTML = '<div class="empty">Running…</div>';
  document.getElementById("metrics-body").innerHTML = '<div class="empty">Metrics appear when the run finishes.</div>';
  document.getElementById("log-count").textContent = "";
  setAttempt("—");
  setToolRound(0);
}

function showError(message) {
  setStatus("failed");
  document.getElementById("result").innerHTML =
    `<div class="verdict fail">ERROR<span class="tag">${escapeHtml(String(message))}</span></div>`;
}

async function solve() {
  const btn = document.getElementById("solve-btn");
  const ref = document.getElementById("problem-ref").value.trim();
  if (!ref) {
    document.getElementById("problem-ref").focus();
    return;
  }

  btn.disabled = true;
  if (currentES) currentES.close();
  resetBoard();
  const myToken = runToken;
  runConsumer(myToken);

  document.getElementById("problem-body").innerHTML =
    '<div class="empty">Fetching the problem…</div>';

  let runId;
  try {
    const res = await fetch("/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        problem_ref: ref,
        max_attempts: parseInt(document.getElementById("max-attempts").value, 10) || 3,
        mode: document.getElementById("mode-select").value,
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(payload.detail || res.statusText);
      document.getElementById("problem-body").innerHTML =
        `<div class="empty">Could not load that problem: ${escapeHtml(String(payload.detail || res.statusText))}</div>`;
      btn.disabled = false;
      return;
    }
    runId = payload.run_id;
    // The prepared problem comes back with the run id, so the question and
    // its derived budget/test cases are on screen before solving starts.
    if (payload.problem) renderProblem(payload.problem);
  } catch (e) {
    showError(e);
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
      pushAction(async () => {
        const r = await fetch(`/runs/${runId}`).then((x) => x.json());
        if (r.status === "done") {
          renderResult(r.result);
          if (r.metrics) renderMetrics(r.metrics, r.result);
          if (r.problem) renderProblem(r.problem);
        } else if (r.status === "error") {
          showError(r.error);
        }
        btn.disabled = false;
      });
      return;
    }
    handleEvent(data);
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
}

// Preview a problem without solving it — fetch + extract + budget only, so
// the question can be read (and its derived oracle inspected) up front.
async function previewProblem(ref) {
  document.getElementById("problem-body").innerHTML = '<div class="empty">Fetching…</div>';
  try {
    const res = await fetch(`/problem?ref=${encodeURIComponent(ref)}&generate_tests=false`);
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("problem-body").innerHTML =
        `<div class="empty">${escapeHtml(String(data.detail || res.statusText))}</div>`;
      return;
    }
    renderProblem(data);
  } catch (e) {
    document.getElementById("problem-body").innerHTML =
      `<div class="empty">${escapeHtml(String(e))}</div>`;
  }
}

const refInput = document.getElementById("problem-ref");
document.getElementById("solve-btn").addEventListener("click", solve);
refInput.addEventListener("keydown", (e) => { if (e.key === "Enter") solve(); });
// Changing modes reshapes the flowchart, so redraw it before the next run.
document.getElementById("mode-select").addEventListener("change", () => {
  currentMode = document.getElementById("mode-select").value;
  buildBoard(currentMode);
  resetNodes();
});
document.querySelectorAll(".qp").forEach((el) => {
  el.addEventListener("click", () => {
    refInput.value = el.dataset.ref;
    previewProblem(el.dataset.ref);
  });
});
buildBoard(document.getElementById("mode-select").value);

// ---------------------------------------------------------------------------
// History view (P14 dashboard) — pass rate + retry distribution per kata,
// plus a raw recent-attempts table. Reads GET /stats + GET /history.
// ---------------------------------------------------------------------------
function statsCardHtml(kataId, s) {
  const pct = Math.round(s.pass_rate * 100);
  return `<div class="stat-card">
    <div class="stat-kata">${escapeHtml(kataId)}</div>
    <div class="stat-row">${s.attempt_count} attempt(s) recorded · max attempt_no ${s.max_attempt_no}</div>
    <div class="stat-row">${pct}% pass rate</div>
    <div class="stat-bar"><div class="stat-bar-fill" style="width:${pct}%"></div></div>
  </div>`;
}

function historyRowHtml(row) {
  const when = new Date(row.created_at * 1000).toLocaleString();
  return `<tr>
    <td>${escapeHtml(row.kata_id)}</td>
    <td>${escapeHtml(row.kata_category)}</td>
    <td>${row.attempt_no}</td>
    <td class="${row.passed ? "pass" : "fail"}">${row.passed ? "pass" : "fail"}</td>
    <td>${row.tokens_used}</td>
    <td>${escapeHtml(row.failure_reason || "—")}</td>
    <td>${when}</td>
  </tr>`;
}

async function loadHistory() {
  const statsEl = document.getElementById("stats-grid");
  const tableEl = document.getElementById("history-table");
  statsEl.innerHTML = `<div class="empty">Loading…</div>`;
  tableEl.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const [stats, history] = await Promise.all([
      fetch("/stats").then((r) => r.json()),
      fetch("/history").then((r) => r.json()),
    ]);

    const kataIds = Object.keys(stats.per_kata);
    statsEl.innerHTML = kataIds.length
      ? kataIds.map((id) => statsCardHtml(id, stats.per_kata[id])).join("")
      : `<div class="empty">No runs recorded yet — solve a kata first.</div>`;

    tableEl.innerHTML = history.length
      ? `<table class="history-table">
          <thead><tr><th>kata</th><th>category</th><th>attempt</th><th>verdict</th><th>tokens</th><th>failure reason</th><th>when</th></tr></thead>
          <tbody>${history.map(historyRowHtml).join("")}</tbody>
        </table>`
      : `<div class="empty">No attempts recorded yet.</div>`;
  } catch (e) {
    statsEl.innerHTML = `<div class="empty">Failed to load history: ${e}</div>`;
    tableEl.innerHTML = "";
  }
}

function showView(view) {
  document.getElementById("view-run").classList.toggle("hidden", view !== "run");
  document.getElementById("view-history").classList.toggle("hidden", view !== "history");
  document.getElementById("tab-run").classList.toggle("active", view === "run");
  document.getElementById("tab-history").classList.toggle("active", view === "history");
  if (view === "history") loadHistory();
}
document.getElementById("tab-run").addEventListener("click", () => showView("run"));
document.getElementById("tab-history").addEventListener("click", () => showView("history"));
