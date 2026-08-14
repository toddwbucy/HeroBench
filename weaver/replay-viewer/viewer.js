"use strict";

// ------------------------------------------------------------------
// Tiny DOM helper. All variable content goes through textContent or
// node-construction; nothing assigns innerHTML with interpolated data.
// ------------------------------------------------------------------
function el(tag, attrs, ...children) {
  const ns = tag.startsWith("svg:") || ["svg", "rect", "circle", "path", "text", "g", "title"].includes(tag)
    ? "http://www.w3.org/2000/svg"
    : null;
  const node = ns ? document.createElementNS(ns, tag.replace(/^svg:/, "")) : document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class" || k === "className") node.setAttribute("class", String(v));
      else if (k === "text") node.textContent = String(v);
      else if (k === "html") {/* deliberately not supported */}
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, String(v));
    }
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) {
      for (const cc of c) if (cc != null && cc !== false) node.appendChild(typeof cc === "string" ? document.createTextNode(cc) : cc);
    } else if (typeof c === "string" || typeof c === "number") {
      node.appendChild(document.createTextNode(String(c)));
    } else {
      node.appendChild(c);
    }
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

const STATE = {
  data: null,
  episodeIdx: -1,
  stepIdx: 0,
  playing: false,
  playTimer: null,
  // Live-polling state. Only available when the file was loaded via the
  // ?file= URL parameter (i.e., served from an HTTP origin); drag-dropped
  // File objects can't be re-read without a fresh user gesture.
  liveUrl: null,
  liveOn: false,
  liveTimer: null,
  liveIntervalMs: 5000,
  liveTailFollow: true, // when true, auto-advance to last step on each refresh
  cachedWorld: null,    // memoized world data (static across polls)
  // Fog-of-war toggle (S1). true = dim tiles the agent has NOT discovered by the
  // current scrub point (agent-knowledge view); false = show full god-view.
  fogMode: true,
};

// Cap large strings so a pathological LLM input doesn't blow up the browser.
const LLM_TEXT_PREVIEW_CHARS = 60000;
const AQL_RESULT_PREVIEW_CHARS = 4000;

