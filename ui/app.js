"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ═══════════════════════════════════════════════════════════════════════════
// THE PIPELINE DIAGRAM
// ---------------------------------------------------------------------------
// ONE persistent diagram per run (not redrawn per attempt). Loops are real
// loop-back edges with live counters — a retry travels back to the top of the
// solving loop, a same-plan retry takes a shorter hop from Tester to Coder,
// and the model being asked again mid-attempt (a tool-calling round, the
// Tester's edge-case sweep) travels back into Provider. This is what a ring
// (repeats the same path, no back-edges) and per-attempt lanes (flattens the
// loop into repeated rows) both failed to show: the loop *structure* itself.
//
// Geometry is a serpentine — prepare phase left-to-right across the top band,
// then the solving loop boustrophedon below — so each loop-back is a short hop
// up its own column rather than a long bow around the outside, and the diagram
// uses the width it actually has instead of scaling itself unreadable.
//
// Colour carries two independent signals that must not compete:
//   HUE   = phase (a cyan→teal→indigo→violet→amber→emerald spectrum)
//   GLOW  = state (idle / running / executing / done / failed)
// ═══════════════════════════════════════════════════════════════════════════
const NODE_META = {
  fetch:        { title: "Fetch",         tag: "get the problem",       p: "P15", phase: "prepare" },
  extract:      { title: "Extract",       tag: "→ runnable spec",       p: "P16", phase: "prepare" },
  budget:       { title: "Budget",        tag: "constraints → Big-O",   p: "P17", phase: "derive"  },
  testgen:      { title: "Test-gen",      tag: "edge cases",            p: "P18", phase: "derive"  },
  orchestrator: { title: "Orchestrator",  tag: "loop + retry",          p: "P6",  phase: "agent"   },
  instructions: { title: "Instructions",  tag: "prompt build",          p: "P2",  phase: "agent"   },
  planner:      { title: "Planner",       tag: "step plan",             p: "P12", phase: "agent"   },
  coder:        { title: "Coder",         tag: "plan → code",           p: "P12", phase: "agent"   },
  provider:     { title: "Provider seam", tag: "LLM client",            p: "P1",  phase: "model"   },
  approval:     { title: "Approval gate", tag: "run_tests",             p: "P3",  phase: "exec"    },
  tools:        { title: "Tools",         tag: "run_tests",             p: "P3",  phase: "exec"    },
  sandbox:      { title: "Sandbox",       tag: "subprocess",            p: "P4",  phase: "exec"    },
  tester:       { title: "Tester",        tag: "verify",                p: "P12", phase: "agent"   },
  oracle:       { title: "Oracle",        tag: "ground truth",          p: "P5",  phase: "verdict" },
  judge:        { title: "Judge",         tag: "bad test or real bug?", p: "P19", phase: "verdict" },
};
const HUE = {
  prepare: "#22d3ee", derive: "#2dd4bf", agent: "#818cf8",
  model: "#a78bfa", exec: "#fbbf24", verdict: "#34d399",
};
const LOOP_HUE = { retry: "#a78bfa", sameplan: "#22d3ee", toolloop: "#fbbf24" };
const hueOf = (id) => HUE[NODE_META[id].phase];

const PRELUDE = ["fetch", "extract", "budget", "testgen"];
const ORDER_SINGLE = [...PRELUDE, "orchestrator", "instructions", "provider", "approval", "tools", "sandbox", "oracle"];
const ORDER_MULTI = [...PRELUDE, "planner", "coder", "provider", "approval", "tools", "sandbox", "tester", "oracle", "judge"];
// Nodes the model can be asked FROM as the first call of a fresh context (a
// main-path edge into Provider). Any other source travels the amber loop-back.
const MAIN_PATH_INTO_PROVIDER = new Set(["instructions", "planner", "coder"]);

// Board units are chosen so the diagram is WIDER than the panel it sits in
// (aspect ~2.3 vs the stage's ~2.0). That makes it width-bound when scaled to
// fit, which in turn means the row pitch — not the panel's height — decides how
// big a node renders. Rows are therefore packed tight: at a 132-unit pitch a
// 66-unit node fills half its row, and the labels survive the downscale.
// (Measured: the previous 4-row/612-unit board rendered its titles at 8.2px.)
const BOARD_W = 1250;
const COLS = [190, 480, 770, 1060];
const NW = 240, NH = 66;   // solving nodes
const PW = 224, PH = 56;   // prelude nodes
const Y_PRE = 62, Y_A = 232, Y_B = 380, Y_C = 528;

let currentOrder = ORDER_SINGLE;
let nodePos = {}, SEGMENTS = [], BANDS = [];
const conns = {}, nodeEls = {};
let loopBadgeBg = {};

function layout(mode) {
  currentOrder = mode === "multi" ? ORDER_MULTI : ORDER_SINGLE;
  nodePos = {};
  PRELUDE.forEach((id, i) => { nodePos[id] = { x: COLS[i], y: Y_PRE, w: PW, h: PH }; });

  const put = (id, x, y) => { nodePos[id] = { x, y, w: NW, h: NH }; };
  const segs = [];
  const row = (a, b) => segs.push([a, b, null, { fs: "right", ts: "left" }]);
  const rowBack = (a, b) => segs.push([a, b, null, { fs: "left", ts: "right" }]);
  const down = (a, b) => segs.push([a, b, null, { fs: "bottom", ts: "top" }]);

  row("fetch", "extract"); row("extract", "budget"); row("budget", "testgen");

  let bottomY;
  if (mode === "multi") {
    put("planner", COLS[0], Y_A);  put("coder", COLS[1], Y_A);
    put("provider", COLS[2], Y_A); put("approval", COLS[3], Y_A);
    put("tools", COLS[3], Y_B);    put("sandbox", COLS[2], Y_B);
    put("tester", COLS[1], Y_B);   put("oracle", COLS[0], Y_B);
    put("judge", COLS[0], Y_C);

    segs.push(["testgen", "planner", null, { fs: "bottom", ts: "top", route: "sv" }]);
    row("planner", "coder"); row("coder", "provider"); row("provider", "approval");
    down("approval", "tools");
    rowBack("tools", "sandbox"); rowBack("sandbox", "tester"); rowBack("tester", "oracle");
    down("oracle", "judge");
    segs.push(["oracle", "planner", "retry", { fs: "top", ts: "bottom" }]);
    segs.push(["tester", "coder", "sameplan", { fs: "top", ts: "bottom" }]);
    segs.push(["tools", "provider", "toolloop", { fs: "top", ff: .28, ts: "bottom", tf: .74, route: "sv" }]);
    segs.push(["tester", "provider", "toolloop", { fs: "top", ff: .76, ts: "bottom", tf: .26, route: "sv" }]);
    bottomY = Y_C + NH / 2;
  } else {
    put("orchestrator", COLS[0], Y_A); put("instructions", COLS[1], Y_A);
    put("provider", COLS[2], Y_A);     put("approval", COLS[3], Y_A);
    put("tools", COLS[3], Y_B);        put("sandbox", COLS[2], Y_B);
    put("oracle", COLS[1], Y_B);

    segs.push(["testgen", "orchestrator", null, { fs: "bottom", ts: "top", route: "sv" }]);
    row("orchestrator", "instructions"); row("instructions", "provider"); row("provider", "approval");
    down("approval", "tools");
    rowBack("tools", "sandbox"); rowBack("sandbox", "oracle");
    segs.push(["oracle", "orchestrator", "retry", { fs: "left", ts: "left", route: "lane", lane: 58 }]);
    segs.push(["tools", "provider", "toolloop", { fs: "top", ff: .28, ts: "bottom", tf: .74, route: "sv" }]);
    bottomY = Y_B + NH / 2;
  }

  SEGMENTS = segs;
  const solveTop = 168;
  BANDS = [
    { x: 44, y: 16, w: BOARD_W - 88, h: 90, label: "1 · prepare the problem", sub: "P15–P18", hue: HUE.prepare },
    { x: 44, y: solveTop, w: BOARD_W - 88, h: bottomY + 24 - solveTop,
      label: "2 · solving loop", sub: mode === "multi" ? "P12 · P1–P6 · P19" : "P1–P6", hue: HUE.agent },
  ];
  return bottomY + 36;
}

// A point on a node's boundary: side + fraction along it. Explicit anchors
// (rather than clipping a centre-to-centre ray) keep two edges that share a
// node from stacking on the same port.
function anchor(id, side, frac) {
  const n = nodePos[id], f = frac == null ? .5 : frac;
  const x0 = n.x - n.w / 2, y0 = n.y - n.h / 2;
  if (side === "top")    return { x: x0 + n.w * f, y: y0 };
  if (side === "bottom") return { x: x0 + n.w * f, y: y0 + n.h };
  if (side === "left")   return { x: x0,           y: y0 + n.h * f };
  return { x: x0 + n.w, y: y0 + n.h * f };
}
function cubicPoint(p0, c1, c2, p3, t) {
  const u = 1 - t;
  return {
    x: u*u*u*p0.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*p3.x,
    y: u*u*u*p0.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*p3.y,
  };
}
function pointOnConn(c, t) {
  if (c.type === "cubic") return cubicPoint(c.p0, c.c1, c.c2, c.p2, t);
  const u = 1 - t;
  return { x: c.p0.x * u + c.p2.x * t, y: c.p0.y * u + c.p2.y * t };
}

const el = (tag, attrs) => {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
};

