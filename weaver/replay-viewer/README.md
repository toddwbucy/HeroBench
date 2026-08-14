# HeroBench Replay Viewer

Throwaway diagnostic tool for inspecting HeroBench agent runs at the
cognitive-step level. Renders a `weaver-trace` JSONL file as an
interactive replay: SVG map of the full HeroBench world with the
agent's path overlaid, synchronized cognitive log (LLM I/O, tool calls
with full content, Pen/Notepad writes, AQL queries), inventory and
skill state per step.

**Status:** v0.1, scripts directory, not part of the WeaverTools build.
Lives here so it's easy to throw away when WeaverTools or HeroBench
shifts shape.

## Why this exists

`weaver trace view` is generic and shows whether spans are flowing.
This tool is HeroBench-specific and shows *what the agent saw, decided,
and did*, step by step, **on top of the actual game world map**. Built
specifically to make non-exploration / lock-in failure modes visible in
seconds rather than hours of grep.

---

## How to view a trace — the canonical process

### Prerequisites

1. A trace file: `*.jsonl` somewhere under `/bulk-store/weaver-traces/<agent>/`.
2. A reachable HeroBench server. Any port works since the world data is
   static; for our cohort agents it's typically `http://127.0.0.1:8000`
   (GPU 0) or `http://127.0.0.1:8001` (GPU 1). One curl confirms it's
   alive: `curl -s http://127.0.0.1:8000/maps | head -c 200`.

### Steps

```bash
cd /opt/weavertools/scripts/herobench-replay-viewer

# 1. Extract episodes.json from the raw trace, fetching world data.
#    Convention: name the output <agent>_<YYYY-MM-DD>.episodes.json so
#    multiple runs don't collide.
python3 extract_episodes.py \
    /bulk-store/weaver-traces/herobench-benchero-1/run_*.jsonl \
    --out benchero-1_$(date +%Y-%m-%d).episodes.json \
    --world-host http://127.0.0.1:8001

# 2. Serve the directory over HTTP so the viewer can fetch the file
#    and the auto-fetch fallback works for raw .jsonl drops.
python3 -m http.server 8085

# 3. Open the viewer with the file as a query parameter.
xdg-open "http://localhost:8085/?file=benchero-1_$(date +%Y-%m-%d).episodes.json"
```

That's it. The viewer opens, the JSON loads, the map renders, and you
can scrub through episodes by clicking them in the left panel.

### Why HTTP, not file://

The `python3 -m http.server` step matters for two reasons:

- **Cache invalidation.** Browsers cache `viewer.js` aggressively under
  `file://` URLs; HTTP server lets hard-refresh actually pick up edits.
- **The `?file=` URL parameter.** Direct-load via the URL avoids the
  drag-drop file-picker confusion (which file is the right one?).
  An HTTP origin is required for the `fetch()` call that loads it.

If you forget to start the HTTP server first, you'll get
"file not found" or "ERR_CONNECTION_REFUSED" when opening the URL —
the easiest tell.

---

## What the map shows

- **Biome tiles** for the full HeroBench world (357 tiles for the
  current snapshot — Forest, Lake, Graveyard, City, Spawn, the single
  Forest-Forge tile that holds the smelter).
- **Content markers** (R=resource, M=monster, W=workshop, T=task
  master, B=bank, $=grand exchange) on the 70 tiles that have content.
  Hover any marker for the full content code.
- **Agent footprint outlines** on tiles the agent touched in the
  selected episode:
  - Green = at least one successful action at that tile.
  - Red = action(s) failed at that tile.
  - White-dashed = observed but no action taken.
  - White solid = agent's current position.
  - Faded-dashed = will be visited later in the episode (after current
    scrub position).
- **White trail line** showing movement order, time-gated to the
  current step.
- **White dot** showing the agent's actual position at the current
  step (updates as you scrub through successful move actions).

## What the cognitive log shows

Every cognitively-significant span, time-ordered, with the current
step highlighted. Each entry expands to show its full content:

- **observe** — `HeroBenchObserve` calls; character state at that moment.
- **llm** — Decoder LLM calls. The output messages are the agent's
  actual reasoning text. Input messages collapsed-by-default to keep
  the panel scannable; click the `details` to see the full prompt.
- **act** — `HeroBenchAct` calls with action verb (move, gather,
  craft, fight, equip, etc.) and parameters. Errored acts in red.