// ------------------------------------------------------------------
// In-browser extractor — mirrors extract_episodes.py's build_episodes.
// Input: array of span objects parsed from JSONL.
// Output: an object with the same shape extract_episodes.py emits.
// ------------------------------------------------------------------
function buildEpisodesFromSpans(spans) {
  spans.sort((a, b) => (a.startTimeUnixNano || 0) - (b.startTimeUnixNano || 0));
  const attempts = [];
  let agent = null, runId = null, model = null, host = null;
  let firstTs = null, lastTs = null;
  for (const s of spans) {
    const ts = s.startTimeUnixNano || 0;
    const ets = s.endTimeUnixNano || 0;
    if (firstTs == null || ts < firstTs) firstTs = ts;
    if (ets > (lastTs || 0)) lastTs = ets;
    const res = s.resource || {};
    if (agent == null) agent = res["agent.name"];
    if (runId == null) runId = res["weaver.run_id"];
    if (model == null) model = res["llm.model_name"];
    if (host == null) host = res["host.name"];
    if (s.name === "task_attempt") {
      const a = s.attributes || {};
      attempts.push({
        task: a["herobench.task_name"],
        attempt: a["herobench.attempt_num"],
        score: a["herobench.score"],
        solved: a["herobench.solved"],
        actions_taken: a["herobench.actions_taken"],
        actions_succeeded: a["herobench.actions_succeeded"],
        task_type: a["herobench.task_type"],
        difficulty: a["herobench.difficulty"],
        start_ns: ts,
        end_ns: ets,
        duration_s: (ets && ts) ? (ets - ts) / 1e9 : null,
        steps: [],
        trail: [],
        discovered_tiles: {},
      });
    }
  }
  attempts.sort((x, y) => x.start_ns - y.start_ns);

  function findEp(ts) {
    // Linear scan — at most a few dozen attempts per run.
    for (let i = 0; i < attempts.length; i++) {
      if (attempts[i].start_ns <= ts && ts <= attempts[i].end_ns) return i;
    }
    return -1;
  }

  function parseField(v) {
    if (typeof v === "string") {
      try { return JSON.parse(v); } catch { return v; }
    }
    return v;
  }

  // Parse the agent's fog-of-war CSV (the `discovered_map_csv` field the
  // HeroBenchObserve tool surfaces — the agent's OWN evolving map, distinct
  // from the god-view world). Header lines start with '#'; the column header
  // is `x,y,terrain,content,last_seen_turn`; rows follow. `last_seen_turn` may
  // be the literal "now" for the current tile. Returns { "x,y": {x,y,terrain,
  // content,last_seen} }. This is the real fog-of-war the viewer renders.
  function parseDiscoveredMapCsv(csv) {
    const out = {};
    if (typeof csv !== "string" || !csv) return out;
    for (const raw of csv.split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      if (line.startsWith("x,y,")) continue; // column header
      const parts = line.split(",");
      if (parts.length < 2) continue;
      const x = parseInt(parts[0], 10);
      const y = parseInt(parts[1], 10);
      if (Number.isNaN(x) || Number.isNaN(y)) continue;
      out[`${x},${y}`] = {
        x, y,
        terrain: (parts[2] || "").trim(),
        content: (parts[3] || "").trim(),
        last_seen: (parts[4] || "").trim(), // turn number or "now"
      };
    }
    return out;
  }

  function trimStr(s) {
    if (typeof s !== "string") return s;
    if (s.length <= LLM_TEXT_PREVIEW_CHARS) return s;
    return s.slice(0, LLM_TEXT_PREVIEW_CHARS) + `\n\n[... truncated, ${s.length - LLM_TEXT_PREVIEW_CHARS} more chars ...]`;
  }

  function shapeMsg(m) {
    if (typeof m !== "object" || m == null) return { role: "?", content: String(m).slice(0, 200) };
    let c = m.content;
    if (Array.isArray(c)) {
      c = c.map((b) => typeof b === "object" ? JSON.stringify(b) : String(b)).join("\n");
    }
    if (typeof c !== "string") c = JSON.stringify(c);
    return { role: m.role || "?", content: trimStr(c) };
  }

  function summarizeChar(out) {
    if (!out || typeof out !== "object") return {};
    const ch = out.character || {};
    if (typeof ch !== "object") return {};
    const pos = (ch.position && ch.position.x != null && ch.position.y != null)
      ? { x: ch.position.x, y: ch.position.y }
      : (ch.x != null && ch.y != null ? { x: ch.x, y: ch.y } : {});
    const inv = Array.isArray(ch.inventory) ? ch.inventory : [];
    const skills = {};
    for (const k of Object.keys(ch)) {
      if (k.endsWith("_level") && typeof ch[k] === "number") {
        const skill = k.slice(0, -"_level".length);
        skills[skill] = { level: ch[k], xp: ch[skill + "_xp"], max_xp: ch[skill + "_max_xp"] };
      }
    }
    return {
      position: pos, hp: ch.hp, max_hp: ch.max_hp, name: ch.name,
      inventory: inv, skills,
      weapon: ch.weapon_slot, body_armor: ch.body_armor_slot,
      helmet: ch.helmet_slot, boots: ch.boots_slot,
    };
  }

  function summarizeBC(out) {
    if (!out || typeof out !== "object") return {};
    const bc = out.belief_context || {};
    if (typeof bc !== "object") return {};
    return {
      graph_size: bc.graph_size,
      active_constraints: bc.active_constraints,
      relevant_nodes: (bc.relevant_nodes || []).slice(0, 30),
      relevant_edges: (bc.relevant_edges || []).slice(0, 30),
      known_paths: (bc.known_paths || []).slice(0, 10),
    };
  }

  function summarizeLLM(a) {
    const inp = Array.isArray(a["llm.input_messages"]) ? a["llm.input_messages"] : [];
    const out = Array.isArray(a["llm.output_messages"]) ? a["llm.output_messages"] : [];
    return {
      input_messages: inp.map(shapeMsg),
      output_messages: out.map(shapeMsg),
      tokens: {
        prompt: a["llm.token_count.prompt"],
        completion: a["llm.token_count.completion"],
        total: a["llm.token_count.total"],
      },
      stop_reason: a["llm.stop_reason"],
    };
  }

  function summarizeAQL(a) {
    let res = a["query.results"];
    let resStr = res != null ? JSON.stringify(res) : "";
    if (resStr.length > AQL_RESULT_PREVIEW_CHARS) {
      resStr = resStr.slice(0, AQL_RESULT_PREVIEW_CHARS) +
        `\n\n[... truncated, ${resStr.length - AQL_RESULT_PREVIEW_CHARS} more chars ...]`;
    }
    return {
      db: a.db,
      bind_vars: a["query.bind_vars"],
      result_count: a["query.result_count"],
      results_preview: resStr,
      span_kind: a["openinference.span.kind"],
    };
  }

  for (const s of spans) {
    const a = s.attributes || {};
    const ts = s.startTimeUnixNano || 0;
    const idx = findEp(ts);
    if (idx < 0) continue;
    const ep = attempts[idx];
    const n = s.name;
    if (n === "tool") {
      const tname = a["tool.name"];
      const params = parseField(a["tool.parameters"]) || {};
      // `tool.output` is the canonical key per the fork trace contract;
      // fall back to legacy `output.value` for pre-contract traces.
      const output = parseField(a["tool.output"] ?? a["output.value"]);
      const err = !!a.error;
      const errMsg = a["error.message"];
      if (tname === "HeroBenchObserve") {
        const ch = summarizeChar(output);
        const pos = ch.position || null;
        // Fog-of-war is collected at the RUN level (see fog_timeline below),
        // not per-step — observes outside task_attempt windows are still picked
        // up there. The step itself stays a plain observe record.
        ep.steps.push({
          kind: "observe", ts_ns: ts, params,
          character: ch,
          belief_context: summarizeBC(output),
          ambient_risk: (output && typeof output === "object") ? output.ambient_risk : null,
          warning: (output && typeof output === "object") ? output._budget_warning : null,
          error: err, error_message: errMsg,
        });
        if (pos && pos.x != null && pos.y != null) {
          const k = `${pos.x},${pos.y}`;
          if (!ep.discovered_tiles[k]) {
            ep.discovered_tiles[k] = { x: pos.x, y: pos.y, first_seen_ns: ts, events: [] };
          }
        }
      } else if (tname === "HeroBenchAct") {
        const action = (params && params.action) || "?";
        ep.steps.push({
          kind: "act", ts_ns: ts, action, params,
          output: (output && typeof output === "object" && !Array.isArray(output))
            ? Object.fromEntries(Object.entries(output).filter(([k]) => k !== "belief_update"))
            : output,
          belief_update_summary: (output && typeof output === "object") ? output.belief_update : null,
          error: err, error_message: errMsg,
        });
        if (action === "move" && !err && params.x != null && params.y != null) {
          ep.trail.push({ x: params.x, y: params.y, ts_ns: ts });
        }
        if (params.x != null && params.y != null) {
          const k = `${params.x},${params.y}`;
          const tile = ep.discovered_tiles[k] || (ep.discovered_tiles[k] = {
            x: params.x, y: params.y, first_seen_ns: ts, events: [],
          });
          tile.events.push({ ts_ns: ts, action, success: !err, params });
        }
      } else if (tname === "HeroBenchPlan") {
        ep.steps.push({
          kind: "plan", ts_ns: ts,
          plan: params.plan, steps: params.steps, output, error: err,
        });
      } else if (tname === "Pen") {
        ep.steps.push({
          kind: "pen", ts_ns: ts,
          topic: params.topic, content: params.content, tags: params.tags,
          location: params.location, output, error: err,
        });
      } else if (tname === "Notepad") {
        ep.steps.push({ kind: "notepad", ts_ns: ts, params, output, error: err });
      } else {
        ep.steps.push({
          kind: "tool", ts_ns: ts, tool_name: tname, params, output,
          error: err, error_message: errMsg,
        });
      }
    } else if (n === "llm") {
      ep.steps.push({ kind: "llm", ts_ns: ts, ...summarizeLLM(a) });
    } else if (n === "aql.query") {
      ep.steps.push({ kind: "aql", ts_ns: ts, ...summarizeAQL(a) });
    } else if (n === "context_nap") {
      ep.steps.push({
        kind: "nap", ts_ns: ts,
        trigger: a["nap.trigger"],
        input_tokens: a["nap.input_tokens"],
        threshold: a["nap.threshold_tokens"],
        number: a["nap.number"],
      });
    }
    // embed spans, list_collections, etc. intentionally dropped — high-volume, low-diagnostic.
  }

  for (const ep of attempts) {
    ep.steps.sort((x, y) => x.ts_ns - y.ts_ns);
    ep.trail.sort((x, y) => x.ts_ns - y.ts_ns);
  }

  // Run-level fog-of-war timeline — EVERY observe's `discovered_map_csv`, in
  // time order, INDEPENDENT of the task_attempt episode windows. The episode
  // grouping (findEp) drops any span outside an attempt window, which discarded
  // most observes (e.g. 128 of 145 in a gemma run) and starved the fog render.
  // Fog is cumulative per observe, so the latest entry at-or-before a given
  // time is the agent's full known map at that point. This is the "follow the
  // whole trace, don't drop spans" fix, scoped to fog.
  const fogTimeline = [];
  for (const s of spans) {
    if (s.name !== "tool") continue;
    const a = s.attributes || {};
    if (a["tool.name"] !== "HeroBenchObserve") continue;
    const output = parseField(a["tool.output"] ?? a["output.value"]);
    const csv = (output && typeof output === "object") ? output.discovered_map_csv : null;
    const fog = parseDiscoveredMapCsv(csv);
    if (Object.keys(fog).length) {
      fogTimeline.push({ ts_ns: s.startTimeUnixNano || 0, fog });
    }
  }
  fogTimeline.sort((x, y) => x.ts_ns - y.ts_ns);

  return {
    agent, run_id: runId, model, host,
    first_ts_ns: firstTs, last_ts_ns: lastTs,
    episodes: attempts,
    fog_timeline: fogTimeline,
  };
}

// ------------------------------------------------------------------
// File loading
// ------------------------------------------------------------------
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const app = document.getElementById("app");

function onFile(file) {
  const reader = new FileReader();
  reader.onload = async (ev) => {
    const text = ev.target.result;
    const data = parseInput(text, file.name);
    if (!data) return;  // parseInput already alerted
    // If the file came in without world data, try to fetch it from common
    // HeroBench ports. This works only when the viewer is served from an
    // HTTP origin (e.g., python3 -m http.server) — file:// blocks the
    // cross-origin fetch silently. If the fetch succeeds, attach the
    // result to data.world; if it fails, the no-world overlay kicks in.
    if (!data.world || !Array.isArray(data.world.tiles) || data.world.tiles.length === 0) {
      const fetched = await tryFetchWorld(["http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:9000"]);
      if (fetched) data.world = fetched;
    }
    STATE.data = data;
    onLoaded();
  };
  reader.readAsText(file);
}

async function tryFetchWorld(bases) {
  // Prefer same-origin world.json (snapshotted via fetch_world.py).
  // This avoids CORS when the viewer is served from http://localhost:<port>
  // and HeroBench is on http://127.0.0.1:<port> (different origins per
  // browser policy, even though both resolve to localhost).
  try {
    const res = await fetch("world.json", { cache: "no-store" });
    if (res.ok) {
      const w = await res.json();
      if (Array.isArray(w.tiles) && w.tiles.length) {
        console.log(`[viewer] loaded world from same-origin world.json (${w.tiles.length} tiles)`);
        return w;
      }
    }
  } catch {
    // No world.json — fall through to cross-origin attempts.
  }
  // Cross-origin attempts (will fail silently if HeroBench has no CORS).
  for (const base of bases) {
    try {
      const [tiles, resources, monsters, items] = await Promise.all([
        fetch(`${base}/maps`).then((r) => r.ok ? r.json() : null),
        fetch(`${base}/resources`).then((r) => r.ok ? r.json() : null),
        fetch(`${base}/monsters`).then((r) => r.ok ? r.json() : null),
        fetch(`${base}/items`).then((r) => r.ok ? r.json() : null),
      ]);
      if (Array.isArray(tiles) && tiles.length) {
        console.log(`[viewer] auto-fetched world from ${base} (${tiles.length} tiles)`);
        return { fetched_from: base, tiles, resources: resources || [], monsters: monsters || [], items: items || [] };
      }
    } catch (e) {
      // CORS / network error / server down — try the next base.
    }
  }
  return null;
}

