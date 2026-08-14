# HeroBench Replay Viewer — Spec (proposed)

Status: **proposed** · Owner: todd · 2026-06-02

Parent charter: `weaver-demo-PRD` (this is the herobench/ sub-area of the weaver-demo consumer crate).

A static-HTML/JS replay viewer for HeroBench agent runs, rebuilt into a
**point-in-time, scrub-driven** inspector for the late-next-week presentation.
Lives at `scripts/herobench-replay-viewer/` (not part of the WeaverTools build —
throwaway diagnostic tooling, kept versioned so it can be discarded when the
bench shifts shape).

---

## 1. Purpose

Turn a `weaver-trace` `*.jsonl` run into an interactive replay that makes the
memory architecture's effect on behaviour *visible in seconds*: where the agent
went, what it actually *knew* (fog-of-war) vs ground truth, its state at any
moment, and the reasoning that produced each action. For the presentation it is
the artifact that shows "the agent operates on a partial, self-constructed map,"
not raw spans.

The guiding interaction: **one scrub position drives everything.** Pick a moment
(by scrubbing, or by clicking an event in the trace) and the map, the state
panel, and the cognitive log all reflect *that* moment.

---

## 2. Current state (what exists)

`index.html` (3-column grid `280px 1fr 1fr`) + `viewer.js` (~1500 lines) + a
no-cache `serve.py` (port 8085). Layout today:

- **Left stack:** `Episodes` (task-attempt navigation) + `Run Summary` (run totals).
- **Centre:** one `Map` SVG (god-view world tiles from `world.json`, agent path,
  and an *existing but currently dormant* fog-dimming overlay) + a point-in-time
  `state-panel` below it.
- **Right:** `Cognitive log` (LLM I/O, tool calls, Pen/Notepad, AQL).
- **Footer:** global scrubber + play/step controls (already drives `STATE.stepIdx`).

What already works and is REUSED (do not rebuild):
- `buildEpisodesFromSpans` → steps + `fog_timeline` (run-level, post-#438).
- `renderState` is **already point-in-time** — it reads `lastObserve(STATE.stepIdx)`
  (the observation at/before the current step) for HP / inventory / skills.
- `renderMap` already has both render paths: god-view world tiles, AND a
  "Layer 2.5" fog overlay that dims tiles absent from `currentFog()`.
- The scrubber already exists and updates `STATE.stepIdx`.

---

## 3. The fog regression (must fix first)

**Symptom:** the map renders full god-view with no fog dimming; user recalls it
working before a power-outage reboot ~2 days ago.

**Findings (2026-06-02):**
- Data is present: the current-binary trace has 673 `discovered_map_csv` rows.
- The build logic is correct: the viewer's *exact* fog-build code, run offline
  in Node against the live trace, yields **`fog_timeline.length == 57`, 41 tiles**.
- `viewer.js` on disk is the clean, committed #438 version (the fix that stopped
  dropping observes); it is NOT an uncommitted post-reboot edit.

**Conclusion:** the regression is **browser-side**, not data or logic. Ranked
suspects:
1. **Stale cached `viewer.js`** — the browser is running a pre-#438 build (which
   starved fog) despite the `?v=N` cache-bust. (Most likely.)
2. **`world.json` ↔ run mismatch** — `fog_timeline` builds, but the Layer-2.5
   overlay dims *world* tiles by coordinate; if `world.json` is from a different
   world/seed than the run's character+server, the agent's known coords don't
   line up and nothing visibly dims.
3. **Scrub at step 0** — before the first observe, `currentFog()` is null by
   design; fog only appears once scrubbed past an observe.

**Definitive in-browser triage** (one console line): `STATE.data.fog_timeline?.length`
→ `0/undefined` ⇒ suspect 1 (stale JS); `57` ⇒ suspect 2/3 (rendering/overlay).

---

## 4. Goals — the redesign

R1. **Single map + fog-of-war toggle.** One SVG; a toggle flips between
   *god-view* (all world tiles bright) and *agent-knowledge* (only/highlighted
   discovered tiles, rest dimmed) at the current scrub point. (Rejected the
   two-map layout: costs width better spent on trace+log; the toggle on the
   *same* tiles tells the knowledge-gap story more sharply.)

R2. **Color-coded navigable Trace** (replaces `Episodes`, left-upper): a flat,
   continuous, scannable event list for the WHOLE run (not split into episode
   windows). Each row colored by event type so notable events (death, task
   success/failure, errors) are findable at a glance. Clicking a row sets the
   scrub position → drives map + state + log. It is a "search index" into the
   cognitive log.

R3. **Point-in-time State** (left-lower): HP, inventory, skill levels AS OF the
   current scrub position (e.g. at a death event: HP 0, exact loadout, skills
   then). Mostly a relocation — `renderState` already computes this.