- **plan** — `HeroBenchPlan` calls with the proposed plan + steps.
- **pen** — Pen writes (the agent's external memory). Topic + full
  content visible inline.
- **notepad** — Notepad reads (recall of prior Pen content).
- **aql** — ArangoDB queries (database retrievals). Bind vars and
  results preview available via `details`.
- **nap** — Context naps (cache pressure events).

## Live mode

Click the `● Live: off` button in the header to enable polling. The
viewer re-fetches the file every 5 seconds and updates the map +
log + state panels in place. Useful for watching an in-flight run.

Requirements:
- The file must have been loaded via the `?file=` URL parameter
  (HTTP server mode). The button is disabled when a file was
  drag-dropped — `File` objects can't be re-read without a fresh
  user gesture.
- Either a raw `.jsonl` (re-extracted in-browser each poll) or a
  pre-built `episodes.json` works. For long-running traces, a
  Python-side script that re-extracts every minute may be more
  efficient than per-poll in-browser extraction.

Behavior:
- If you're scrubbed at the **end** of the current episode, the
  viewer auto-advances to the latest step on each refresh
  (tail-follow).
- If you've scrubbed **earlier** in the episode, your position is
  preserved across refreshes so you can study a moment without it
  scrolling away.
- New episodes appear in the list as `task_attempt` spans complete.
- Selected episode is preserved by (task, attempt) identity, not
  list index, so it follows even if the list reorders.

To watch a running cohort agent live:

```bash
cd /opt/weavertools/scripts/herobench-replay-viewer

# 1. Snapshot the world data once into world.json. Same-origin file
#    avoids the CORS block that prevents direct cross-origin fetches
#    from the viewer to HeroBench (localhost:8085 ≠ 127.0.0.1:8000
#    under browser policy, no CORS middleware on HeroBench).
python3 fetch_world.py --host http://127.0.0.1:8000 --out world.json

# 2. Symlink the live trace(s) into this directory so http.server
#    (rooted here) can reach them.
ln -sf /bulk-store/weaver-traces/herobench-benchero-3/run_*.jsonl ./live-b3.jsonl
ln -sf /bulk-store/weaver-traces/herobench-benchero-2/run_*.jsonl ./live-b2.jsonl

# 3. Serve and open.
python3 -m http.server 8085 &
xdg-open "http://localhost:8085/?file=live-b3.jsonl"
```

Click the **● Live: off** button in the header to flip it on. Every
5 seconds the viewer re-fetches the JSONL, parses any new spans,
updates the map and cognitive log. The world data is cached after
the first load — subsequent polls just re-read the trace bytes,
not the world endpoints.

**Re-snapshot `world.json` only when:**
- HeroBench's world data actually changes (rare — the map is static
  by design).
- You switch to a different HeroBench server with different content.
- The pinned `world.json` is missing or stale.

## Keyboard shortcuts

- `←` / `→` — step back / forward
- `Space` — play / pause autoplay
- `Home` / `End` — first / last step in current episode

## File layout

| File | Role |
|---|---|
| `extract_episodes.py` | Reads `weaver-trace` JSONL; emits `<agent>_<date>.episodes.json` grouped by `task_attempt`. Optional `--world-host` fetches the static HeroBench world data and embeds it. |
| `index.html` | Static HTML shell + CSS. Loads `viewer.js`. |
| `viewer.js` | All viewer logic. Builds DOM via createElement (no innerHTML on user content). Includes an in-browser JSONL extractor as a fallback when raw traces are dropped, and a world auto-fetch when served from HTTP origin. |
| `*.episodes.json` | Generated digests. Convention: `<agent>_<YYYY-MM-DD>.episodes.json`. |

## Diagnostic guide — common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Browser says "ERR_CONNECTION_REFUSED" | HTTP server not running, or running on different port | Start `python3 -m http.server 8085` in this directory |
| Viewer loads but map is all black with red "NO WORLD DATA" overlay | Loaded a file without world data (raw `.jsonl` drop or pre-world-fetch episodes.json) | Re-extract with `--world-host http://127.0.0.1:8000` (or 8001) |
| Header shows wrong agent name | Loaded the wrong file | Check the meta line in the header; pick the right `<agent>_*.episodes.json` |
| Edits to `viewer.js` aren't taking effect | Browser cache | Use HTTP server (not file://); hard-refresh with Ctrl+Shift+R; or DevTools → Network tab → Disable cache |
| Some episodes have no `observe` step at start | Bench harness recorded an attempt that didn't reach an observation (context overflow / immediate failure) | Pick a different attempt; this is data, not a viewer bug |

## Episode digest schema (what extract_episodes.py emits)

```jsonc
{
  "agent": "herobench-benchero-1",
  "run_id": "...",
  "model": "qwen3-coder-30b-q6-gpu1",
  "host": "...",
  "first_ts_ns": 0, "last_ts_ns": 0,
  "world": {
    "fetched_from": "http://127.0.0.1:8001",
    "tiles": [{"name": "Forest", "skin": "forest_3", "x": 8, "y": 10, "content": null}, ...],
    "resources": [{"name": "Copper Rocks", "code": "copper_rocks", "skill": "mining", ...}, ...],
    "monsters": [{"name": "Chicken", "code": "chicken", "level": 1, ...}, ...],
    "items": [...]
  },
  "episodes": [
    {
      "task": "task_d4_1", "attempt": 12,
      "score": 100.0, "solved": true,
      "actions_taken": 9, "actions_succeeded": 6,
      "task_type": "craft", "difficulty": 4,
      "start_ns": 0, "end_ns": 0, "duration_s": 39.0,
      "steps": [
        // one entry per cognitively-significant span, time-ordered:
        // kind: "observe" | "llm" | "act" | "plan"
        //     | "pen" | "notepad" | "aql" | "nap" | "tool"
      ],
      "trail": [ {"x": 0, "y": 0, "ts_ns": 0} ],
      "discovered_tiles": { "x,y": {"x": 0, "y": 0, "first_seen_ns": 0, "events": []} }
    }
  ]
}
```

## What isn't here (yet)

- **Diff view**: side-by-side replay of two episodes (e.g. d4_1 #4 vs
  #12) with synchronized scrubbing. High-leverage; deferred to v0.2.
- **Jump-to-event search**: "first move to (5,5)", "first Pen
  containing 'impossible'", etc. Manual scrubbing covers the current
  case but doesn't scale.
- **HADES retrieval visualization beyond AQL spans**: the AQL bind vars
  and result preview show what was retrieved, but a more graph-aware
  view (which `belief_node`s, edge weights) would be better. Out of
  scope for v0.1.

## Throwaway discipline

This tool is intentionally not part of the WeaverTools build, not
maintained as part of the harness, not written for external audiences.
If WeaverTools' trace format changes or HeroBench is retired, throw
this away and rebuild against whatever shape exists then.