// Accepts either:
//   1. An episodes.json (output of extract_episodes.py) — used directly.
//   2. A raw trace .jsonl — extracted in-browser into the same shape.
//   3. Anything else — clear error pointing at the right file.
function parseInput(text, filename) {
  // Single-JSON path first.
  let asJson = null;
  let parseErr = null;
  try {
    asJson = JSON.parse(text);
  } catch (e) {
    parseErr = e;
  }
  if (asJson) {
    if (Array.isArray(asJson.episodes)) return asJson;
    // Recognize a trace manifest (no 'episodes' but has run_id + agent.name)
    if (asJson.run_id || asJson.agent || asJson.manifest_version) {
      alert(
        "This looks like a *.manifest.json (trace metadata). You want the matching " +
        "*.jsonl raw trace file from the same directory, or an episodes.json produced " +
        "by extract_episodes.py."
      );
      return null;
    }
    // Single span (one line of a JSONL)?
    if (asJson.traceId && asJson.spanId) {
      alert(
        "This is a single trace span. Drop the full .jsonl trace file instead — the " +
        "viewer will extract episodes from it in-browser."
      );
      return null;
    }
    alert("Loaded JSON has no 'episodes' array and isn't a recognized trace shape.");
    return null;
  }
  // Failed JSON.parse — likely JSONL. Split on newlines, parse each.
  const lines = text.split("\n");
  const spans = [];
  let lineErrs = 0;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try { spans.push(JSON.parse(trimmed)); }
    catch { lineErrs++; }
  }
  if (spans.length === 0) {
    alert("File doesn't look like JSON or JSONL (no parseable lines). " +
      (parseErr ? `Parse error: ${parseErr.message}` : ""));
    return null;
  }
  // Heuristic: if more than half the lines failed, this isn't a trace.
  if (lineErrs > spans.length) {
    alert(`Looks like JSONL but ${lineErrs} of ${lineErrs + spans.length} lines failed to parse. ` +
      "Is this actually a weaver-trace output?");
    return null;
  }
  // Verify it looks like a weaver-trace JSONL (spans have traceId / startTimeUnixNano).
  if (!spans[0].traceId || spans[0].startTimeUnixNano == null) {
    alert("Lines parsed as JSON but don't look like trace spans (no traceId / startTimeUnixNano). " +
      "Drop a weaver-trace .jsonl or an episodes.json from extract_episodes.py.");
    return null;
  }
  return buildEpisodesFromSpans(spans);
}

fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) onFile(e.target.files[0]);
});

["dragenter", "dragover"].forEach((ev) => {
  document.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); });
});
document.addEventListener("drop", (e) => {
  e.preventDefault(); e.stopPropagation();
  if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
});

document.getElementById("reload-btn").addEventListener("click", () => {
  dropZone.classList.remove("hidden");
  app.classList.add("hidden");
});

// ------------------------------------------------------------------
// S2: flat color-coded navigable trace. A run-level, continuous list of EVERY
// step (not split into selectable episodes), color-coded so notable events —
// death, task solved/failed, errors, level-ups — pop. Clicking a row scrubs
// there; it's a search index into the cognitive log on the right. Task
// boundaries appear as subtle markers, not separate sections.
// ------------------------------------------------------------------
function classifyStep(s) {
  // Returns { cls, label }; cls drives the left color bar + label tint.
  if (s.kind === "observe") {
    const ch = s.character || {};
    if (ch.hp === 0) return { cls: "ev-death", label: "DEATH (hp 0)" };
    const p = ch.position || {};
    return { cls: "ev-observe", label: `observe (${p.x ?? "?"},${p.y ?? "?"}) hp${ch.hp ?? "?"}` };
  }
  if (s.kind === "act") {
    if (s.error) return { cls: "ev-error", label: `${s.action || "act"} ✗` };
    return { cls: "ev-act", label: s.action || "act" };
  }
  if (s.kind === "plan") return { cls: "ev-plan", label: "plan" };
  if (s.kind === "pen") return { cls: "ev-pen", label: `pen: ${(s.topic || "").slice(0, 22)}` };
  if (s.kind === "notepad") return { cls: "ev-notepad", label: "notepad" };
  if (s.kind === "llm") return { cls: "ev-llm", label: `llm ${s.stop_reason || ""}`.trim() };
  if (s.kind === "aql") return { cls: "ev-aql", label: `aql ·${s.result_count ?? "?"}` };
  if (s.kind === "nap") return { cls: "ev-nap", label: `nap #${s.number ?? "?"}` };
  if (s.kind === "tool") return { cls: "ev-act", label: s.tool_name || "tool" };
  return { cls: "ev-llm", label: s.kind };
}

function renderTrace(d) {
  const list = document.getElementById("episode-list");
  clear(list);
  let prevSkills = null;
  d.episodes.forEach((ep, epIdx) => {
    // Task boundary marker (subtle divider; clicking jumps to its first step).
    list.appendChild(el("div",
      { class: `tmark ${ep.solved ? "solved" : "failed"}`,
        onclick: () => jumpToFlat(epIdx, 0) },
      el("span", { class: "tname", text: `${ep.task || "?"} #${ep.attempt ?? "?"}` }),
      el("span", { text: "" }),
      el("span", { class: "badge", text: `${ep.solved ? "✓" : "✗"} ${Math.round(ep.score || 0)}` })
    ));
    ep.steps.forEach((s, stepIdx) => {
      let { cls, label } = classifyStep(s);
      // Level-up: an observe whose any skill level exceeds the prior observe's.
      if (s.kind === "observe" && s.character && s.character.skills) {
        const sk = s.character.skills;
        if (prevSkills) {
          for (const k of Object.keys(sk)) {
            const lv = sk[k] && sk[k].level, pv = prevSkills[k] && prevSkills[k].level;
            if (lv != null && pv != null && lv > pv) { cls = "ev-levelup"; label = `level up: ${k} ${lv}`; break; }
          }
        }
        prevSkills = sk;
      }
      list.appendChild(el("div",
        { class: `trow ${cls}`, "data-ep": String(epIdx), "data-step": String(stepIdx),
          onclick: () => jumpToFlat(epIdx, stepIdx) },
        el("span", { class: "bar" }),
        el("span", { class: "k", text: s.kind }),
        el("span", { class: "lbl", text: label }),
        el("span", { class: "tag", text: "" })
      ));
    });
  });
}

// Click a trace row → scrub to that step (drives map + state + log).
function jumpToFlat(epIdx, stepIdx) {
  STATE.episodeIdx = epIdx;
  STATE.stepIdx = stepIdx;
  renderEpisode();
}

// Highlight the active trace row + scroll it into view. Called from
// renderEpisode so scrubber/keyboard moves stay in sync with the trace.
function highlightTraceRow() {
  const list = document.getElementById("episode-list");
  if (!list) return;
  let active = null;
  for (const row of list.querySelectorAll(".trow")) {
    const on = parseInt(row.dataset.ep) === STATE.episodeIdx && parseInt(row.dataset.step) === STATE.stepIdx;
    row.classList.toggle("active", on);
    if (on) active = row;
  }
  if (active) active.scrollIntoView({ block: "nearest", behavior: "auto" });
}