R4. **Unified scrub.** One `STATE.stepIdx` (and `STATE.fogMode`) drives the
   trace highlight, the map (incl. fog), the state panel, and the log scroll
   position — bidirectionally (scrubber ⇄ trace-click).

Non-goals (this iteration): editing traces, multi-run compare, live-tailing
beyond the existing best-effort `?live` mode.

---

## 5. Committed substrate vs swappable presentation

Per project discipline, separate the stable contract from the tunable surface.

**Committed substrate — the trace-field contract the viewer reads** (changing
these is a trace-emitter change, coordinated):
- Span shape: `name`, `attributes`, `startTimeUnixNano`, `traceId`.
- Tool spans: `attributes["tool.name"]` ∈ {`HeroBenchObserve`, `HeroBenchPlan`,
  `HeroBenchAct`, `Pen`, `Notepad`, …}; `attributes["output.value"]` (object).
- Observe output: `discovered_map_csv` (the fog source), `character`
  (`hp`/`max_hp`, `position`, `inventory`, `skills`), `ambient_risk`,
  `belief_context`, `warning`.
- The **event taxonomy** (§6) — the classification keys the trace must expose
  enough signal to compute.

**Swappable presentation (hypothesis):** layout/proportions, the color palette,
which events map to which color, the fog toggle's default, map projection. These
change freely without touching the emitter.

---

## 6. Event taxonomy (color-coding for R2)

Each trace step is classified into one event type → one color. Derived from the
observe `character` state deltas + tool results + task outcomes:

| Event | Signal | Color |
|---|---|---|
| Agent death | `character.hp` transitions to 0 (or a death marker) | red (`--bad`) |
| Task solved | attempt `solved == true` / score crosses solve threshold | green (`--good`) |
| Task failed (attempt end, unsolved) | attempt boundary, `solved == false` | `--bad` — rendered on the attempt-boundary marker (`.tmark.failed`), not a per-step row |
| Action error | `HeroBenchAct` result is an error (e.g. HTTP 489/598) | orange (`--warn`) |
| Observe | `HeroBenchObserve` | blue (`--aql`) |
| Plan | `HeroBenchPlan` | teal (`--llm`) |
| Memory write | `Pen` / `Notepad` | purple / yellow (`--pen`/`--notepad`) |
| Level-up | a `skills[*].level` increases | `--good` (bold) |
| (default) act/llm step | — | neutral |

All colors reference CSS variables defined in the viewer's `:root`; there are no
dedicated "dim"/"accent" variants. Task-failed and level-up reuse `--bad`/`--good`
(the former on the boundary marker, the latter bold on the level-up row).

Open: exact "death" and "solve" signals to be confirmed against the trace
(attempt-result fields vs per-observe HP). Belongs in the impl's step-classifier.

---

## 7. Implementation plan (stages — each independently verifiable in-browser)

S0. **Fix the fog regression** (§3). Confirm via `fog_timeline.length` console
    check; if stale JS, harden cache-busting (content-hash query, not a manual
    integer) and document the hard-refresh; if `world.json` mismatch, make the
    agent-view path self-sufficient from `discovered_map_csv` (already coded for
    the no-world case) so it never depends on a matching `world.json`.

S1. **Fog-of-war toggle.** Add `STATE.fogMode` + a header toggle; gate the
    Layer-2.5 dimming (and/or the agent-only render) on it.

S2. **Color-coded navigable Trace** (R2): a step classifier (§6) + a flat
    color-coded list replacing `episode-list`; row-click sets `STATE.stepIdx`.
    Default: **one row per span** (list every step), with subtle per-attempt
    boundary markers — no run-collapsing (see O3).

S3. **Relocate point-in-time State** (R3): move `renderState` output to the
    left-lower panel; retire/repurpose `Run Summary`.

S4. **Unified scrub wiring** (R4): ensure scrubber, trace-click, map, state, and
    log all read/write the single `STATE.stepIdx`; log auto-scrolls to the
    selected step.

Verification is manual (browser), staged: the Claude-in-Chrome extension is not
currently connecting from the dev loop, so each stage is checked by the operator
(screenshot / `STATE.*` console reads) before the next.

---

## 8. Open questions

- O1. `world.json` provenance: is the checked-in `world.json` guaranteed to match
  the run's world (character + server port + seed)? If not, the god-view is
  misleading and the agent-view should render purely from `discovered_map_csv`.
- O2. Exact death/solve signals in the trace for the §6 classifier.
- O3. **Resolved:** the trace **lists every step** (one `.trow` per span, in
  `renderTrace`) — no run-collapsing. It stays scannable enough at current run
  sizes; collapsing repetitive `HeroBenchAct` runs into expand-on-click groups is
  a possible later enhancement, not implemented.