function buildBoard(mode) {
  const height = layout(mode);
  $("board").setAttribute("viewBox", `0 0 ${BOARD_W} ${height}`);

  const defs = $("defs-dyn"), bandG = $("bands"), connG = $("conns"),
        nodesG = $("nodes"), badgeG = $("loop-badges"), cometG = $("comet");
  [defs, bandG, connG, nodesG, badgeG, cometG].forEach((g) => (g.innerHTML = ""));
  for (const k of Object.keys(conns)) delete conns[k];
  for (const k of Object.keys(nodeEls)) delete nodeEls[k];
  loopBadgeBg = {};

  // ── phase-tinted bands, label riding the top border like a fieldset legend
  BANDS.forEach((b) => {
    const g = el("linearGradient", { id: `bandg-${b.label.charCodeAt(0)}`, x1: "0", y1: "0", x2: "0", y2: "1" });
    g.appendChild(el("stop", { offset: "0%", "stop-color": b.hue, "stop-opacity": ".055" }));
    g.appendChild(el("stop", { offset: "100%", "stop-color": b.hue, "stop-opacity": ".012" }));
    defs.appendChild(g);
    bandG.appendChild(el("rect", {
      class: "band", x: b.x, y: b.y, width: b.w, height: b.h, rx: 20,
      fill: `url(#bandg-${b.label.charCodeAt(0)})`, stroke: b.hue, "stroke-opacity": ".16",
    }));
    const label = `${b.label}   ·   ${b.sub}`;
    const wpx = label.length * 7.4 + 28;
    bandG.appendChild(el("rect", {
      class: "band-pill", x: b.x + 20, y: b.y - 12, width: wpx, height: 24, rx: 12,
      fill: "#0b0b11", stroke: b.hue, "stroke-opacity": ".26",
    }));
    const t = el("text", { class: "band-label", x: b.x + 20 + wpx / 2, y: b.y + 1,
      "text-anchor": "middle", "dominant-baseline": "middle", fill: b.hue, "fill-opacity": ".75" });
    t.textContent = label;
    bandG.appendChild(t);
  });

  // ── edges: gradient stroke source-hue → target-hue, plus a blur layer that
  //    only lights up while the packet is actually on that edge
  SEGMENTS.forEach(([from, to, kind, spec], i) => {
    const p0 = anchor(from, spec.fs, spec.ff), p2 = anchor(to, spec.ts, spec.tf);
    let d, c1, c2, type = "line";
    if (spec.route === "sv") {
      const my = (p0.y + p2.y) / 2;
      c1 = { x: p0.x, y: my }; c2 = { x: p2.x, y: my }; type = "cubic";
    } else if (spec.route === "lane") {
      c1 = { x: spec.lane, y: p0.y }; c2 = { x: spec.lane, y: p2.y }; type = "cubic";
    }
    d = type === "cubic"
      ? `M ${p0.x} ${p0.y} C ${c1.x} ${c1.y} ${c2.x} ${c2.y} ${p2.x} ${p2.y}`
      : `M ${p0.x} ${p0.y} L ${p2.x} ${p2.y}`;

    const gid = `eg${i}`;
    const grad = el("linearGradient", { id: gid, gradientUnits: "userSpaceOnUse", x1: p0.x, y1: p0.y, x2: p2.x, y2: p2.y });
    const a = kind ? LOOP_HUE[kind] : hueOf(from);
    const bcol = kind ? LOOP_HUE[kind] : hueOf(to);
    grad.appendChild(el("stop", { offset: "0%", "stop-color": a }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": bcol }));
    defs.appendChild(grad);

    const glow = el("path", { class: "conn-glow", d, stroke: `url(#${gid})` });
    const path = el("path", { class: "conn" + (kind ? " loop " + kind : ""), d, stroke: `url(#${gid})` });
    connG.appendChild(glow); connG.appendChild(path);

    const c = { el: path, glow, type, p0, c1, c2, p2, kind };
    // arrowhead drawn by hand: a marker can't inherit a gradient stroke
    const tip = pointOnConn(c, 1), pre = pointOnConn(c, .965);
    const ang = Math.atan2(tip.y - pre.y, tip.x - pre.x) * 180 / Math.PI;
    const head = el("path", { class: "arrow", d: "M -9 -5 L 0 0 L -9 5 Z", fill: bcol,
      transform: `translate(${tip.x} ${tip.y}) rotate(${ang})` });
    connG.appendChild(head);
    c.head = head;
    conns[`${from}->${to}`] = c;
  });

  // ── loop counter badges
  const seen = new Set();
  SEGMENTS.forEach(([from, to, kind]) => {
    if (!kind || seen.has(kind)) return;
    seen.add(kind);
    const mid = pointOnConn(conns[`${from}->${to}`], .5);
    const bg = el("rect", { class: "loop-badge-bg hidden", rx: 7, fill: "#0b0b11",
      stroke: LOOP_HUE[kind], "stroke-opacity": ".45" });
    badgeG.appendChild(bg);
    const t = el("text", { class: "loop-badge hidden", id: `badge-${kind}`, x: mid.x, y: mid.y,
      "text-anchor": "middle", "dominant-baseline": "middle", fill: LOOP_HUE[kind] });
    badgeG.appendChild(t);
    loopBadgeBg[kind] = bg;
  });

  // ── nodes: glass plate + specular top edge + phase badge + travelling beam
  currentOrder.forEach((id, i) => {
    const n = nodePos[id], meta = NODE_META[id], small = PRELUDE.includes(id);
    const hue = hueOf(id);
    const x = n.x - n.w / 2, y = n.y - n.h / 2, r = 17;

    const g = el("g", { class: "node idle" + (small ? " small" : "") + (REDUCED ? "" : " enter") });
    g.dataset.id = id;
    if (!REDUCED) g.style.animationDelay = `${i * 42}ms`;

    const pg = el("linearGradient", { id: `ng${i}`, x1: "0", y1: "0", x2: "0", y2: "1" });
    pg.appendChild(el("stop", { offset: "0%", "stop-color": "#ffffff", "stop-opacity": ".075" }));
    pg.appendChild(el("stop", { offset: "100%", "stop-color": "#ffffff", "stop-opacity": ".022" }));
    defs.appendChild(pg);

    const bg = el("linearGradient", { id: `bg${i}`, x1: "0", y1: "0", x2: "1", y2: "0" });
    bg.appendChild(el("stop", { offset: "0%", "stop-color": hue, "stop-opacity": "0" }));
    bg.appendChild(el("stop", { offset: "50%", "stop-color": "#ffffff" }));
    bg.appendChild(el("stop", { offset: "100%", "stop-color": hue }));
    defs.appendChild(bg);

    const sg = el("linearGradient", { id: `sg${i}`, x1: "0", y1: "0", x2: "1", y2: "0" });
    sg.appendChild(el("stop", { offset: "0%", "stop-color": "#fff", "stop-opacity": "0" }));
    sg.appendChild(el("stop", { offset: "50%", "stop-color": "#fff", "stop-opacity": ".28" }));
    sg.appendChild(el("stop", { offset: "100%", "stop-color": "#fff", "stop-opacity": "0" }));
    defs.appendChild(sg);

    g.appendChild(el("rect", { class: "plate", x, y, width: n.w, height: n.h, rx: r,
      fill: `url(#ng${i})`, stroke: hue, "stroke-opacity": ".26", "stroke-width": 1 }));
    // the "lit from above" specular edge — a fading highlight on the top border
    g.appendChild(el("path", { class: "specular", d: `M ${x + r} ${y + 1} H ${x + n.w - r}`, stroke: `url(#sg${i})`, "stroke-width": 1.5 }));

    const peri = 2 * (n.w + n.h) - 8 * r + 2 * Math.PI * r;
    const beam = el("rect", { class: "beam", x, y, width: n.w, height: n.h, rx: r,
      stroke: `url(#bg${i})`, "stroke-dasharray": `${(peri * .22).toFixed(1)} ${(peri * .78).toFixed(1)}` });
    beam.style.setProperty("--peri", `${peri.toFixed(1)}px`);
    g.appendChild(beam);

    const bw = meta.p.length * 8.2 + 15;
    g.appendChild(el("rect", { x: x + 13, y: y + 11, width: bw, height: 19, rx: 6,
      fill: hue, "fill-opacity": ".16", stroke: hue, "stroke-opacity": ".3" }));
    const pn = el("text", { class: "n-pnum", x: x + 13 + bw / 2, y: y + 21.2,
      "text-anchor": "middle", "dominant-baseline": "middle", fill: hue });
    pn.textContent = meta.p;
    g.appendChild(pn);

    const tick = el("g", { class: "tick", transform: `translate(${x + n.w - 25} ${y + 20})` });
    tick.appendChild(el("circle", { r: 9.5, fill: "#34d399", "fill-opacity": ".18" }));
    tick.appendChild(el("path", { d: "M -4 0 L -1.3 3 L 4.3 -3.3", fill: "none",
      stroke: "#34d399", "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    g.appendChild(tick);

    const ty = small ? n.y + 1 : n.y - 1;
    const t1 = el("text", { class: "n-title", x: n.x, y: ty, "text-anchor": "middle", "dominant-baseline": "middle" });
    t1.textContent = meta.title;
    const t2 = el("text", { class: "n-tag", x: n.x, y: ty + 19, "text-anchor": "middle", "dominant-baseline": "middle" });
    t2.textContent = meta.tag;
    g.appendChild(t1); g.appendChild(t2);

    nodesG.appendChild(g);
    nodeEls[id] = g;
    g.addEventListener("click", () => openInspector(id));
  });

  buildComet();
}

// ── the comet: a head plus a decaying trail sampled from recent positions ──
const TRAIL = 7;
let trailEls = [], trailBuf = [];
function buildComet() {
  const g = $("comet");
  trailEls = [];
  for (let i = TRAIL - 1; i >= 0; i--) {
    const c = el("circle", { r: 7 * (1 - i / (TRAIL + 1.5)), fill: "#dbe6ff",
      opacity: (1 - i / TRAIL) * .5 });
    g.appendChild(c); trailEls.unshift(c);
  }
  const halo = el("circle", { r: 17, fill: "rgba(150,180,255,.22)" });
  const head = el("circle", { r: 6.5, fill: "#eef3ff" });
  g.appendChild(halo); g.appendChild(head);
  trailEls.headEls = [halo, head];
}
function setCometColor(hex) {
  trailEls.forEach((c) => c.setAttribute("fill", hex));
  trailEls.headEls[0].setAttribute("fill", hex + "38");
  $("comet").style.filter = `drop-shadow(0 0 9px ${hex})`;
}
function moveComet(pt) {
  trailBuf.unshift(pt);
  if (trailBuf.length > TRAIL) trailBuf.pop();
  trailEls.headEls.forEach((c) => { c.setAttribute("cx", pt.x); c.setAttribute("cy", pt.y); });
  trailEls.forEach((c, i) => {
    const p = trailBuf[Math.min(i, trailBuf.length - 1)];
    c.setAttribute("cx", p.x); c.setAttribute("cy", p.y);
  });
}

// ═══════════ animation primitives ═══════════
const easeInOut = (p) => (p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
const dwell = (ms) => new Promise((r) => setTimeout(r, ms));
function tween(dur, onUpdate) {
  return new Promise((res) => {
    const t0 = performance.now();
    let done = false, raf = 0, timer = 0;
    (function step() {
      if (done) return;
      cancelAnimationFrame(raf); clearTimeout(timer);
      const p = Math.min(1, (performance.now() - t0) / dur);
      onUpdate(easeInOut(p));
      if (p < 1) { raf = requestAnimationFrame(step); timer = setTimeout(step, 100); }
      else { done = true; res(); }
    })();
  });
}

function setNode(id, state) {
  const e = nodeEls[id];
  if (!e) return;
  e.classList.remove("idle", "active", "exec", "done", "warn", "skip");
  e.classList.add(state);
  const hue = hueOf(id), plate = e.querySelector(".plate");
  const paint = (stroke, op, glow) => {
    plate.setAttribute("stroke", stroke);
    plate.setAttribute("stroke-opacity", op);
    plate.setAttribute("stroke-width", glow ? 1.5 : 1);
    e.style.filter = glow ? `drop-shadow(0 0 16px ${glow})` : "none";
  };
  if (state === "active")      paint("#9db4ff", ".95", "rgba(124,156,255,.55)");
  else if (state === "exec")   paint("#fbbf24", ".95", "rgba(251,191,36,.5)");
  else if (state === "done")   paint("#34d399", ".7",  "rgba(52,211,153,.28)");
  else if (state === "warn")   paint("#fb7185", ".9",  "rgba(251,113,133,.5)");
  else if (state === "skip")   paint(hue, ".18", null);
  else                         paint(hue, ".26", null);
}
const resetNodes = () => currentOrder.forEach((id) => setNode(id, "idle"));
// A retry re-runs the *solving* loop only. The prelude happens once per run, so
// clearing it on every retry would wrongly suggest the problem was re-fetched.
const resetSolvingNodes = () => currentOrder.forEach((id) => { if (!PRELUDE.includes(id)) setNode(id, "idle"); });

async function ghostTravel(from, to) {
  const a = nodePos[from], b = nodePos[to];
  if (!a || !b) return;
  const g = el("path", { class: "conn flowing", d: `M ${a.x} ${a.y} L ${b.x} ${b.y}`,
    stroke: "rgba(160,180,255,.45)", "stroke-dasharray": "4 6" });
  $("conns").appendChild(g);
  $("comet").classList.remove("hidden");
  setCometColor("#c7d5ff");
  await tween(340, (t) => moveComet({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }));
  g.remove();
}

async function travel(from, to) {
  let c = conns[`${from}->${to}`], reverse = false;
  if (!c) { c = conns[`${to}->${from}`]; reverse = true; }
  if (!c) {
    // Consecutive prelude hops (a curated problem skips extraction) walk the
    // real chain; anything else is an out-of-band call → transient ghost line.
    const i = currentOrder.indexOf(from), j = currentOrder.indexOf(to);
    if (i !== -1 && j > i && currentOrder.slice(i + 1, j).every((n) => PRELUDE.includes(n))) {
      for (let k = i; k < j; k++) await travel(currentOrder[k], currentOrder[k + 1]);
      return;
    }
    await ghostTravel(from, to);
    return;
  }
  $("comet").classList.remove("hidden");
  setCometColor(c.kind ? LOOP_HUE[c.kind] : hueOf(reverse ? from : to));
  c.el.classList.add("flowing"); c.glow.classList.add("flowing");
  trailBuf = [];
  await tween(c.kind ? 620 : 440, (t) => moveComet(pointOnConn(c, reverse ? 1 - t : t)));
  c.el.classList.remove("flowing"); c.glow.classList.remove("flowing");
  c.el.classList.add("traveled"); c.head.classList.add("traveled");
}

function setLoopBadge(kind, text) {
  const t = $(`badge-${kind}`), bg = loopBadgeBg[kind];
  if (!t) return;
  t.textContent = text;
  t.classList.toggle("hidden", !text);
  if (!bg) return;
  if (!text) { bg.classList.add("hidden"); return; }
  bg.classList.remove("hidden");
  const b = t.getBBox(), px = 9, py = 5;
  bg.setAttribute("x", b.x - px); bg.setAttribute("y", b.y - py);
  bg.setAttribute("width", b.width + px * 2); bg.setAttribute("height", b.height + py * 2);
  [t, bg].forEach((e) => { e.classList.remove("badge-pop"); void e.getBBox(); e.classList.add("badge-pop"); });
}

const modelDotEl = $("model-dot");
// What the vitals strip shows when no run is in flight: the *configured*
// model, not a placeholder. During a run it's replaced by the model each
// completion actually came back from.
let idleModelLabel = "swappable LLM";
const modelThink = (on) => modelDotEl.classList.toggle("think", on);
function setModelInfo(name, stat) {
  if (name) $("model-name").textContent = name;
  if (stat !== undefined) $("model-stat").textContent = stat;
}

// ═══════════════════════════════════════════════════════════════════════════
// Action queue — SSE events enqueue actions; one consumer plays them in order,
// so instant events still animate legibly and a slow real LLM call simply
// holds the "thinking" state until its ok arrives.
// ═══════════════════════════════════════════════════════════════════════════
let queue = [], waiter = null, runToken = 0;
let toolRoundInAttempt = 0, lastToolModelInitiated = false;
let currentMode = "multi", plannerJustRan = false;
let retryLoopCount = 0, sameplanLoopCount = 0, toolLoopCount = 0;

// Synchronous tagging — every event is stamped with its attempt (and, in multi
// mode, its subagent role) the instant it arrives, independent of the queue's
// pacing. Several kinds (approval_gate, gen_ai.tool, sandbox_exec…) don't carry
// attempt_no themselves, so this is what lets the feed attribute them.
let currentAttemptNo = 0, currentSubagentRole = null;
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
function pushAction(fn) { queue.push(fn); if (waiter) { const w = waiter; waiter = null; w(); } }
function nextAction() {
  if (queue.length) return Promise.resolve(queue.shift());
  return new Promise((res) => { waiter = () => res(queue.shift()); });
}
async function runConsumer(token) {
  while (token === runToken) {
    const fn = await nextAction();
    if (token !== runToken) return;
    try { await fn(); } catch (e) { console.error(e); }
  }
}

// ═══════════ inspector ═══════════
let currentRunEvents = [];
const K_PRELUDE = { fetch: "fetch", extract: "extract", budget: "budget", testgen: "testgen", judge: "judge" };
const K_SINGLE = { ...K_PRELUDE, instructions: "instructions", "gen_ai.completion": "provider",
  approval_gate: "approval", "gen_ai.tool": "tools", sandbox_exec: "sandbox",
  verification: "oracle", orchestrator: "orchestrator", run: "orchestrator" };
const K_MULTI = { ...K_PRELUDE, "gen_ai.completion": "provider", approval_gate: "approval",
  "gen_ai.tool": "tools", sandbox_exec: "sandbox", verification: "oracle",
  orchestrator: "planner", run: "planner" };
const kindToNode = (k) => (currentMode === "multi" ? K_MULTI : K_SINGLE)[k];
const SUBAGENT_ROLES = new Set(["planner", "coder", "tester"]);

function buildSubagentInspectorBody(role) {
  const spans = currentRunEvents.filter((e) => e.kind === "subagent" && (e.attrs || {}).role === role);
  const started = spans.filter((e) => e.status === "started").slice().reverse();
  if (!started.length) return `<div class="sheet-empty">No calls to this subagent yet in the current run.</div>`;
  return started.map((s) => {
    const end = spans.find((e) => e.span_id === s.span_id && e.status !== "started");
    const dur = end && end.duration_ms != null ? `${end.duration_ms.toFixed(1)}ms` : "";
    const status = end ? end.status : "started";
    return `<div class="icard">
      <div class="icard-top"><span class="icard-t">${role} · attempt ${s.attrs.attempt_no ?? "?"}</span>
        <span class="icard-m"><span class="st ${status}">${status}</span>${dur ? ` · ${dur}` : ""}</span></div>
      <div class="isec"><h4>Input</h4>${fmtAttrs(s.attrs)}</div>
      <div class="isec"><h4>Output</h4>${fmtAttrs((end && end.attrs) || {})}</div>
    </div>`;
  }).join("");
}

function fmtValue(key, val) {
  if (val == null) return `<span class="v-null">—</span>`;
  if (key === "messages" && Array.isArray(val))
    return val.map((m) => `<div class="msg"><div class="msg-role">${escapeHtml(m.role)}</div><pre>${escapeHtml(m.content)}</pre></div>`).join("");
  if (key === "failed_cases" && Array.isArray(val)) {
    if (!val.length) return `<span class="v-null">none</span>`;
    return val.map((fc) => {
      const d = fc.error ? `raised ${escapeHtml(fc.error)}`
        : `expected ${escapeHtml(JSON.stringify(fc.expected))} got ${escapeHtml(JSON.stringify(fc.actual))}`;
      return `<div class="fc-row">input=${escapeHtml(JSON.stringify(fc.input))} — ${d}</div>`;
    }).join("");
  }
  if (["code", "script"].includes(key) && typeof val === "string") return `<pre>${highlightPy(val)}</pre>`;
  if (["response_text", "stdout", "stderr", "kata_prompt"].includes(key) && typeof val === "string")
    return `<pre>${escapeHtml(val) || "(empty)"}</pre>`;
  if (typeof val === "string")
    return (val.length > 100 || val.includes("\n")) ? `<pre>${escapeHtml(val)}</pre>` : escapeHtml(val);
  if (typeof val === "object") return `<pre>${escapeHtml(JSON.stringify(val, null, 2))}</pre>`;
  return escapeHtml(String(val));
}
function fmtAttrs(attrs) {
  const es = Object.entries(attrs || {});
  if (!es.length) return `<div class="kv-empty">none</div>`;
  return es.map(([k, v]) => `<div class="kv"><div class="kv-k">${escapeHtml(k)}</div><div class="kv-v">${fmtValue(k, v)}</div></div>`).join("");
}

function buildInspectorBody(nodeId) {
  const spans = new Map();
  for (const evt of currentRunEvents) {
    if (kindToNode(evt.kind) !== nodeId) continue;
    if (evt.kind === "orchestrator" && evt.name !== "attempt") continue;
    if (!spans.has(evt.span_id)) spans.set(evt.span_id, { name: evt.name, start: null, end: null, attemptNo: evt._attemptNo });
    const s = spans.get(evt.span_id);
    if (evt.status === "started") s.start = evt; else s.end = evt;
  }
  const groups = [...spans.values()].sort((a, b) => (b.start || b.end).timestamp - (a.start || a.end).timestamp);
  if (!groups.length) return `<div class="sheet-empty">No calls to this primitive yet in the current run.</div>`;
  return groups.map((g) => {
    const sa = (g.start && g.start.attrs) || {}, ea = (g.end && g.end.attrs) || {};
    const out = {};
    for (const [k, v] of Object.entries(ea)) if (!(k in sa) || JSON.stringify(sa[k]) !== JSON.stringify(v)) out[k] = v;
    const status = g.end ? g.end.status : "started";
    const dur = g.end && g.end.duration_ms != null ? `${g.end.duration_ms.toFixed(1)}ms` : "";
    const title = g.name === "attempt" ? `attempt ${g.attemptNo}` : `${g.name} · attempt ${g.attemptNo}`;
    return `<div class="icard">
      <div class="icard-top"><span class="icard-t">${escapeHtml(title)}</span>
        <span class="icard-m"><span class="st ${status}">${status}</span>${dur ? ` · ${dur}` : ""}</span></div>
      <div class="isec"><h4>Input</h4>${fmtAttrs(sa)}</div>
      <div class="isec"><h4>Output</h4>${fmtAttrs(out)}</div>
    </div>`;
  }).join("");
}

let sheetEl = null;
function ensureSheet() {
  if (sheetEl) return sheetEl;
  const d = document.createElement("div");
  d.className = "ov hidden";
  d.innerHTML = `<div class="sheet">
      <div class="sheet-top">
        <div><div class="sheet-tag" id="sheet-tag"></div><div class="sheet-title" id="sheet-title"></div></div>
        <button class="sheet-x" aria-label="close">✕</button>
      </div>
      <div class="sheet-body" id="sheet-body"></div>
    </div>`;
  document.body.appendChild(d);
  d.addEventListener("click", (e) => { if (e.target === d) closeSheet(); });
  d.querySelector(".sheet-x").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  sheetEl = d;
  return d;
}
const closeSheet = () => sheetEl && sheetEl.classList.add("hidden");
function openSheet(tag, title, body) {
  const d = ensureSheet();
  $("sheet-tag").textContent = tag; $("sheet-title").textContent = title;
  $("sheet-body").innerHTML = body;
  d.classList.remove("hidden");
}
function openInspector(nodeId) {
  const m = NODE_META[nodeId];
  openSheet(`${m.p} · ${m.tag}`, m.title,
    SUBAGENT_ROLES.has(nodeId) ? buildSubagentInspectorBody(nodeId) : buildInspectorBody(nodeId));
}

// ═══════════════════════════════════════════════════════════════════════════
// Story narration — raw trace events into the plain-English sentences that
// explain why a small model can land a hard problem: the harness caught its
// mistake, told it exactly what was wrong, and made it try again.
// ═══════════════════════════════════════════════════════════════════════════
function narrate(evt) {
  const a = evt.attrs || {};
  if (evt.kind === "fetch" && evt.status === "ok") {
    if (a.ok === false) return { icon: "🚫", text: `Could not fetch the problem: ${a.error}` };
    const d = a.difficulty && a.difficulty !== "unknown" ? ` (${a.difficulty})` : "";
    return { icon: "🌐", text: `Fetched "${a.title}"${d} from ${a.source}.` };
  }
  if (evt.kind === "extract" && evt.status === "ok") {
    const how = a.extraction_method === "deterministic"
      ? "parsed straight out of the statement — no model call needed"
      : "extracted by the model from the raw statement";
    const entry = a.kind === "design" ? `class ${a.entry_point}` : `${a.entry_point}()`;
    return { icon: "🔍", text: `Worked out the entry point (${entry}) and ${a.official_case_count} ground-truth test case(s) — ${how}.` };
  }
  if (evt.kind === "budget" && evt.status === "ok") {
    if (!a.max_n) return { icon: "📐", text: "No input bound stated, so no complexity target could be derived." };
    const slow = (a.too_slow || []).length ? ` — ${(a.too_slow || []).join(", ")} would time out` : "";
    return { icon: "📐", text: `Constraints allow n up to ${Number(a.max_n).toLocaleString()}, so the Coder is told to target ${a.acceptable} or better${slow}.` };
  }
  if (evt.kind === "testgen" && evt.status === "ok") {
    const dropped = a.dropped_count ? ` (${a.dropped_count} discarded for violating the constraints)` : "";
    if (!a.kept_count) return { icon: "🧪", text: `No extra test cases were generated${dropped}.` };
    return { icon: "🧪", text: `Generated ${a.kept_count} extra edge/happy-path case(s) that respect the stated constraints${dropped}.` };
  }
  if (evt.kind === "judge" && evt.status === "ok") {
    const v = a.verdicts || {}, parts = [];
    if (v.bad_test) parts.push(`${v.bad_test} were bad test case(s), discarded`);
    if (v.real_bug) parts.push(`${v.real_bug} exposed a real bug`);
    if (v.uncertain) parts.push(`${v.uncertain} were inconclusive`);
    return { icon: "⚖️", text: `Judge reviewed ${a.disagreement_count} disagreement(s) on generated cases: ${parts.join(", ") || "no verdict"}.` };
  }
  if (evt.kind === "orchestrator" && evt.name === "attempt" && evt.status === "started") {
    const n = a.attempt_no || evt._attemptNo;
    return n === 1 ? { icon: "▶️", text: "Asking the model to solve it from scratch." }
                   : { icon: "🔁", text: `Retrying — attempt ${n}, with the previous failure fed back into the prompt.` };
  }
  if (evt.kind === "subagent" && evt.status === "ok") {
    if (a.role === "planner") return { icon: "🗺️", text: `Planner drafted a ${(a.plan_steps || []).length}-step plan — no code yet, just a strategy.` };
    if (a.role === "coder") {
      const tools = (a.tools_used || []).filter((t) => t !== "run_tests");
      return { icon: "👨‍💻", text: `Coder wrote an implementation${tools.length ? ` (self-checked with: ${tools.join(", ")})` : ""}.` };
    }
    if (a.role === "tester") return a.oracle_passed
      ? { icon: "✅", text: "Tester ran the official oracle — every case passed." }
      : { icon: "❌", text: `Tester ran the official oracle — ${a.score != null ? Math.round((1 - a.score) * 100) + "%" : "some"} of cases failed.` };
  }
  if (evt.kind === "gen_ai.tool" && evt.status === "started")
    return a.model_initiated
      ? { icon: "🔧", text: `Model chose to call \`${a["gen_ai.tool.name"]}\` itself, to double-check its own answer before committing.` }
      : { icon: "🛡️", text: `Harness runs its own authoritative \`${a["gen_ai.tool.name"]}\` check — the model's word alone is never trusted.` };
  if (evt.kind === "verification" && evt.status === "ok") {
    // The Tester verifies each tier separately, so this fires twice per attempt.
    // Only the ground-truth run decides pass/fail; the generated run is a
    // different (weaker) claim and is narrated as such.
    if (a.case_tier === "generated") return a.oracle_passed
      ? { icon: "🧪", text: "The generated edge cases all agreed with the code too." }
      : { icon: "🤔", text: `${a.failed_case_count ?? "Some"} generated case(s) disagreed with the code — sending them to the Judge, since a generated case can be wrong itself.` };
    return a.oracle_passed
      ? { icon: "✅", text: "Oracle verdict on the ground-truth cases: all passed." }
      : { icon: "❌", text: `Oracle verdict on the ground-truth cases: ${a.failed_case_count ?? "some"} failed — this is what gets fed back to the model.` };
  }
  if (evt.kind === "skill" && evt.name === "skill_loaded")
    return { icon: "📘", text: `Loaded the "${a.skill}" technique notes into context (kata category: ${a.kata_category}) — extra know-how the base model doesn't have baked in.` };
  if (evt.kind === "memory" && evt.name === "memory_recall")
    return { icon: "🧠", text: `Recalled a hint from a previous run: ${a.hint}` };
  if (evt.kind === "gen_ai.completion" && evt.status === "ok" && (a.safety_retries || 0) > 0)
    return { icon: "⚠️", text: `The provider routed the request to a safety/moderation model instead of a real one — the harness detected that and retried ×${a.safety_retries}.` };
  return null;
}

// ═══════════ event → action ═══════════
function handleEvent(evt) {
  tagEvent(evt);
  updateVitals(evt);
  currentRunEvents.push(evt);
  appendLog(evt);
  const a = evt.attrs || {};

  switch (`${evt.kind}:${evt.status}`) {
    case "fetch:started":
      pushAction(async () => setNode("fetch", "active")); break;
    case "fetch:ok":
      pushAction(async () => { setNode("fetch", a.ok === false ? "warn" : "done"); await dwell(130); }); break;
    case "extract:started":
      pushAction(async () => { await travel("fetch", "extract"); setNode("extract", "active"); }); break;
    case "extract:ok":
      pushAction(async () => { setNode("extract", "done"); await dwell(110); }); break;
    case "budget:started":
      pushAction(async () => {
        // Curated problems skip extraction, so come from whichever ran last.
        const from = nodeEls.extract && nodeEls.extract.classList.contains("idle") ? "fetch" : "extract";
        await travel(from, "budget"); setNode("budget", "active");
      }); break;
    case "budget:ok":
      // No stated input bound is "nothing to derive", not a failure.
      pushAction(async () => { setNode("budget", a.max_n ? "done" : "skip"); await dwell(110); }); break;
    case "testgen:started":
      pushAction(async () => { await travel("budget", "testgen"); setNode("testgen", "active"); }); break;
    case "testgen:ok":
      pushAction(async () => { setNode("testgen", a.kept_count ? "done" : "skip"); await dwell(110); }); break;
    case "judge:started":
      pushAction(async () => { await travel("oracle", "judge"); setNode("judge", "active"); }); break;
    case "judge:ok":
      pushAction(async () => { setNode("judge", (a.verdicts || {}).real_bug ? "warn" : "done"); await dwell(150); }); break;

    case "orchestrator:started":
      if (evt.name !== "attempt") break;
      pushAction(async () => {
        const n = a.attempt_no || evt._attemptNo;
        toolRoundInAttempt = 0;
        if (n > 1) {
          retryLoopCount++;
          // Only the multi-mode "replanned" retry is decided later (by whether
          // Planner actually runs); in single mode every retry takes this edge.
          if (currentMode === "single") {
            setNode("oracle", "idle");
            await travel("oracle", "orchestrator");
            resetSolvingNodes();
            setLoopBadge("retry", `retry ×${retryLoopCount}`);
          }
        } else if (currentMode === "single") {
          await travel("testgen", "orchestrator");
        }
        if (currentMode === "single") setNode("orchestrator", "active");
      }); break;

    case "instructions:ok":
      pushAction(async () => {
        setNode("orchestrator", "done");
        await travel("orchestrator", "instructions");
        setNode("instructions", "active"); await dwell(150);
      }); break;

    case "subagent:started":
      pushAction(async () => {
        const role = a.role, attemptNo = evt._attemptNo;
        toolRoundInAttempt = 0;
        if (role === "planner") {
          plannerJustRan = true;
          if (attemptNo > 1) {
            retryLoopCount++;
            setNode("oracle", "idle");
            await travel("oracle", "planner");
            resetSolvingNodes();
            setLoopBadge("retry", `retry ×${retryLoopCount}`);
          } else await travel("testgen", "planner");
          setNode("planner", "active");
        } else if (role === "coder") {
          if (plannerJustRan) { setNode("planner", "done"); await travel("planner", "coder"); }
          else if (attemptNo > 1) {
            sameplanLoopCount++;
            setNode("tester", "idle");
            ["provider", "approval", "tools", "sandbox"].forEach((id) => setNode(id, "idle"));
            await travel("tester", "coder");
            setLoopBadge("sameplan", `same plan ×${sameplanLoopCount}`);
          }
          plannerJustRan = false;
          setNode("coder", "active");
        } else if (role === "tester") {
          setNode("coder", "done"); await travel("coder", "tester"); setNode("tester", "active");
        }
      }); break;

    case "subagent:ok":
      pushAction(async () => {
        if (a.role === "tester") setNode("tester", a.oracle_passed === false ? "warn" : "done");
        else setNode(a.role, "done");
      }); break;

    case "gen_ai.completion:started":
      pushAction(async () => {
        toolRoundInAttempt++;
        const from = toolRoundInAttempt === 1 ? (currentMode === "single" ? "instructions" : evt._role) : "tools";
        const useLoop = !MAIN_PATH_INTO_PROVIDER.has(from);
        setNode(from, "done");
        await travel(from, "provider");
        if (useLoop) { toolLoopCount++; setLoopBadge("toolloop", `asked again ×${toolLoopCount}`); }
        setNode("provider", "active");
        modelThink(true);
      }); break;

    case "gen_ai.completion:ok":
      pushAction(async () => {
        const model = a["gen_ai.response.model"] || "LLM";
        const toks = a["gen_ai.usage.output_tokens"] ?? "?";
        const lat = a["latency_ms"] != null ? `${Math.round(a["latency_ms"])}ms` : "";
        setModelInfo(model, `${toks} out · ${lat}`);
        modelThink(false);
        if ((a["safety_retries"] || 0) > 0) {
          modelDotEl.classList.add("warnflash"); await dwell(520); modelDotEl.classList.remove("warnflash");
        }
      }); break;

    case "approval_gate:ok":
      pushAction(async () => {
        setNode("provider", "done");
        await travel("provider", "approval");
        setNode("approval", "active"); await dwell(160); setNode("approval", "done");
      }); break;

    case "gen_ai.tool:started":
      pushAction(async () => {
        lastToolModelInitiated = !!a.model_initiated;
        await travel("approval", "tools");
        setNode("tools", "active"); await dwell(100);
      }); break;

    case "sandbox_exec:started":
      pushAction(async () => {
        setNode("tools", "done"); await travel("tools", "sandbox"); setNode("sandbox", "exec");
      }); break;

    case "gen_ai.tool:ok":
      pushAction(async () => setNode("tools", "done")); break;

    case "verification:ok":
      pushAction(async () => {
        setNode("sandbox", "done");
        await travel("sandbox", "oracle");
        setNode("oracle", "active"); await dwell(160);
        // Only the ground-truth tier decides the Oracle's verdict. A generated
        // case disagreeing is a question for the Judge, not an oracle failure,
        // so it must not paint this node red.
        if (a.case_tier === "generated") { setNode("oracle", "done"); return; }
        setNode("oracle", a.oracle_passed ? "done" : "warn");
        // Update the attempt's badge as soon as its verdict is known: a
        // self-check earlier in the attempt may be overridden by the harness's
        // own authoritative check, and events arrive in order, so the last wins.
        const g = logGroups[evt._attemptNo];
        if (g) {
          g.badgeEl.className = "log-group-badge " + (a.oracle_passed ? "pass" : "fail");
          g.badgeEl.textContent = a.oracle_passed ? "pass" : "fail";
        }
      }); break;

    case "run:ok":
      pushAction(async () => {
        modelThink(false);
        $("comet").classList.add("hidden");
        // The last LLM call of a run has no following step to mark Provider
        // done, so it would otherwise be left pulsing after the run ended.
        if (nodeEls.provider && nodeEls.provider.classList.contains("active")) setNode("provider", "done");
      }); break;
  }
}

// ═══════════ story feed ═══════════
const logEl = $("log");
let logCount = 0, logGroups = {}, currentLogGroupNo = null;

function ensureLogGroup(no) {
  if (logGroups[no]) return logGroups[no];
  if (logEl.querySelector(".blank")) logEl.innerHTML = "";
  if (currentLogGroupNo != null && logGroups[currentLogGroupNo]) logGroups[currentLogGroupNo].el.classList.add("collapsed");
  const d = document.createElement("div");
  d.className = "log-group";
  d.innerHTML = `<div class="log-group-head">
      <span class="log-group-chev">▼</span>
      <span class="log-group-title">${no === 0 ? "Preparing the problem" : `Attempt ${no}`}</span>
      <span class="log-group-badge running">running</span>
      <span class="log-group-count">0 events</span>
    </div>
    <div class="log-group-body">
      <div class="story-list"></div>
      <div class="raw-toggle">show raw trace (0)</div>
      <div class="raw-list hidden"></div>
    </div>`;
  logEl.appendChild(d);
  d.querySelector(".log-group-head").addEventListener("click", () => d.classList.toggle("collapsed"));
  const rt = d.querySelector(".raw-toggle"), rl = d.querySelector(".raw-list");
  rt.addEventListener("click", (e) => {
    e.stopPropagation(); rl.classList.toggle("hidden");
    rt.textContent = `${rl.classList.contains("hidden") ? "show" : "hide"} raw trace (${g.rawCount})`;
  });
  const g = { el: d, storyEl: d.querySelector(".story-list"), rawEl: rl, rawToggleEl: rt,
    badgeEl: d.querySelector(".log-group-badge"), countEl: d.querySelector(".log-group-count"), rawCount: 0, storyCount: 0 };
  if (no === 0) g.badgeEl.classList.add("hidden");
  logGroups[no] = g; currentLogGroupNo = no;
  return g;
}

function appendLog(evt) {
  const g = ensureLogGroup(evt._attemptNo);
  const raw = document.createElement("div");
  raw.className = "log-line";
  const dur = evt.duration_ms != null ? `${evt.duration_ms.toFixed(1)}ms` : "";
  raw.innerHTML = `<span class="st ${evt.status}">${evt.status}</span><span class="nm">${escapeHtml(evt.name)}</span><span class="du">${dur}</span>`;
  raw.addEventListener("click", () => openLogDetail(evt));
  g.rawEl.appendChild(raw);
  g.rawCount++;
  g.rawToggleEl.textContent = `${g.rawEl.classList.contains("hidden") ? "show" : "hide"} raw trace (${g.rawCount})`;

  const note = narrate(evt);
  if (note) {
    const s = document.createElement("div");
    s.className = "story-line";
    s.style.animationDelay = `${Math.min(g.storyCount++, 6) * 24}ms`;
    s.innerHTML = `<span class="story-icon">${note.icon}</span><span class="story-text">${inlineCode(note.text)}</span>`;
    s.title = "click to see the underlying event";
    s.addEventListener("click", () => openLogDetail(evt));
    g.storyEl.appendChild(s);
  }
  g.countEl.textContent = `${g.rawCount} events`;
  const pane = $("pane-story");
  pane.scrollTop = pane.scrollHeight;
  $("tab-badge-story").textContent = ++logCount;
}
function finalizeLogGroups(result) {
  result.attempts.forEach((at) => {
    const g = logGroups[at.attempt_no];
    if (!g) return;
    g.badgeEl.className = "log-group-badge " + (at.report.oracle_passed ? "pass" : "fail");
    g.badgeEl.textContent = at.report.oracle_passed ? "pass" : "fail";
  });
}
function openLogDetail(evt) {
  const meta = { span_id: evt.span_id, parent_span_id: evt.parent_span_id, kind: evt.kind, timestamp: evt.timestamp };
  openSheet(evt.kind, evt.name, `<div class="icard">
      <div class="icard-top"><span class="icard-t">${escapeHtml(evt.name)}</span>
        <span class="icard-m"><span class="st ${evt.status}">${evt.status}</span>${evt.duration_ms != null ? ` · ${evt.duration_ms.toFixed(1)}ms` : ""}</span></div>
      <div class="isec"><h4>Meta</h4>${fmtAttrs(meta)}</div>
      <div class="isec"><h4>Attrs</h4>${fmtAttrs(evt.attrs)}</div>
    </div>`);
}

// ═══════════════════════════════════════════════════════════════════════════
// Vitals — driven straight off the event stream, NOT off the animation queue.
// The queue deliberately paces the diagram so a run that finishes in 44ms is
// still watchable; the numbers must describe the run, not the replay.
// ═══════════════════════════════════════════════════════════════════════════
let liveTokens = 0, runStartedAt = 0, elapsedTimer = null, liveToolRound = 0;

function startElapsed() {
  if (elapsedTimer) return;
  runStartedAt = performance.now();
  elapsedTimer = setInterval(() => { $("stat-elapsed").textContent = fmtMs(performance.now() - runStartedAt); }, 90);
}
function stopElapsed(finalMs) {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  if (finalMs != null) $("stat-elapsed").textContent = fmtMs(finalMs);
}
function setStatus(s) {
  const p = $("status-pill");
  p.className = "vital status " + s;
  p.innerHTML = `<i></i>${s}`;
}
const setAttempt = (n) => ($("stat-attempt").textContent = n);
const setToolRound = (n) => ($("stat-round").textContent = n > 0 ? n : "—");

// Count a number up rather than snapping — makes token/cost growth legible.
function tickTo(node, target, fmt) {
  const from = parseFloat(node.dataset.v || "0");
  node.dataset.v = target;
  if (REDUCED || Math.abs(target - from) < 1e-9) { node.textContent = fmt(target); return; }
  const t0 = performance.now(), dur = 480;
  (function step() {
    const p = Math.min(1, (performance.now() - t0) / dur);
    node.textContent = fmt(from + (target - from) * (1 - Math.pow(1 - p, 3)));
    if (p < 1) requestAnimationFrame(step);
  })();
}
function addLiveTokens(i, o) {
  liveTokens += (i || 0) + (o || 0);
  tickTo($("stat-tokens"), liveTokens, (v) => Math.round(v).toLocaleString());
}
function updateVitals(evt) {
  const a = evt.attrs || {};
  if (evt.kind === "orchestrator" && evt.name === "attempt" && evt.status === "started") {
    setAttempt(a.attempt_no || evt._attemptNo); liveToolRound = 0; setToolRound(0);
  }
  if (evt.kind === "subagent" && evt.status === "started") { liveToolRound = 0; setToolRound(0); }
  if (evt.kind === "gen_ai.completion" && evt.status === "started") setToolRound(++liveToolRound);
  if (evt.kind === "gen_ai.completion" && evt.status === "ok")
    addLiveTokens(a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]);
}

// ═══════════ formatting ═══════════
const escapeHtml = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const inlineCode = (s) => escapeHtml(s).replace(/`([^`]+)`/g, (_, m) => `<code>${m}</code>`);
function fmtMs(ms) {
  if (ms == null) return "—";
  if (ms >= 60000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

// Minimal Python highlighter — the code under review is a few dozen lines, so a
// single-pass tokenizer beats pulling a library into a vanilla-JS project.
const PY_KW = "def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|lambda|yield|pass|break|continue|None|True|False|self|is|assert|global|nonlocal|async|await|del|print";
const PY_RE = new RegExp(
  `(#[^\\n]*)|("""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*')|\\b(${PY_KW})\\b|\\b(\\d+\\.?\\d*)\\b|\\b([A-Za-z_]\\w*)(?=\\()`, "g");
function highlightPy(src) {
  let out = "", last = 0, m;
  PY_RE.lastIndex = 0;
  while ((m = PY_RE.exec(src)) !== null) {
    out += escapeHtml(src.slice(last, m.index));
    const cls = m[1] ? "tk-c" : m[2] ? "tk-s" : m[3] ? "tk-k" : m[4] ? "tk-n" : "tk-f";
    out += `<span class="${cls}">${escapeHtml(m[0])}</span>`;
    last = PY_RE.lastIndex;
  }
  return out + escapeHtml(src.slice(last));
}

// Classic LCS line diff (no library — kata code is a few dozen lines, so the
// O(n*m) table is trivial).
function diffLines(o, n) {
  const a = o.split("\n"), b = n.split("\n"), N = a.length, M = b.length;
  const dp = Array.from({ length: N + 1 }, () => new Array(M + 1).fill(0));
  for (let i = N - 1; i >= 0; i--) for (let j = M - 1; j >= 0; j--)
    dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const ops = []; let i = 0, j = 0;
  while (i < N && j < M) {
    if (a[i] === b[j]) { ops.push({ t: "same", x: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: "remove", x: a[i] }); i++; }
    else { ops.push({ t: "add", x: b[j] }); j++; }
  }
  while (i < N) ops.push({ t: "remove", x: a[i++] });
  while (j < M) ops.push({ t: "add", x: b[j++] });
  return ops;
}
const MARK = { same: " ", add: "+", remove: "−" };
const dlHtml = (op) => `<span class="dl ${op.t}">${MARK[op.t]} ${highlightPy(op.x)}</span>`;
const renderDiff = (o, n) => `<pre class="diff">${diffLines(o, n).map(dlHtml).join("\n")}</pre>`;
function renderDiffPane(o, n, side) {
  const keep = side === "left" ? (t) => t !== "add" : (t) => t !== "remove";
  return `<pre class="diff">${diffLines(o, n).filter((op) => keep(op.t)).map(dlHtml).join("\n")}</pre>`;
}
function failCasesHtml(at) {
  if (at.report.oracle_passed) return "";
  return at.report.failed_cases.slice(0, 5).map((fc) => {
    const d = fc.error ? `raised ${fc.error}` : `expected ${JSON.stringify(fc.expected)} got ${JSON.stringify(fc.actual)}`;
    return `<div class="failcase">input=${escapeHtml(JSON.stringify(fc.input))} — ${escapeHtml(d)}</div>`;
  }).join("");
}

// ═══════════ result pane ═══════════
let lastResult = null, openAttempts = [];
function renderResult(r) {
  lastResult = r; openAttempts = [];
  finalizeLogGroups(r);
  const b = $("tab-badge-result");
  b.textContent = r.passed ? "pass" : "fail";
  b.className = "tab-badge " + (r.passed ? "pass" : "fail");
  renderAttempts();
}
function toggleAttempt(no) {
  const i = openAttempts.indexOf(no);
  if (i !== -1) openAttempts.splice(i, 1);
  else { openAttempts.push(no); if (openAttempts.length > 2) openAttempts.shift(); }
  renderAttempts();
}
function headHtml(at, extra, chev) {
  const ok = at.report.oracle_passed, sr = at.completion && at.completion.safety_retries;
  return `<div class="head" data-attempt="${at.attempt_no}">
      <span class="head-l"><span class="chev">${chev}</span><span>Attempt ${at.attempt_no}</span>${extra}
        ${sr ? `<span class="sbadge">safety-retry ×${sr}</span>` : ""}</span>
      <span class="verd ${ok ? "pass" : "fail"}">${ok ? "pass" : "fail"}</span>
    </div>`;
}
function attemptHtml(at, prev, open) {
  const extra = open ? `<span class="dbadge">${prev ? `diff vs attempt ${prev.attempt_no}` : "first attempt"}</span>` : "";
  let h = `<div class="attempt ${open ? "open" : ""}">` + headHtml(at, extra, "▸");
  if (open) h += `<div class="attempt-body">${prev ? renderDiff(prev.code, at.code) : `<pre class="code">${highlightPy(at.code)}</pre>`}${failCasesHtml(at)}</div>`;
  return h + `</div>`;
}
const cpaneHtml = (at, other, side) => `<div class="cpane">${headHtml(at, "", "▾")}
    <div class="attempt-body">${renderDiffPane(side === "left" ? at.code : other.code, side === "left" ? other.code : at.code, side)}${failCasesHtml(at)}</div>
  </div>`;

function renderAttempts() {
  const r = lastResult, node = $("result");
  let h = `<div class="verdict ${r.passed ? "pass" : "fail"}">
      <span class="v-mark">${r.passed ? "✓" : "✕"}</span>
      <div><div class="v-title">${r.passed ? "Solved" : "Not solved"}</div>
        <div class="v-sub">${r.attempts.length} attempt(s) · ${fmtMs(r.total_duration_ms)}</div></div>
    </div>`;
  if (r.attempts.length > 1 && openAttempts.length !== 2)
    h += `<div class="hint-row">expand any two attempts to compare them directly</div>`;
  h += `<div class="attempts">`;
  if (openAttempts.length === 2) {
    const [a, b] = [...openAttempts].sort((x, y) => x - y).map((n) => r.attempts.find((at) => at.attempt_no === n));
    h += `<div class="hint-row">comparing attempt ${a.attempt_no} → attempt ${b.attempt_no}</div>`;
    h += `<div class="cgrid">${cpaneHtml(a, b, "left")}${cpaneHtml(b, a, "right")}</div>`;
    r.attempts.forEach((at) => { if (at.attempt_no !== a.attempt_no && at.attempt_no !== b.attempt_no) h += attemptHtml(at, null, false); });
  } else {
    r.attempts.forEach((at, i) => h += attemptHtml(at, i > 0 ? r.attempts[i - 1] : null, openAttempts.includes(at.attempt_no)));
  }
  node.innerHTML = h + `</div>`;
  node.querySelectorAll(".head[data-attempt]").forEach((e) =>
    e.addEventListener("click", () => toggleAttempt(parseInt(e.dataset.attempt, 10))));
}

// ═══════════════════════════════════════════════════════════════════════════
// Problem pane — the question, plus everything the harness derived from it
// before writing a line of code: the entry point, what the constraints imply
// about acceptable complexity, and exactly which cases it will be graded on
// (labelled by tier, so ground truth vs. invented is never ambiguous).
// ═══════════════════════════════════════════════════════════════════════════
function renderProblem(p) {
  // "parsed from the statement" and "written by the model" are very different
  // trust levels, so which route ran is stated outright.
  const METHOD = { curated: "hand-written · offline", deterministic: "parsed from the statement · no model call",
    llm: "model-extracted", "llm-retry": "model-extracted (retried)" };
  const diff = (p.difficulty || "unknown").toLowerCase();
  const tags = [`<span class="chip ${diff}">${escapeHtml(diff)}</span>`,
    `<span class="chip kind">${escapeHtml(p.kind)}</span>`,
    ...(p.topics || []).slice(0, 4).map((t) => `<span class="chip">${escapeHtml(t)}</span>`)].join("");

  const b = p.budget || {};
  const budget = b.acceptable && b.acceptable !== "unknown"
    ? `<div class="budget">
         <div class="budget-head"><span class="budget-target">${escapeHtml(b.acceptable)}</span>
           <span>or better${b.max_n ? ` · n up to ${Number(b.max_n).toLocaleString()}` : ""}</span></div>
         <div class="budget-why">${escapeHtml(b.reasoning || "")}</div>
         ${(b.too_slow || []).length ? `<div class="budget-slow">would time out: ${escapeHtml(b.too_slow.join(", "))}</div>` : ""}
       </div>`
    : `<div class="budget"><div class="budget-why">${escapeHtml(b.reasoning || "No complexity target could be derived.")}</div></div>`;

  const cases = (p.test_cases || []).map((c) => `<tr>
      <td><span class="tier ${c.source}">${c.source === "generated" ? "gen" : "truth"}</span></td>
      <td class="val">${escapeHtml(JSON.stringify(c.input))}</td>
      <td class="val">${escapeHtml(JSON.stringify(c.expected))}</td>
      <td class="why">${escapeHtml(c.rationale || "")}</td></tr>`).join("");
  const truth = (p.test_cases || []).filter((c) => c.source !== "generated").length;
  const gen = (p.test_cases || []).length - truth;

  $("problem-body").innerHTML = `
    <div class="p-title">${escapeHtml(p.title)}</div>
    <div class="p-src">${escapeHtml(p.source)} · ${escapeHtml(METHOD[p.extraction_method] || p.extraction_method || "")}</div>
    <div class="p-tags">${tags}${p.url ? `<a class="p-link" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">view source ↗</a>` : ""}</div>
    <div class="statement collapsed" id="statement">${escapeHtml(p.statement_text || "")}</div>
    <span class="statement-toggle" id="statement-toggle">show full statement</span>
    <div class="sec">Entry point the harness must implement</div>
    <pre class="code">${highlightPy(p.signature || p.starter_code || "—")}</pre>
    ${(p.constraints || []).length ? `<div class="sec">Constraints</div>
      <ul class="constraints">${p.constraints.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}
    <div class="sec">Complexity budget · derived, P17</div>${budget}
    <div class="sec">Test cases · ${truth} ground truth + ${gen} generated</div>
    <table class="cases"><thead><tr><th>tier</th><th>input</th><th>expected</th><th>why</th></tr></thead>
      <tbody>${cases || `<tr><td colspan="4" class="why">none</td></tr>`}</tbody></table>`;

  const st = $("statement"), tg = $("statement-toggle");
  tg.addEventListener("click", () => {
    st.classList.toggle("collapsed");
    tg.textContent = st.classList.contains("collapsed") ? "show full statement" : "show less";
  });
  $("problem-meta").textContent = `${p.title} · ${diff} · ${p.kind}`;
}

// ═══════════ cost pane ═══════════
function renderMetrics(m, result) {
  const pct = (v) => (m.wall_ms ? Math.max(0, (v / m.wall_ms) * 100) : 0);
  const other = Math.max(0, m.wall_ms - m.llm_ms - m.sandbox_ms);
  const agents = Object.entries(m.per_agent || {}).sort((a, b) => b[1].total_tokens - a[1].total_tokens)
    .map(([role, u]) => `<tr><td class="role">${escapeHtml(role)}</td><td>${u.calls}</td>
      <td>${u.input_tokens.toLocaleString()}</td><td>${u.output_tokens.toLocaleString()}</td><td>${fmtMs(u.latency_ms)}</td></tr>`).join("");

  tickTo($("stat-cost"), m.cost_usd, (v) => `$${v.toFixed(4)}`);
  tickTo($("stat-tokens"), m.total_tokens, (v) => Math.round(v).toLocaleString());

  $("metrics-body").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="kpi-v">${fmtMs(m.wall_ms)}</div><div class="kpi-l">wall time</div><div class="kpi-s">${m.llm_calls} llm calls</div></div>
      <div class="kpi ${result && result.passed ? "good" : "bad"}"><div class="kpi-v">${m.attempts}</div><div class="kpi-l">attempts</div>
        <div class="kpi-s">${result && result.passed ? "solved" : "unsolved"}</div></div>
      <div class="kpi"><div class="kpi-v">${(m.total_tokens / 1000).toFixed(1)}k</div><div class="kpi-l">tokens</div>
        <div class="kpi-s">${m.input_tokens.toLocaleString()} in · ${m.output_tokens.toLocaleString()} out</div></div>
      <div class="kpi hero"><div class="kpi-v">$${m.cost_usd.toFixed(4)}</div><div class="kpi-l">cost</div>
        <div class="kpi-s" title="${escapeHtml((m.models_used || []).join(", "))}">${escapeHtml((m.models_used || []).join(", ") || "—")}</div></div>
    </div>
    <div class="sec">Where the wall time went</div>
    <div class="split" role="img" aria-label="time split between model, sandbox and harness">
      <div class="seg-bar s1" style="width:${pct(m.llm_ms)}%"></div>
      <div class="seg-bar s2" style="width:${pct(m.sandbox_ms)}%"></div>
      <div class="seg-bar s3" style="width:${pct(other)}%"></div>
    </div>
    <div class="split-key">
      <span><i style="background:var(--series-1)"></i>model <b>${fmtMs(m.llm_ms)}</b></span>
      <span><i style="background:var(--series-2)"></i>sandbox <b>${fmtMs(m.sandbox_ms)}</b></span>
      <span><i style="background:var(--series-3)"></i>harness <b>${fmtMs(other)}</b></span>
    </div>
    <div class="sec">Tokens by agent</div>
    <table class="data"><thead><tr><th>agent</th><th>calls</th><th>in</th><th>out</th><th>time</th></tr></thead>
      <tbody>${agents || `<tr><td colspan="5">no model calls</td></tr>`}</tbody></table>`;
  stagger("#metrics-body .kpi", 55);
}

// Stagger an entrance across freshly-rendered elements. Capped, so a 19-card
// grid doesn't leave the last card arriving two-thirds of a second late.
function stagger(sel, step, cap = 10) {
  if (REDUCED) return;
  document.querySelectorAll(sel).forEach((e, i) => (e.style.animationDelay = `${Math.min(i, cap) * step}ms`));
}

// ═══════════ tabs / views ═══════════
function moveTabInk() {
  const t = document.querySelector(".tab.active");
  if (!t) return;
  $("tab-ink").style.width = `${t.offsetWidth - 20}px`;
  $("tab-ink").style.transform = `translateX(${t.offsetLeft + 10}px)`;
}
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${name}`));
  moveTabInk();
  if (name === "story") $("tab-badge-story").className = "tab-badge";
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

function showView(v) {
  ["run", "history", "about"].forEach((n) => $(`view-${n}`).classList.toggle("hidden", n !== v));
  document.querySelectorAll(".rail-btn[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $("picks").classList.toggle("hidden", v !== "run");
  // The command bar stays — only its run-specific controls come and go, so the
  // wordmark is present on every view rather than just the live-run one.
  document.querySelectorAll(".run-only").forEach((e) => e.classList.toggle("hidden", v !== "run"));
  if (v === "run") requestAnimationFrame(moveTabInk);
  if (v === "history") loadHistory();
  if (v === "about") renderPrimitives();
}
document.querySelectorAll(".rail-btn[data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

function moveSegThumb() {
  const on = $("mode-seg").querySelector("button.on");
  $("seg-thumb").style.width = `${on.offsetWidth}px`;
  $("seg-thumb").style.transform = `translateX(${on.offsetLeft - 4}px)`;
}

// ═══════════ run lifecycle ═══════════
let currentES = null;

function resetBoard() {
  runToken++;
  queue = []; waiter = null; logCount = 0;
  currentRunEvents = [];
  toolRoundInAttempt = 0; lastToolModelInitiated = false; plannerJustRan = false;
  currentAttemptNo = 0; currentSubagentRole = null;
  retryLoopCount = 0; sameplanLoopCount = 0; toolLoopCount = 0;
  liveTokens = 0; liveToolRound = 0;
  stopElapsed(); closeSheet();

  buildBoard(currentMode);
  resetNodes();
  ["retry", "sameplan", "toolloop"].forEach((k) => setLoopBadge(k, ""));
  logGroups = {}; currentLogGroupNo = null;
  modelThink(false); modelDotEl.classList.remove("warnflash");
  setModelInfo(idleModelLabel, "configured");
  $("comet").classList.add("hidden");
  trailBuf = [];

  logEl.innerHTML = `<div class="blank"><span class="blank-glyph">◐</span><b>Connecting…</b><p>Waiting for the first trace event.</p></div>`;
  $("result").innerHTML = `<div class="blank"><span class="blank-glyph">◈</span><b>Run in progress</b><p>Attempts appear as the harness produces them.</p></div>`;
  $("metrics-body").innerHTML = `<div class="blank"><span class="blank-glyph">◎</span><b>Run in progress</b><p>Time, tokens and cost are rolled up at the end.</p></div>`;
  $("tab-badge-story").textContent = ""; $("tab-badge-story").className = "tab-badge";
  $("tab-badge-result").textContent = ""; $("tab-badge-result").className = "tab-badge";
  setStatus("idle"); setAttempt("—"); setToolRound(0);
  ["stat-tokens", "stat-cost"].forEach((id) => ($(id).dataset.v = "0"));
  $("stat-tokens").textContent = "0"; $("stat-cost").textContent = "—"; $("stat-elapsed").textContent = "0ms";
}

function showError(msg) {
  setStatus("failed"); stopElapsed();
  $("result").innerHTML = `<div class="verdict fail"><span class="v-mark">!</span>
      <div><div class="v-title">Run failed</div><div class="v-sub">${escapeHtml(String(msg))}</div></div></div>`;
  showTab("result");
}

async function solve() {
  const btn = $("solve-btn"), ref = $("problem-ref").value.trim();
  if (!ref) { $("problem-ref").focus(); return; }
  btn.disabled = true; $("solve-label").textContent = "Solving";
  if (currentES) currentES.close();
  resetBoard();
  const token = runToken;
  runConsumer(token);
  showTab("story");
  $("problem-body").innerHTML = `<div class="blank"><span class="blank-glyph">◇</span><b>Fetching the problem…</b></div>`;
  const finish = () => { btn.disabled = false; $("solve-label").textContent = "Solve"; };

  let runId;
  try {
    const res = await fetch("/solve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ problem_ref: ref, max_attempts: maxAttempts, mode: currentMode }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(payload.detail || res.statusText);
      $("problem-body").innerHTML = `<div class="blank"><span class="blank-glyph">◇</span><b>Could not load that problem</b><p>${escapeHtml(String(payload.detail || res.statusText))}</p></div>`;
      finish(); return;
    }
    runId = payload.run_id;
    // The prepared problem comes back with the run id, so the question and its
    // derived budget/test cases are on screen before solving starts.
    if (payload.problem) renderProblem(payload.problem);
  } catch (e) { showError(e); finish(); return; }

  const es = new EventSource(`/trace-stream/${runId}`);
  currentES = es;
  logEl.innerHTML = "";
  setStatus("running"); startElapsed();

  es.onmessage = async (msg) => {
    const data = JSON.parse(msg.data);
    if (data.final) {
      es.close();
      // The run is over the moment the stream ends. The vitals say so
      // immediately; the diagram keeps replaying at its own pace and the panes
      // populate when that replay finishes.
      const r = await fetchRunResult(runId);
      if (r.status === "done") {
        stopElapsed(r.result.total_duration_ms);
        setStatus(r.result.passed ? "passed" : "failed");
        if (r.metrics) tickTo($("stat-cost"), r.metrics.cost_usd, (v) => `$${v.toFixed(4)}`);
      } else stopElapsed();
      finish();
      pushAction(async () => {
        if (r.status === "done") {
          renderResult(r.result);
          if (r.metrics) renderMetrics(r.metrics, r.result);
          if (r.problem) renderProblem(r.problem);
          showTab("result");
        } else if (r.status === "error") showError(r.error);
      });
      return;
    }
    handleEvent(data);
  };
  es.onerror = () => { es.close(); finish(); };
}

// The trace stream closes when the run span ends, which is a moment *before*
// the API has stored the RunResult — so a single GET right after the stream
// ends can legitimately still answer "running". Poll briefly rather than
// treating that as a dead end.
async function fetchRunResult(runId, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`/runs/${runId}`).then((x) => x.json());
      if (r.status !== "running") return r;
    } catch (e) { return { status: "error", error: String(e) }; }
    await dwell(100);
  }
  return { status: "error", error: "run did not report a result in time" };
}

// Preview without solving — fetch + extract + budget only, so the question can
// be read (and its derived oracle inspected) up front.
async function previewProblem(ref) {
  showTab("problem");
  $("problem-body").innerHTML = `<div class="blank"><span class="blank-glyph">◇</span><b>Fetching…</b></div>`;
  try {
    const res = await fetch(`/problem?ref=${encodeURIComponent(ref)}&generate_tests=false`);
    const data = await res.json();
    if (!res.ok) {
      $("problem-body").innerHTML = `<div class="blank"><span class="blank-glyph">◇</span><b>Could not load that problem</b><p>${escapeHtml(String(data.detail || res.statusText))}</p></div>`;
      return;
    }
    renderProblem(data);
  } catch (e) {
    $("problem-body").innerHTML = `<div class="blank"><span class="blank-glyph">◇</span><b>Request failed</b><p>${escapeHtml(String(e))}</p></div>`;
  }
}

// ═══════════ console wiring ═══════════
const refInput = $("problem-ref");
let maxAttempts = 3;
$("solve-btn").addEventListener("click", solve);
refInput.addEventListener("keydown", (e) => { if (e.key === "Enter") solve(); });

$("mode-seg").querySelectorAll("button").forEach((btn) => {
  btn.addEventListener("click", () => {
    currentMode = btn.dataset.mode;
    $("mode-seg").querySelectorAll("button").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", String(on));
    });
    moveSegThumb();
    $("mode-badge").textContent = currentMode === "multi" ? "multi-agent" : "single-agent";
    // Changing modes reshapes the flowchart, so redraw before the next run.
    buildBoard(currentMode); resetNodes();
  });
});
function setAttempts(n) {
  maxAttempts = Math.max(1, Math.min(6, n));
  $("att-value").textContent = maxAttempts;
  $("att-minus").disabled = maxAttempts === 1;
  $("att-plus").disabled = maxAttempts === 6;
}
$("att-minus").addEventListener("click", () => setAttempts(maxAttempts - 1));
$("att-plus").addEventListener("click", () => setAttempts(maxAttempts + 1));
document.querySelectorAll(".pick").forEach((e, i) => {
  e.style.animationDelay = `${i * 40}ms`;
  e.addEventListener("click", () => {
    document.querySelectorAll(".pick").forEach((q) => q.classList.toggle("on", q === e));
    refInput.value = e.dataset.ref;
    previewProblem(e.dataset.ref);
  });
});

// ═══════════ history (P14 dashboard) ═══════════
let historyRows = [];
const statHtml = (id, s) => {
  const pct = Math.round(s.pass_rate * 100);
  return `<div class="stat">
    <div class="stat-k" title="${escapeHtml(id)}">${escapeHtml(id)}</div>
    <div class="stat-r">${s.attempt_count} attempt(s) · deepest retry #${s.max_attempt_no}</div>
    <div class="stat-r"><span class="stat-pct">${pct}%</span> of attempts passed</div>
    <div class="meter"><div class="meter-f" style="width:${pct}%"></div></div></div>`;
};
function historyTableHtml(rows) {
  if (!rows.length) return `<div class="blank"><b>Nothing matches</b></div>`;
  return `<table class="data">
    <thead><tr><th>problem</th><th class="txt">category</th><th>attempt</th><th>verdict</th><th>tokens</th><th class="txt">failure reason</th><th class="txt">when</th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td class="role">${escapeHtml(r.kata_id)}</td><td class="txt">${escapeHtml(r.kata_category)}</td>
      <td>${r.attempt_no}</td><td class="${r.passed ? "pass" : "fail"}">${r.passed ? "pass" : "fail"}</td>
      <td>${r.tokens_used}</td>
      <td class="txt wide" title="${escapeHtml(r.failure_reason || "")}">${escapeHtml(r.failure_reason || "—")}</td>
      <td class="txt">${new Date(r.created_at * 1000).toLocaleString()}</td></tr>`).join("")}</tbody></table>`;
}
async function loadHistory() {
  const sg = $("stats-grid"), tw = $("history-table"), kp = $("history-kpis");
  sg.innerHTML = `<div class="blank"><b>Loading…</b></div>`;
  try {
    const [stats, history] = await Promise.all([
      fetch("/stats").then((r) => r.json()), fetch("/history").then((r) => r.json())]);
    historyRows = history;
    const ids = Object.keys(stats.per_kata);
    const passed = history.filter((r) => r.passed).length;
    const tokens = history.reduce((s, r) => s + (r.tokens_used || 0), 0);
    kp.innerHTML = `
      <div class="kpi"><div class="kpi-v">${stats.total_attempts}</div><div class="kpi-l">attempts logged</div></div>
      <div class="kpi"><div class="kpi-v">${ids.length}</div><div class="kpi-l">problems seen</div></div>
      <div class="kpi good"><div class="kpi-v">${history.length ? Math.round((passed / history.length) * 100) : 0}%</div>
        <div class="kpi-l">attempt pass rate</div><div class="kpi-s">${passed}/${history.length} most recent</div></div>
      <div class="kpi hero"><div class="kpi-v">${(tokens / 1000).toFixed(1)}k</div><div class="kpi-l">tokens spent</div></div>`;
    sg.innerHTML = ids.length ? ids.map((id) => statHtml(id, stats.per_kata[id])).join("")
      : `<div class="blank"><b>No runs recorded yet</b><p>Solve a problem and it lands here.</p></div>`;
    tw.innerHTML = historyTableHtml(history);
    stagger("#history-kpis .kpi", 55); stagger("#stats-grid .stat", 40);
  } catch (e) {
    kp.innerHTML = ""; tw.innerHTML = "";
    sg.innerHTML = `<div class="blank"><b>Failed to load history</b><p>${escapeHtml(String(e))}</p></div>`;
  }
}
$("history-filter").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  $("history-table").innerHTML = historyTableHtml(!q ? historyRows
    : historyRows.filter((r) => [r.kata_id, r.kata_category, r.failure_reason].some((v) => (v || "").toLowerCase().includes(q))));
});

// ═══════════ primitives ═══════════
const PRIMITIVES = [
  ["P1","Provider seam",1,"model","One interface for every model. OpenAI-compatible clients (OpenRouter, Ollama) and a deterministic fake sit behind the same three methods.","harness/providers/"],
  ["P2","Instructions",1,"agent","Builds the system + user prompt for an attempt, including the derived complexity target and any structured feedback from the last failure.","harness/instructions.py"],
  ["P3","Tools + approval gate",1,"exec","Six tools the model may call. Every call passes an approval gate first, so a policy can block side-effecting work.","harness/tools/"],
  ["P4","Sandbox",1,"exec","Candidate code never runs in-process. A subprocess with a timeout executes it and reports back structurally.","harness/sandbox/executor.py"],
  ["P5","Verification (oracle)",1,"verdict","The authoritative pass/fail. The model's own claim about its code is never what decides a run.","harness/verification/oracle.py"],
  ["P6","Orchestration",1,"agent","The outer attempt loop and the inner tool-call loop — single-agent and multi-agent variants of the same contract.","harness/orchestrator.py"],
  ["P7","Tracing + cost",1,"derive","OTel-flavoured spans emitted synchronously, streamed to this UI over SSE, and rolled up into tokens and dollars.","harness/tracing/"],
  ["P8","UI",1,"prepare","This page: the serpentine pipeline with real loop-back edges, the narrated story feed, and the history dashboard.","ui/"],
  ["P9","History",2,"derive","Per-session attempt state — what was tried, what failed, and how, within one run.","harness/context/history.py"],
  ["P10","Context delivery",2,"agent","Turns a failure into a structured diff the model can act on, rather than pasting a raw traceback.","harness/context/failure_formatter.py"],
  ["P11","Context management",2,"agent","Trims and compacts the message list so a long retry chain doesn't blow the window.","harness/context/context_manager.py"],
  ["P12","Subagents",2,"agent","Planner, Coder and Tester. Each returns a distilled result — never its raw internal transcript.","harness/subagents/"],
  ["P13","Skills",3,"prepare","SKILL.md notes loaded on demand by problem category — know-how the base model doesn't have baked in.","skills/"],
  ["P14","Durable state + memory",3,"derive","A cross-session attempt log in SQLite, plus recall of what went wrong last time on a similar problem.","harness/memory/store.py"],
  ["P15","Problem fetching",4,"prepare","LeetCode GraphQL, a pasted statement, or a curated offline kata — one interface over all three.","harness/problems/"],
  ["P16","Extraction",4,"prepare","Statement → entry point, signature and ground-truth cases. Deterministic where possible; the model is the fallback.","harness/subagents/extractor.py"],
  ["P17","Complexity budget",4,"derive","A table, not an opinion: n ≤ 5·10⁴ means target O(n log n), and O(n²) will time out.","harness/complexity_budget.py"],
  ["P18","Test-case generation",4,"derive","~10 extra constraint-respecting edge cases the published examples don't cover.","harness/subagents/testgen.py"],
  ["P19","Judge",4,"verdict","Adjudicates a disagreement on a generated case: bad test, real bug, or uncertain — and defaults to uncertain.","harness/subagents/judge.py"],
];
let primsDone = false;
function renderPrimitives() {
  if (primsDone) return;
  primsDone = true;
  $("primitive-grid").innerHTML = PRIMITIVES.map(([p, name, phase, hue, desc, file]) => `
    <div class="prim">
      <div class="prim-top">
        <span class="prim-p" style="color:${HUE[hue]};background:${HUE[hue]}22;border:1px solid ${HUE[hue]}44">${p}</span>
        <span class="prim-phase">phase ${phase}</span>
      </div>
      <div class="prim-n">${escapeHtml(name)}</div>
      <div class="prim-d">${escapeHtml(desc)}</div>
      <div class="prim-f">${escapeHtml(file)}</div>
    </div>`).join("");
  stagger(".prim", 34);
  // cursor-tracked sheen, so the grid feels lit rather than printed
  document.querySelectorAll(".prim").forEach((c) => c.addEventListener("pointermove", (e) => {
    const r = c.getBoundingClientRect();
    c.style.setProperty("--mx", `${e.clientX - r.left}px`);
    c.style.setProperty("--my", `${e.clientY - r.top}px`);
  }));
}

// ═══════════ boot ═══════════
// Which model sits behind the provider seam is a headline fact for this
// project, so it goes in the always-visible vitals strip — the rail dot is
// only the live/offline light, with the endpoint detail on hover.
async function loadMeta() {
  const chip = $("wire-chip"), tip = $("wire-tip");
  try {
    const m = await fetch("/meta").then((r) => r.json());
    chip.classList.add(m.live ? "live" : "offline");
    tip.textContent = m.live
      ? `${m.provider} → ${m.base_url}\nmodel: ${m.model}\napproval: ${m.approval_policy}`
      : "FakeProvider — scripted offline demo, no API key set";
    idleModelLabel = m.live ? m.model : "FakeProvider · scripted";
    setModelInfo(idleModelLabel, m.live ? "configured" : "offline");
    $("model-vital").title = tip.textContent;
  } catch {
    chip.classList.add("offline");
    tip.textContent = "provider unknown";
  }
}

setAttempts(3);
buildBoard(currentMode);
moveSegThumb();
moveTabInk();
loadMeta();
window.addEventListener("resize", () => { moveSegThumb(); moveTabInk(); });