// ------------------------------------------------------------------
// After load: render episode list
// ------------------------------------------------------------------
function onLoaded() {
  const d = STATE.data;
  if (!d || !Array.isArray(d.episodes)) {
    alert("episodes.json missing 'episodes' array");
    return;
  }
  dropZone.classList.add("hidden");
  app.classList.remove("hidden");
  // Diagnostic: surface key counts so DevTools console confirms what loaded.
  const nWorldTiles = (d.world && Array.isArray(d.world.tiles)) ? d.world.tiles.length : 0;
  // Run-level fog snapshots (all observes) + the size of the final map.
  const nFogSnaps = Array.isArray(d.fog_timeline) ? d.fog_timeline.length : 0;
  const finalFogTiles = nFogSnaps ? Object.keys(d.fog_timeline[nFogSnaps - 1].fog).length : 0;
  console.log(`[viewer] loaded: ${d.episodes.length} episodes, world tiles: ${nWorldTiles}, fog snapshots: ${nFogSnaps} (final map ${finalFogTiles} tiles)`);
  if (nWorldTiles === 0 && nFogSnaps === 0) {
    console.warn("[viewer] No world tiles AND no discovered_map_csv — the map will be empty. " +
      "Load a raw *.jsonl trace from a run with the fog-of-war tool, or pass " +
      "`extract_episodes.py --world-host http://127.0.0.1:8000` for the god-view overlay.");
  } else if (nWorldTiles === 0) {
    console.log("[viewer] No god-view world tiles — rendering the agent's fog-of-war directly from discovered_map_csv. " +
      "Pass --world-host to extract_episodes.py if you also want the god-view background.");
  }
  document.getElementById("header-meta").textContent =
    `${d.agent || "?"} · ${(d.model || "?")} · ${d.episodes.length} episodes` +
    mapStatusLabel(d);

  renderTrace(d); // S2: flat color-coded navigable trace (replaces episode list)
  // S3: the lower-left panel now shows point-in-time player state (renderState,
  // driven by the scrub position) instead of the run-total summary.
  if (d.episodes.length > 0) selectEpisode(0);
}

// ------------------------------------------------------------------
// Run summary panel — aggregates across all episodes in this run.
//   • Tasks accomplished: per-task best score + total attempts.
//   • Latest character state: level, HP, skills, inventory at the
//     last observation in the run.
// ------------------------------------------------------------------
function renderRunSummary(d) {
  const root = document.getElementById("run-summary");
  if (!root) return;
  clear(root);

  // Aggregate per-task: solved? best score? attempt count?
  const byTask = new Map();
  for (const ep of d.episodes) {
    const t = ep.task || "?";
    const e = byTask.get(t) || { attempts: 0, solved: false, best_score: 0, last_attempt: 0 };
    e.attempts += 1;
    if (ep.solved) e.solved = true;
    if ((ep.score || 0) > e.best_score) e.best_score = ep.score || 0;
    if ((ep.attempt || 0) > e.last_attempt) e.last_attempt = ep.attempt || 0;
    byTask.set(t, e);
  }
  const tasks = Array.from(byTask.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const solvedCount = tasks.filter(([, e]) => e.solved).length;

  // Tasks section.
  const taskSec = el("div", { class: "section" },
    el("h3", { text: `Tasks (${solvedCount}/${tasks.length} solved)` })
  );
  for (const [task, e] of tasks) {
    const row = el("div", { class: `task-row ${e.solved ? "solved" : "unsolved"}` },
      el("span", { class: "check", text: e.solved ? "✓" : "✗" }),
      el("span", { class: "name", text: task }),
      el("span", { class: "attempts", text: `${e.attempts}×` }),
      el("span", { class: "score", text: String(Math.round(e.best_score)) })
    );
    taskSec.appendChild(row);
  }
  root.appendChild(taskSec);

  // Latest character state — find the last observe step in the last episode.
  const latestCharacter = findLatestCharacter(d);
  if (!latestCharacter) {
    const sec = el("div", { class: "section" },
      el("h3", { text: "Character" }),
      el("div", { style: "color: var(--muted); font-size: 11px;", text: "No observation captured yet." })
    );
    root.appendChild(sec);
    return;
  }

  // Character vital section.
  const ch = latestCharacter;
  const kv = el("div", { class: "kv" });
  if (ch.level != null) {
    kv.appendChild(el("div", { class: "k", text: "level" }));
    const xpStr = ch.xp != null && ch.max_xp != null ? ` (${ch.xp}/${ch.max_xp} xp)` : "";
    kv.appendChild(el("div", { text: `${ch.level}${xpStr}` }));
  }
  if (ch.hp != null) {
    kv.appendChild(el("div", { class: "k", text: "HP" }));
    const hpStr = ch.max_hp != null ? `${ch.hp}/${ch.max_hp}` : String(ch.hp);
    kv.appendChild(el("div", { text: hpStr }));
  }
  if (ch.name) {
    kv.appendChild(el("div", { class: "k", text: "name" }));
    kv.appendChild(el("div", { text: String(ch.name) }));
  }
  const vitalSec = el("div", { class: "section" },
    el("h3", { text: "Character (latest)" }),
    kv
  );
  root.appendChild(vitalSec);

  // Skills section.
  const skills = ch.skills || {};
  const skillEntries = Object.entries(skills).filter(([, v]) => v && v.level != null);
  if (skillEntries.length) {
    const grid = el("div", { class: "skills-grid" });
    for (const [name, v] of skillEntries) {
      grid.appendChild(el("div", { class: "skill-cell" },
        el("span", { text: name }),
        el("span", { class: "lvl", text: String(v.level) })
      ));
    }
    root.appendChild(el("div", { class: "section" },
      el("h3", { text: "Skills" }), grid
    ));
  }

  // Inventory section.
  const inv = ch.inventory || [];
  const nonempty = inv.filter((it) => typeof it === "object" ? (it.quantity || 0) > 0 : true);
  const invSec = el("div", { class: "section" },
    el("h3", { text: `Inventory${nonempty.length ? ` (${nonempty.length})` : ""}` })
  );
  if (nonempty.length === 0) {
    invSec.appendChild(el("div", { style: "color: var(--muted); font-size: 11px;", text: "empty" }));
  } else {
    const wrap = el("div");
    for (const item of nonempty) {
      if (typeof item === "object" && item) {
        wrap.appendChild(el("span", { class: "inv-item",
          text: `${item.code || JSON.stringify(item)}${item.quantity ? ` ×${item.quantity}` : ""}` }));
      } else {
        wrap.appendChild(el("span", { class: "inv-item", text: String(item) }));
      }
    }
    invSec.appendChild(wrap);
  }
  root.appendChild(invSec);

  // Equipment (only non-empty slots).
  const equipped = collectEquipment(ch);
  if (Object.keys(equipped).length) {
    const wrap = el("div", { class: "kv" });
    for (const [slot, item] of Object.entries(equipped)) {
      wrap.appendChild(el("div", { class: "k", text: slot }));
      wrap.appendChild(el("div", { text: item }));
    }
    root.appendChild(el("div", { class: "section" },
      el("h3", { text: "Equipped" }), wrap
    ));
  }
}

function findLatestCharacter(d) {
  // Walk episodes in reverse and find the latest observe step's
  // character state. (Our extractor stores summarized character on
  // each observe step.)
  for (let ei = d.episodes.length - 1; ei >= 0; ei--) {
    const ep = d.episodes[ei];
    for (let si = ep.steps.length - 1; si >= 0; si--) {
      const s = ep.steps[si];
      if (s.kind === "observe" && s.character) return s.character;
    }
  }
  return null;
}

function collectEquipment(ch) {
  const slots = {};
  const candidate_keys = [
    "weapon", "body_armor", "helmet", "boots",
    "ring1", "ring2", "amulet",
    "artifact1", "artifact2", "artifact3",
  ];
  for (const k of candidate_keys) {
    const v = ch[k];
    if (typeof v === "string" && v) slots[k] = v;
  }
  return slots;
}

function selectEpisode(i) {
  STATE.episodeIdx = i;
  STATE.stepIdx = 0;
  renderEpisode(); // highlightTraceRow (called inside) syncs the trace selection
}

// ------------------------------------------------------------------
// Episode rendering
// ------------------------------------------------------------------
function currentEpisode() {
  if (STATE.episodeIdx < 0) return null;
  return STATE.data.episodes[STATE.episodeIdx];
}

function renderEpisode() {
  const ep = currentEpisode();
  if (!ep) return;
  const total = ep.steps.length;
  const scrub = document.getElementById("scrubber");
  scrub.max = Math.max(0, total - 1);
  scrub.value = Math.min(STATE.stepIdx, scrub.max);
  document.getElementById("map-title-text").textContent =
    `Map · ${ep.task} attempt #${ep.attempt} · ${ep.solved ? "SOLVED" : "fail"} · score ${Math.round(ep.score || 0)}`;
  renderState();
  renderLog();
  renderMap();
  document.getElementById("step-pos").textContent = total === 0 ? "- / -" : `${STATE.stepIdx + 1} / ${total}`;
  highlightTraceRow(); // S2: keep the trace selection in sync with the scrub position
}

function currentStep() {
  const ep = currentEpisode();
  if (!ep || ep.steps.length === 0) return null;
  return ep.steps[Math.min(STATE.stepIdx, ep.steps.length - 1)];
}

function lastObserve(beforeIdx) {
  const ep = currentEpisode();
  if (!ep) return null;
  for (let i = Math.min(beforeIdx, ep.steps.length - 1); i >= 0; i--) {
    if (ep.steps[i].kind === "observe") return ep.steps[i];
  }
  return null;
}

function currentPosition() {
  // The agent's position changes on successful `move` actions, not just
  // on observe spans. Walk steps[0..stepIdx] to integrate position
  // updates: each observe sets the position from character state, each
  // successful move sets the position to its destination. Result is the
  // agent's actual location at the current scrub point.
  const ep = currentEpisode();
  if (!ep) return null;
  const last = Math.min(STATE.stepIdx, ep.steps.length - 1);
  let pos = null;
  for (let i = 0; i <= last; i++) {
    const s = ep.steps[i];
    if (s.kind === "observe" && s.character && s.character.position) {
      pos = s.character.position;
    } else if (
      s.kind === "act" && s.action === "move" && !s.error &&
      s.params && s.params.x != null && s.params.y != null
    ) {
      pos = { x: s.params.x, y: s.params.y };
    }
  }
  return pos;
}

// The agent's fog-of-war at the current scrub point: the cumulative
// `discovered_map_csv` from the most recent observe at or before stepIdx.
// Returns the `{ "x,y": {x,y,terrain,content,last_seen} }` map, or null.
// The agent's fog at the current scrub point — the latest run-level fog
// snapshot at or before the current step's timestamp. Drawn from the run-level
// `fog_timeline` (ALL observes), so it is complete regardless of which episode
// the scrub point sits in (and immune to the episode-window dropping).
function currentFog() {
  const tl = STATE.data && STATE.data.fog_timeline;
  if (!tl || !tl.length) return null;
  const cur = currentStep();
  const tNow = cur ? cur.ts_ns : tl[tl.length - 1].ts_ns;
  let fog = null;
  for (const e of tl) {
    if (e.ts_ns <= tNow) fog = e.fog; else break;
  }
  return fog;
}

// The run's complete fog (last cumulative snapshot) — sizes the viewBox stably
// so the map doesn't reflow as the agent discovers more.
function fullFog() {
  const tl = STATE.data && STATE.data.fog_timeline;
  return (tl && tl.length) ? tl[tl.length - 1].fog : null;
}

// Biome → fill (shared by world layer and fog-only rendering).
const BIOME_FILL = {
  "Forest": "#2d6e44",
  "Forest (Forge)": "#e88a2e",
  "Lake": "#3a86c8",
  "Graveyard": "#6a5d7b",
  "City": "#a09686",
  "Spawn": "#7ee896",
};

// ------------------------------------------------------------------
// Map (SVG, built via createElementNS — no innerHTML)
// ------------------------------------------------------------------
function renderMap() {
  const ep = currentEpisode();
  const svg = document.getElementById("map");
  clear(svg);
  if (!ep) {
    document.getElementById("map-overlay").textContent = "No episode";
    return;
  }

  // Determine viewBox.
  // If world data is loaded, use the full world bounding box; otherwise
  // fall back to the agent's exploration bbox.
  const world = STATE.data.world;
  const worldTiles = (world && world.tiles) || [];
  let minX, maxX, minY, maxY;
  if (worldTiles.length) {
    minX = Math.min(...worldTiles.map((t) => t.x)) - 1;
    maxX = Math.max(...worldTiles.map((t) => t.x)) + 1;
    minY = Math.min(...worldTiles.map((t) => t.y)) - 1;
    maxY = Math.max(...worldTiles.map((t) => t.y)) + 1;
  } else {
    let xs = [], ys = [];
    for (const t of ep.trail) { xs.push(t.x); ys.push(t.y); }
    for (const k of Object.keys(ep.discovered_tiles)) {
      const tile = ep.discovered_tiles[k];
      xs.push(tile.x); ys.push(tile.y);
    }
    // Include the agent's full fog-of-war so the viewBox covers its whole
    // discovered map even with no god-view world data (raw .jsonl).
    const ff = fullFog();
    if (ff) for (const k of Object.keys(ff)) { xs.push(ff[k].x); ys.push(ff[k].y); }
    if (xs.length === 0) { xs = [0]; ys = [0]; }
    minX = Math.min(...xs) - 2; maxX = Math.max(...xs) + 2;
    minY = Math.min(...ys) - 2; maxY = Math.max(...ys) + 2;
  }
  const w = maxX - minX + 1, h = maxY - minY + 1;
  svg.setAttribute("viewBox", `${minX - 0.5} ${minY - 0.5} ${w} ${h}`);

  // No god-view world data? Render the agent's OWN fog-of-war map instead —
  // it is self-sufficient (terrain + content per discovered tile), so a raw
  // *.jsonl trace renders directly with no --world-host fetch. Only show the
  // empty-state when there is neither world nor any discovered_map_csv.
  // `bgFog` (full map) sizes the extent / decides fog-aware mode; `fogNow` is
  // what the agent actually knows AT THE CURRENT SCRUB POINT — so scrubbing
  // forward reveals tiles progressively and scrubbing back hides ones
  // discovered later. Painting the full map here would leak future knowledge
  // into past steps (CR). Declared once and reused by every fog layer below.
  const bgFog = fullFog();
  const fogNow = currentFog();
  if (!worldTiles.length) {
    svg.appendChild(el("rect", {
      x: String(minX - 0.5), y: String(minY - 0.5),
      width: String(w), height: String(h),
      fill: "#0a0d12",
    }));
    if (bgFog && Object.keys(bgFog).length) {
      // Paint only the tiles known by the current scrub point.
      const paint = fogNow || {};
      for (const k of Object.keys(paint)) {
        const f = paint[k];
        svg.appendChild(el("rect", {
          x: String(f.x - 0.5), y: String(f.y - 0.5),
          width: "1", height: "1",
          fill: BIOME_FILL[f.terrain] || "#3a4150",
          stroke: "#0a0d12", "stroke-width": "0.03",
        }, el("title", {
          text: `(${f.x},${f.y}) ${f.terrain || "?"}${f.content ? " · " + f.content : ""} · seen turn ${f.last_seen}`,
        })));
        // The content the AGENT recorded (e.g. "wolf", "copper_rocks") — its
        // memory, which may differ from ground truth. This is the surface
        // where confabulation (e.g. claiming iron at a copper tile) shows up.
        if (f.content) {
          svg.appendChild(el("text", {
            x: String(f.x), y: String(f.y + 0.12),
            "text-anchor": "middle", fill: "#0a0d12",
            "font-size": "0.26", "font-weight": "700",
            text: f.content.slice(0, 3),
          }, el("title", { text: f.content })));
        }
      }
    } else {
      svg.appendChild(el("rect", {
        x: String(minX - 0.5), y: String(minY - 0.5),
        width: String(w), height: String(h), fill: "#330000",
      }));
      svg.appendChild(el("text", {
        x: String((minX + maxX) / 2), y: String((minY + maxY) / 2),
        "text-anchor": "middle", fill: "#ff8c8c",
        "font-size": String(Math.min(w, h) / 18), "font-weight": "700",
        text: "NO MAP DATA (no world tiles, no discovered_map_csv)",
      }));
    }
  }

  const cur = currentStep();
  const tNow = cur ? cur.ts_ns : (ep.steps[0] ? ep.steps[0].ts_ns : 0);
  const pos = currentPosition();

  // Layer 1: world tiles (background biome layer, if loaded). `BIOME_FILL` is
  // module-scoped (shared with the fog-only render above).
  if (worldTiles.length) {
    for (const t of worldTiles) {
      const fill = BIOME_FILL[t.name] || "#444";
      const titleText = `(${t.x},${t.y}) ${t.name}${t.content ? ` · ${t.content.type}: ${t.content.code}` : ""}`;
      svg.appendChild(el("rect", {
        x: String(t.x - 0.5), y: String(t.y - 0.5),
        width: "1", height: "1",
        fill, stroke: "#0a0d12", "stroke-width": "0.03",
      }, el("title", { text: titleText })));
    }
    // Layer 2: world content markers (resource / monster / workshop / spawn).
    const CONTENT_FILL = {
      resource: "#5fbf80",
      monster: "#e06060",
      workshop: "#f0c674",
      tasks_master: "#79b8ff",
      bank: "#d8a8ff",
      grand_exchange: "#d8a8ff",
    };
    const CONTENT_LABEL = {
      resource: "R", monster: "M", workshop: "W",
      tasks_master: "T", bank: "B", grand_exchange: "$",
    };
    for (const t of worldTiles) {
      if (!t.content) continue;
      const c = t.content;
      const fill = CONTENT_FILL[c.type] || "#888";
      const label = CONTENT_LABEL[c.type] || "?";
      svg.appendChild(el("circle", {
        cx: String(t.x), cy: String(t.y), r: "0.32",
        fill, stroke: "#000", "stroke-width": "0.04",
      }, el("title", { text: `${c.type}: ${c.code} at (${t.x},${t.y})` })));
      svg.appendChild(el("text", {
        x: String(t.x), y: String(t.y + 0.13),
        "text-anchor": "middle", fill: "#000",
        "font-size": "0.32", "font-weight": "700",
        text: label,
      }));
    }
    // Spawn cell gets a distinct dashed outline.
    const spawn = worldTiles.find((t) => t.name === "Spawn");
    if (spawn) {
      svg.appendChild(el("rect", {
        x: String(spawn.x - 0.5), y: String(spawn.y - 0.5),
        width: "1", height: "1",
        fill: "none", stroke: "#fff", "stroke-width": "0.08",
        "stroke-dasharray": "0.1 0.1",
      }));
    }

    // Layer 2.5: FOG OF WAR. When god-view world tiles are present, dim every
    // tile the agent has NOT discovered (by the current scrub point), so the
    // god-view recedes and the agent's actual knowledge stands out. Reuses the
    // hoisted `fogNow` so the reveal grows as you scrub forward through observes.
    if (STATE.fogMode && fogNow) {
      for (const t of worldTiles) {
        if (!fogNow[`${t.x},${t.y}`]) {
          svg.appendChild(el("rect", {
            x: String(t.x - 0.5), y: String(t.y - 0.5),
            width: "1", height: "1",
            fill: "#0a0d12", "fill-opacity": "0.82",
          }));
        }
      }
    }
  }

  // Layer 3: agent footprints — tiles the agent touched (success/failure),
  // drawn on top of the world/fog. In fog-aware mode (a fog snapshot exists)
  // a footprint for a tile not yet discovered at the scrub point would leak
  // future knowledge, so SKIP those entirely rather than just fade them; the
  // fade is kept only for the non-fog (legacy) case (CR).
  const fogAware = !!bgFog;
  for (const k of Object.keys(ep.discovered_tiles)) {
    const tile = ep.discovered_tiles[k];
    if (fogAware && tile.first_seen_ns > tNow) continue;
    const allEvents = tile.events || [];
    let stroke = "#ffffff", strokeWidth = "0.12", dasharray = null, opacity = 1;
    if (allEvents.length === 0) {
      stroke = "#ffffff"; dasharray = "0.1 0.1"; opacity = 0.6;
    } else if (allEvents.some((e) => e.success)) {
      stroke = "#6ee37c"; strokeWidth = "0.16";
    } else {
      stroke = "#ff6e6e"; strokeWidth = "0.16";
    }
    if (!fogAware && tile.first_seen_ns > tNow) { opacity = 0.18; dasharray = "0.06 0.12"; }
    if (pos && pos.x === tile.x && pos.y === tile.y) {
      stroke = "#ffffff"; strokeWidth = "0.22"; opacity = 1; dasharray = null;
    }
    const events = allEvents.map((e) => `${e.action}${e.success ? "ok" : "ERR"}`).join(" ");
    const titleText = `(${tile.x},${tile.y}) agent: ${events || "observed"}`;
    const attrs = {
      x: String(tile.x - 0.45), y: String(tile.y - 0.45),
      width: "0.9", height: "0.9",
      fill: "none", stroke, "stroke-width": strokeWidth, opacity: String(opacity),
    };
    if (dasharray) attrs["stroke-dasharray"] = dasharray;
    svg.appendChild(el("rect", attrs, el("title", { text: titleText })));
  }

  // Layer 4: trail polyline, time-gated.
  const trailPts = ep.trail.filter((t) => t.ts_ns <= tNow);
  if (trailPts.length >= 2) {
    const d = trailPts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
    svg.appendChild(el("path", {
      d, fill: "none", stroke: "#ffffff", "stroke-width": "0.08", "stroke-opacity": "0.85",
    }));
  }

  // Layer 5: agent dot at current position.
  if (pos) {
    svg.appendChild(el("circle", {
      cx: String(pos.x), cy: String(pos.y), r: "0.32",
      fill: "#ffffff", stroke: "#58a6ff", "stroke-width": "0.08",
    }));
  }

  // Overlay text
  const totalTiles = Object.keys(ep.discovered_tiles).length;
  const visitedNow = Object.keys(ep.discovered_tiles).filter((k) => ep.discovered_tiles[k].first_seen_ns <= tNow).length;
  const worldStr = worldTiles.length ? ` of ${worldTiles.length} world tiles` : "";
  const fogStr = fogNow ? ` · fog-of-war ${Object.keys(fogNow).length} tiles known` : "";
  document.getElementById("map-overlay").textContent =
    `bbox ${minX},${minY} -> ${maxX},${maxY} · agent touched ${visitedNow}/${totalTiles}${worldStr}${fogStr} · trail ${trailPts.length}`;
}

// ------------------------------------------------------------------
// State panel
// ------------------------------------------------------------------
function renderState() {
  const obs = lastObserve(STATE.stepIdx);
  const sp = document.getElementById("state-panel");
  clear(sp);
  if (!obs) {
    sp.appendChild(el("em", { text: "No observation yet." }));
    return;
  }
  const ch = obs.character || {};
  const pos = ch.position || {};
  const inv = ch.inventory || [];
  const skills = ch.skills || {};
  const ar = obs.ambient_risk || {};
  const bc = obs.belief_context || {};

  // Position / HP
  const posSection = el("div", { class: "section" }, el("h3", { text: "Position / HP" }));
  const kv = el("div", { class: "kv" });
  function addKV(k, v) {
    if (v == null) return;
    kv.appendChild(el("div", { class: "k", text: k }));
    kv.appendChild(el("div", { class: "v", text: String(v) }));
  }
  addKV("position", `(${pos.x ?? "?"}, ${pos.y ?? "?"})`);
  addKV("HP", `${ch.hp ?? "?"}${ch.max_hp ? " / " + ch.max_hp : ""}`);
  if (ch.weapon) addKV("weapon", ch.weapon);
  if (ar.score != null) addKV("ambient risk", ar.score.toFixed(3));
  posSection.appendChild(kv);
  sp.appendChild(posSection);

  // Inventory
  if (inv.length) {
    const invSection = el("div", { class: "section" },
      el("h3", { text: `Inventory (${inv.length})` })
    );
    const invDiv = el("div");
    for (const item of inv) {
      let label;
      if (typeof item === "object" && item) {
        label = `${item.code || JSON.stringify(item)}${item.quantity ? ` x${item.quantity}` : ""}`;
      } else {
        label = String(item);
      }
      invDiv.appendChild(el("span", { class: "inv-item", text: label }));
    }
    invSection.appendChild(invDiv);
    sp.appendChild(invSection);
  }

  // Skills
  const skillEntries = Object.entries(skills).filter(([, v]) => v && v.level != null);
  if (skillEntries.length) {
    const skSec = el("div", { class: "section" }, el("h3", { text: "Skills" }));
    const skDiv = el("div");
    for (const [name, v] of skillEntries) {
      const span = el("span", { class: "skill" });
      span.appendChild(document.createTextNode(`${name}: `));
      span.appendChild(el("span", { class: "lvl", text: String(v.level) }));
      if (v.xp != null && v.max_xp) {
        span.appendChild(document.createTextNode(` (${v.xp}/${v.max_xp})`));
      }
      skDiv.appendChild(span);
    }
    skSec.appendChild(skDiv);
    sp.appendChild(skSec);
  }

  // Belief context
  if (bc.relevant_nodes && bc.relevant_nodes.length) {
    const sec = el("div", { class: "section" }, el("h3", {
      text: `Belief context${bc.graph_size ? ` (${bc.graph_size.total_nodes} nodes)` : ""}`,
    }));
    sec.appendChild(detailsPre(`${bc.relevant_nodes.length} relevant nodes`, bc.relevant_nodes));
    if (bc.active_constraints && bc.active_constraints.length) {
      sec.appendChild(detailsPre(`${bc.active_constraints.length} active constraints`, bc.active_constraints));
    }
    sp.appendChild(sec);
  }

  if (obs.warning) {
    const sec = el("div", { class: "section" }, el("h3", { text: "Observation warning" }));
    sec.appendChild(el("div", { style: "color: var(--warn); font-size: 11px;", text: obs.warning }));
    sp.appendChild(sec);
  }
}

// Open <details> are tracked by a stable key so they survive re-renders.
// The log is rebuilt on row-click (scrub) AND every 5s in live mode; without
// this, an opened panel snaps shut on the next render. Keyed by episode +
// step index + label so it's stable within a run and doesn't bleed across
// episodes.
const _openDetails = new Set();

function detailsPre(summary, content, key) {
  // stopPropagation: each step row has an onclick that re-renders the whole
  // log (to scrub to that step). Without this, clicking a disclosure summary
  // bubbles to the row, forces a re-render, and the <details> is rebuilt
  // collapsed. Stopping propagation lets the native toggle work and keeps the
  // row's scrub-on-click for non-details areas.
  const d = el("details", { onclick: (e) => e.stopPropagation() },
    el("summary", { text: summary }),
    el("pre", { text: typeof content === "string" ? content : JSON.stringify(content, null, 2) })
  );
  if (key) {
    if (_openDetails.has(key)) d.open = true;
    d.addEventListener("toggle", () => {
      if (d.open) _openDetails.add(key);
      else _openDetails.delete(key);
    });
  }
  return d;
}

// ------------------------------------------------------------------
// Cognitive log panel
// ------------------------------------------------------------------
function renderLog() {
  const ep = currentEpisode();
  const log = document.getElementById("log");
  clear(log);
  if (!ep) return;
  const total = ep.steps.length;
  const cur = STATE.stepIdx;
  const start = Math.max(0, cur - 5);
  const end = Math.min(total, cur + 30);
  for (let i = start; i < end; i++) {
    const s = ep.steps[i];
    let cls = "step " + s.kind;
    if (s.kind === "act" && s.error) cls += " error";
    if (i === cur) cls += " current";
    else if (i < cur) cls += " before";
    else cls += " after";
    const div = el("div", { class: cls, onclick: () => { STATE.stepIdx = i; renderEpisode(); } });
    div.appendChild(stepHead(s, i));
    appendStepBody(div, s, i);
    log.appendChild(div);
  }
  const curEl = log.querySelector(".step.current");
  if (curEl) curEl.scrollIntoView({ block: "center", behavior: "auto" });
}

function stepHead(s, i) {
  return el("div", { class: "head" },
    el("span", { class: "kind", text: s.kind }),
    el("span", { class: "ts", text: `step ${i + 1}` })
  );
}

function appendStepBody(div, s, i) {
  // Stable per-panel key for open-state persistence across re-renders.
  const dk = (label) => `${STATE.episodeIdx}:${i}:${label}`;
  switch (s.kind) {
    case "observe": {
      const pos = s.character && s.character.position;
      const txt = `HeroBenchObserve at (${pos?.x ?? "?"}, ${pos?.y ?? "?"}) - HP ${s.character?.hp ?? "?"}, inv ${(s.character?.inventory || []).length}`;
      div.appendChild(el("div", { class: "mono", style: "font-size: 11px; color: var(--muted);", text: txt }));
      break;
    }
    case "act": {
      const body = el("div", { class: "body" });
      body.appendChild(el("strong", { text: s.action || "act" }));
      body.appendChild(document.createTextNode(" " + JSON.stringify(s.params || {}) + " -> "));
      if (s.error) {
        body.appendChild(el("span", { style: "color: var(--bad);", text: "ERR " + (s.error_message || "") }));
      } else {
        body.appendChild(el("span", { style: "color: var(--good);", text: "ok" }));
      }
      div.appendChild(body);
      if (s.output && typeof s.output === "object") div.appendChild(detailsPre("output", s.output, dk("output")));
      break;
    }
    case "plan": {
      div.appendChild(el("div", { class: "body" }, el("em", { text: s.plan || "(no plan text)" })));
      if (s.steps) div.appendChild(detailsPre(`${(s.steps || []).length} step(s)`, s.steps, dk("steps")));
      break;
    }
    case "pen": {
      const head = el("div", { class: "body" });
      head.appendChild(el("strong", { text: s.topic || "(no topic)" }));
      if (s.tags && s.tags.length) {
        head.appendChild(el("span", { style: "color: var(--muted); margin-left: 8px;", text: `[${s.tags.join(", ")}]` }));
      }
      div.appendChild(head);
      div.appendChild(el("div", { class: "body", style: "margin-top: 4px; color: var(--ink);", text: s.content || "" }));
      break;
    }
    case "notepad": {
      div.appendChild(el("div", { class: "body" }, "params: ",
        el("code", { text: JSON.stringify(s.params || {}) })));
      if (s.output) div.appendChild(detailsPre("output", s.output, dk("output")));
      break;
    }
    case "llm": {
      const tk = s.tokens || {};
      div.appendChild(el("div", {
        class: "body",
        style: "font-size: 11px; color: var(--muted);",
        text: `tokens prompt=${tk.prompt ?? "?"} completion=${tk.completion ?? "?"} · stop=${s.stop_reason || "?"}`,
      }));
      // Output messages — the actual reasoning
      if (s.output_messages && s.output_messages.length) {
        for (const m of s.output_messages) {
          div.appendChild(el("div", { class: "role", text: m.role }));
          div.appendChild(el("pre", { text: typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2) }));
        }
      }
      // Last input message visible by default; full set on demand
      if (s.input_messages && s.input_messages.length) {
        const last = s.input_messages[s.input_messages.length - 1];
        div.appendChild(detailsPre(`last input (${last.role})`, last.content, dk("lastinput")));
        if (s.input_messages.length > 1) {
          div.appendChild(detailsPre(`full input (${s.input_messages.length} msgs)`, s.input_messages, dk("fullinput")));
        }
      }
      break;
    }
    case "aql": {
      div.appendChild(el("div", { class: "body", style: "font-size: 11px;",
        text: `${s.db || ""} · count=${s.result_count ?? "?"} · ${s.span_kind || ""}` }));
      if (s.bind_vars) div.appendChild(detailsPre("bind_vars", s.bind_vars, dk("bind_vars")));
      if (s.results_preview) div.appendChild(detailsPre("results preview", s.results_preview, dk("results")));
      break;
    }
    case "nap": {
      div.appendChild(el("div", { class: "body",
        text: `trigger=${s.trigger || "?"} · input=${s.input_tokens ?? "?"} / threshold=${s.threshold ?? "?"} · #${s.number ?? "?"}` }));
      break;
    }
    case "tool": {
      div.appendChild(el("div", { class: "body" },
        el("strong", { text: s.tool_name || "?" }),
        " " + JSON.stringify(s.params || {})));
      if (s.output) div.appendChild(detailsPre("output", s.output, dk("output")));
      break;
    }
    default:
      div.appendChild(el("pre", { text: JSON.stringify(s, null, 2) }));
  }
}

// ------------------------------------------------------------------
// Scrubber + keyboard
// ------------------------------------------------------------------
const scrubber = document.getElementById("scrubber");
scrubber.addEventListener("input", (e) => {
  STATE.stepIdx = parseInt(e.target.value);
  renderEpisode();
});
document.getElementById("prev-btn").addEventListener("click", () => stepBy(-1));
document.getElementById("next-btn").addEventListener("click", () => stepBy(1));
document.getElementById("play-btn").addEventListener("click", togglePlay);

// Fog-of-war toggle (S1): flip between the agent's known map (dim the
// undiscovered) and full ground truth (god-view). Re-renders the map in place.
document.getElementById("fog-toggle").addEventListener("click", () => {
  STATE.fogMode = !STATE.fogMode;
  document.getElementById("fog-toggle").textContent =
    STATE.fogMode ? "🌫 Fog-of-war: ON" : "🗺 God-view: ON";
  renderMap();
});

function stepBy(n) {
  const ep = currentEpisode();
  if (!ep) return;
  STATE.stepIdx = Math.max(0, Math.min(ep.steps.length - 1, STATE.stepIdx + n));
  renderEpisode();
}

function togglePlay() {
  const btn = document.getElementById("play-btn");
  if (STATE.playing) {
    clearInterval(STATE.playTimer);
    STATE.playing = false;
    btn.textContent = "▶";
  } else {
    STATE.playing = true;
    btn.textContent = "▮▮";
    STATE.playTimer = setInterval(() => {
      const ep = currentEpisode();
      if (!ep || STATE.stepIdx >= ep.steps.length - 1) { togglePlay(); return; }
      stepBy(1);
    }, 350);
  }
}

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") { e.preventDefault(); stepBy(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); stepBy(1); }
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
  else if (e.key === "Home") { e.preventDefault(); STATE.stepIdx = 0; renderEpisode(); }
  else if (e.key === "End") {
    e.preventDefault();
    const ep = currentEpisode();
    if (ep) { STATE.stepIdx = ep.steps.length - 1; renderEpisode(); }
  }
});

// ------------------------------------------------------------------
// ?file=... query parameter loads a JSON via fetch (requires HTTP server).
// Supports both pre-extracted episodes.json and raw .jsonl traces.
// Records the URL so the Live button can re-fetch it.
// ------------------------------------------------------------------
async function loadFromUrl(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${url}`);
  const text = await res.text();
  const data = parseInput(text, url);
  if (!data) return false;
  if (!data.world || !Array.isArray(data.world.tiles) || data.world.tiles.length === 0) {
    // Reuse the cached world if we've fetched it before — the HeroBench
    // map is static, so per-poll refetches are pure waste in live mode.
    if (STATE.cachedWorld) {
      data.world = STATE.cachedWorld;
    } else {
      const fetched = await tryFetchWorld(["http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:9000"]);
      if (fetched) {
        data.world = fetched;
        STATE.cachedWorld = fetched;
      }
    }
  } else if (!STATE.cachedWorld) {
    // First load already had world data — cache it for any future loads
    // that don't (e.g., dropping a raw .jsonl after an episodes.json).
    STATE.cachedWorld = data.world;
  }
  STATE.data = data;
  return true;
}

(async () => {
  const q = new URLSearchParams(window.location.search);
  const f = q.get("file");
  if (f) {
    try {
      const ok = await loadFromUrl(f);
      if (ok) {
        STATE.liveUrl = f;
        document.getElementById("live-btn").disabled = false;
        onLoaded();
      }
    } catch (e) {
      console.warn("[viewer] failed to load ?file=:", e.message);
    }
  }
})();

// ------------------------------------------------------------------
// Live-mode polling
// ------------------------------------------------------------------
const liveBtn = document.getElementById("live-btn");
liveBtn.addEventListener("click", toggleLive);

function toggleLive() {
  if (!STATE.liveUrl) {
    alert("Live mode requires the file to have been loaded via ?file= URL parameter " +
      "(i.e., http://localhost:8085/?file=...). Drag-dropped files can't be re-read.");
    return;
  }
  STATE.liveOn = !STATE.liveOn;
  if (STATE.liveOn) {
    liveBtn.classList.add("live-on");
    liveBtn.textContent = "● Live: on";
    STATE.liveTimer = setInterval(livePoll, STATE.liveIntervalMs);
    livePoll(); // immediate first refresh
  } else {
    liveBtn.classList.remove("live-on");
    liveBtn.textContent = "● Live: off";
    if (STATE.liveTimer) { clearInterval(STATE.liveTimer); STATE.liveTimer = null; }
  }
}

async function livePoll() {
  if (!STATE.liveUrl) return;
  // Preserve user position. If the user is at (or within 2 steps of) the
  // end of the current episode, auto-follow the tail. Otherwise stay put.
  const prevEp = currentEpisode();
  const prevTask = prevEp ? prevEp.task : null;
  const prevAttempt = prevEp ? prevEp.attempt : null;
  const prevStepIdx = STATE.stepIdx;
  const prevSteps = prevEp ? prevEp.steps.length : 0;
  const wasAtTail = prevEp ? (prevStepIdx >= prevSteps - 3) : false;

  try {
    const ok = await loadFromUrl(STATE.liveUrl);
    if (!ok) return;
  } catch (e) {
    console.warn("[viewer] live poll failed:", e.message);
    return;
  }

  // Re-render episode list and try to keep the same episode selected.
  const d = STATE.data;
  document.getElementById("header-meta").textContent = liveHeaderMeta(d);
  rebuildEpisodeList(d);

  // Find the previous episode in the new data (by task+attempt key).
  let newIdx = -1;
  if (prevTask != null && prevAttempt != null) {
    newIdx = d.episodes.findIndex((e) => e.task === prevTask && e.attempt === prevAttempt);
  }
  if (newIdx < 0) {
    // Previous episode disappeared (rare; trace truncation?) — fall back to last.
    newIdx = d.episodes.length - 1;
  }
  STATE.episodeIdx = newIdx;

  const ep = currentEpisode();
  if (ep) {
    if (wasAtTail) {
      STATE.stepIdx = ep.steps.length - 1;
    } else {
      STATE.stepIdx = Math.min(prevStepIdx, ep.steps.length - 1);
    }
  } else {
    STATE.stepIdx = 0;
  }

  // Mark active row + render.
  for (const row of document.querySelectorAll("#episode-list .ep")) {
    row.classList.toggle("active", parseInt(row.dataset.idx) === STATE.episodeIdx);
  }
  renderEpisode();
}

// Single source of truth for the header's map-status fragment — used by both
// onLoaded() and liveHeaderMeta() so the world/fog/no-map label can't drift
// between the static and live-refresh paths (CR).
function mapStatusLabel(d) {
  const nWorld = (d.world && Array.isArray(d.world.tiles)) ? d.world.tiles.length : 0;
  const hasFog = Array.isArray(d.fog_timeline) && d.fog_timeline.length > 0;
  if (nWorld) return ` · ${nWorld} world tiles` + (hasFog ? ` + fog-of-war` : ``);
  return hasFog ? ` · fog-of-war` : ` · no map data`;
}

function liveHeaderMeta(d) {
  const live = STATE.liveOn ? " · LIVE" : "";
  return `${d.agent || "?"} · ${d.model || "?"} · ${d.episodes.length} episodes` +
    mapStatusLabel(d) + live;
}

function rebuildEpisodeList(d) {
  // Live-mode refresh: rebuild the flat trace (S2). renderEpisode's
  // highlightTraceRow re-marks the active row after.
  renderTrace(d);
}
