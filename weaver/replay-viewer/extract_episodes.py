#!/usr/bin/env python3
"""Extract episode data from a HeroBench trace JSONL into episodes.json.

The viewer (index.html) loads this. Raw trace JSONL is too big and
finely-grained for browser-side parsing; this extractor selects the spans
the viewer needs and groups them by (task, attempt) episode, ordered by
time within each episode.

Usage:
    python3 extract_episodes.py <trace.jsonl> [--out episodes.json]

The output schema is documented inline in build_episodes() below.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# How much DB query result content to keep per span. AQL results can be
# large; full content lives in the raw trace if needed.
AQL_RESULT_PREVIEW_CHARS = 4000
LLM_TEXT_PREVIEW_CHARS = 60000  # keep most reasoning text but cap pathologically long ones


def load_spans(path: Path) -> list[dict[str, Any]]:
    spans = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    spans.sort(key=lambda s: s.get("startTimeUnixNano", 0))
    return spans


def parse_tool_params(attrs: dict[str, Any]) -> dict[str, Any]:
    p = attrs.get("tool.parameters")
    if isinstance(p, str):
        try:
            return json.loads(p)
        except json.JSONDecodeError:
            return {}
    return p if isinstance(p, dict) else {}


def parse_tool_output(attrs: dict[str, Any]) -> Any:
    # `tool.output` is the canonical key per the HeroBench fork trace-consumer
    # contract; fall back to legacy `output.value` for pre-contract traces.
    out = attrs.get("tool.output", attrs.get("output.value"))
    if isinstance(out, str):
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out
    return out


def extract_position(observe_output: Any) -> dict[str, int] | None:
    if not isinstance(observe_output, dict):
        return None
    ch = observe_output.get("character") or {}
    if not isinstance(ch, dict):
        return None
    pos = ch.get("position")
    if isinstance(pos, dict) and "x" in pos and "y" in pos:
        return {"x": pos["x"], "y": pos["y"]}
    if "x" in ch and "y" in ch:
        return {"x": ch["x"], "y": ch["y"]}
    return None


def summarize_character(observe_output: Any) -> dict[str, Any]:
    """Pull the operator-relevant subset of character state."""
    if not isinstance(observe_output, dict):
        return {}
    ch = observe_output.get("character") or {}
    if not isinstance(ch, dict):
        return {}
    pos = extract_position(observe_output) or {}
    inv = ch.get("inventory")
    if not isinstance(inv, list):
        inv = []
    skills = {}
    # HeroBench character spreads skill levels onto the character dict
    # as `<skill>_level` / `<skill>_xp` / `<skill>_max_xp`.
    for k, v in ch.items():
        if k.endswith("_level") and isinstance(v, (int, float)):
            skill = k[: -len("_level")]
            skills[skill] = {
                "level": v,
                "xp": ch.get(f"{skill}_xp"),
                "max_xp": ch.get(f"{skill}_max_xp"),
            }
    return {
        "position": pos,
        "hp": ch.get("hp"),
        "max_hp": ch.get("max_hp"),
        "name": ch.get("name"),
        # Character level + XP — distinct from per-skill levels. HeroBench
        # exposes these as top-level fields on the character payload.
        "level": ch.get("level"),
        "xp": ch.get("xp"),
        "max_xp": ch.get("max_xp"),
        "inventory": inv,
        "skills": skills,
        "weapon": ch.get("weapon_slot"),
        "body_armor": ch.get("body_armor_slot"),
        "helmet": ch.get("helmet_slot"),
        "boots": ch.get("boots_slot"),
    }


def summarize_belief_context(observe_output: Any) -> dict[str, Any]:
    if not isinstance(observe_output, dict):
        return {}
    bc = observe_output.get("belief_context") or {}
    if not isinstance(bc, dict):
        return {}
    return {
        "graph_size": bc.get("graph_size"),
        "active_constraints": bc.get("active_constraints"),
        "relevant_nodes": (bc.get("relevant_nodes") or [])[:30],
        "relevant_edges": (bc.get("relevant_edges") or [])[:30],
        "known_paths": (bc.get("known_paths") or [])[:10],
    }


def summarize_llm(attrs: dict[str, Any]) -> dict[str, Any]:
    inp = attrs.get("llm.input_messages") or []
    out = attrs.get("llm.output_messages") or []
    if not isinstance(inp, list):
        inp = []
    if not isinstance(out, list):
        out = []

    # Keep last few input messages (the relevant context for this turn)
    # and full output. Earlier input messages are typically the system
    # prompt + earlier turn echoes, which the viewer can show on demand.
    def trim(s: str) -> str:
        if isinstance(s, str) and len(s) > LLM_TEXT_PREVIEW_CHARS:
            return s[:LLM_TEXT_PREVIEW_CHARS] + f"\n\n[... truncated, {len(s) - LLM_TEXT_PREVIEW_CHARS} more chars ...]"
        return s if isinstance(s, str) else json.dumps(s)[:LLM_TEXT_PREVIEW_CHARS]

    def shape_msg(m: Any) -> dict[str, Any]:
        if not isinstance(m, dict):
            return {"role": "?", "content": str(m)[:200]}
        role = m.get("role", "?")
        c = m.get("content", "")
        if isinstance(c, list):
            # could be a list of blocks; flatten to a single string for display
            parts = []
            for block in c:
                if isinstance(block, dict):
                    parts.append(json.dumps(block))
                else:
                    parts.append(str(block))
            c = "\n".join(parts)
        return {"role": role, "content": trim(c) if isinstance(c, (str, list)) else json.dumps(c)[:LLM_TEXT_PREVIEW_CHARS]}

    return {
        "input_messages": [shape_msg(m) for m in inp],
        "output_messages": [shape_msg(m) for m in out],
        "tokens": {
            "prompt": attrs.get("llm.token_count.prompt"),
            "completion": attrs.get("llm.token_count.completion"),
            "total": attrs.get("llm.token_count.total"),
        },
        "stop_reason": attrs.get("llm.stop_reason"),
    }


def summarize_aql(attrs: dict[str, Any]) -> dict[str, Any]:
    res = attrs.get("query.results")
    res_str = json.dumps(res) if res is not None else ""
    if len(res_str) > AQL_RESULT_PREVIEW_CHARS:
        res_str = res_str[:AQL_RESULT_PREVIEW_CHARS] + f"\n\n[... truncated, {len(res_str) - AQL_RESULT_PREVIEW_CHARS} more chars ...]"
    return {
        "db": attrs.get("db"),
        "bind_vars": attrs.get("query.bind_vars"),
        "result_count": attrs.get("query.result_count"),
        "results_preview": res_str,
        "span_kind": attrs.get("openinference.span.kind"),
    }


def build_episodes(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Group spans into per-(task, attempt) episodes, time-ordered.

    Output schema:
        {
          "agent": "...", "run_id": "...", "model": "...", "host": "...",
          "first_ts_ns": int, "last_ts_ns": int,
          "episodes": [
            {
              "task": "task_d4_1", "attempt": 12,
              "score": 100.0, "solved": true,
              "actions_taken": 9, "actions_succeeded": 6,
              "start_ns": ..., "end_ns": ..., "duration_s": ...,
              "steps": [
                { "kind": "observe" | "llm" | "act" | "plan"
                          | "pen" | "notepad" | "aql" | "nap",
                  "ts_ns": ..., ... }
              ],
              "trail": [ {x, y, ts_ns} ],   # successful moves only
              "discovered_tiles": { "x,y": { ... } },  # per-tile observations
            },
            ...
          ]
        }
    """
    # Find task_attempt windows
    attempts = []
    agent = None
    run_id = None
    model = None
    host = None
    first_ts = None
    last_ts = None
    for s in spans:
        ts = s.get("startTimeUnixNano", 0)
        ets = s.get("endTimeUnixNano", 0)
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if ets > (last_ts or 0):
            last_ts = ets
        res = s.get("resource") or {}
        if agent is None:
            agent = res.get("agent.name")
        if run_id is None:
            run_id = res.get("weaver.run_id")
        if model is None:
            model = res.get("llm.model_name")
        if host is None:
            host = res.get("host.name")
        if s.get("name") == "task_attempt":
            a = s.get("attributes") or {}
            attempts.append(
                {
                    "task": a.get("herobench.task_name"),
                    "attempt": a.get("herobench.attempt_num"),
                    "score": a.get("herobench.score"),
                    "solved": a.get("herobench.solved"),
                    "actions_taken": a.get("herobench.actions_taken"),
                    "actions_succeeded": a.get("herobench.actions_succeeded"),
                    "task_type": a.get("herobench.task_type"),
                    "difficulty": a.get("herobench.difficulty"),
                    "start_ns": ts,
                    "end_ns": ets,
                    "duration_s": (ets - ts) / 1e9 if ets and ts else None,
                    "steps": [],
                    "trail": [],
                    "discovered_tiles": {},
                }
            )

    # Sort attempts by start time so binary search is meaningful, and
    # attach steps from spans whose start falls inside the window.
    attempts.sort(key=lambda x: x["start_ns"])
    starts = [a["start_ns"] for a in attempts]
    ends = [a["end_ns"] for a in attempts]

    def find_episode(ts: int) -> int | None:
        # Linear scan is fine — at most ~30 attempts per run.
        for i, (s_ns, e_ns) in enumerate(zip(starts, ends)):
            if s_ns <= ts <= e_ns:
                return i
        return None

    for s in spans:
        n = s.get("name")
        a = s.get("attributes") or {}
        ts = s.get("startTimeUnixNano", 0)
        ep_idx = find_episode(ts)
        if ep_idx is None:
            continue
        ep = attempts[ep_idx]

        if n == "tool":
            tname = a.get("tool.name")
            params = parse_tool_params(a)
            output = parse_tool_output(a)
            err = bool(a.get("error"))
            err_msg = a.get("error.message")
            if tname == "HeroBenchObserve":
                pos = extract_position(output)
                ep["steps"].append(
                    {
                        "kind": "observe",
                        "ts_ns": ts,
                        "params": params,
                        "character": summarize_character(output),
                        "belief_context": summarize_belief_context(output),
                        "ambient_risk": (output or {}).get("ambient_risk") if isinstance(output, dict) else None,
                        "warning": (output or {}).get("_budget_warning") if isinstance(output, dict) else None,
                        "error": err,
                        "error_message": err_msg,
                    }
                )
                # Record this tile as discovered
                if pos is not None:
                    key = f"{pos['x']},{pos['y']}"
                    ep["discovered_tiles"].setdefault(key, {"x": pos["x"], "y": pos["y"], "first_seen_ns": ts, "events": []})
            elif tname == "HeroBenchAct":
                action = params.get("action", "?")
                ep["steps"].append(
                    {
                        "kind": "act",
                        "ts_ns": ts,
                        "action": action,
                        "params": params,
                        "output": output if not isinstance(output, dict) else {
                            k: v for k, v in output.items() if k != "belief_update"
                        },
                        "belief_update_summary": (output.get("belief_update") if isinstance(output, dict) else None),
                        "error": err,
                        "error_message": err_msg,
                    }
                )
                if action == "move" and not err and "x" in params and "y" in params:
                    ep["trail"].append({"x": params["x"], "y": params["y"], "ts_ns": ts})
                # Annotate the discovered tile with what happened here
                if "x" in params and "y" in params:
                    key = f"{params['x']},{params['y']}"
                    tile = ep["discovered_tiles"].setdefault(
                        key, {"x": params["x"], "y": params["y"], "first_seen_ns": ts, "events": []}
                    )
                    tile["events"].append(
                        {"ts_ns": ts, "action": action, "success": not err, "params": params}
                    )
            elif tname == "HeroBenchPlan":
                ep["steps"].append(
                    {
                        "kind": "plan",
                        "ts_ns": ts,
                        "plan": params.get("plan"),
                        "steps": params.get("steps"),
                        "output": output,
                        "error": err,
                    }
                )
            elif tname == "Pen":
                ep["steps"].append(
                    {
                        "kind": "pen",
                        "ts_ns": ts,
                        "topic": params.get("topic"),
                        "content": params.get("content"),
                        "tags": params.get("tags"),
                        "location": params.get("location"),
                        "output": output,
                        "error": err,
                    }
                )
            elif tname == "Notepad":
                ep["steps"].append(
                    {
                        "kind": "notepad",
                        "ts_ns": ts,
                        "params": params,
                        "output": output,
                        "error": err,
                    }
                )
            else:
                # Capture other tools generically so nothing in the
                # operator's diagnostic surface goes missing.
                ep["steps"].append(
                    {
                        "kind": "tool",
                        "ts_ns": ts,
                        "tool_name": tname,
                        "params": params,
                        "output": output,
                        "error": err,
                        "error_message": err_msg,
                    }
                )
        elif n == "llm":
            ep["steps"].append({"kind": "llm", "ts_ns": ts, **summarize_llm(a)})
        elif n == "aql.query":
            # Keep these but coarse — they're noisy and most are mechanical.
            ep["steps"].append({"kind": "aql", "ts_ns": ts, **summarize_aql(a)})
        elif n == "context_nap":
            ep["steps"].append(
                {
                    "kind": "nap",
                    "ts_ns": ts,
                    "trigger": a.get("nap.trigger"),
                    "input_tokens": a.get("nap.input_tokens"),
                    "threshold": a.get("nap.threshold_tokens"),
                    "number": a.get("nap.number"),
                }
            )
        elif n == "surfacing":
            # Associative-surfacing reader span (associative-surfacing-Spec
            # §6.1). Carries the per-turn funnel counts; surfaced_keys is a
            # JSON-encoded array. Consumed by surfacing_audit.py.
            keys_raw = a.get("agent.surfacing.surfaced_keys")
            surfaced_keys: list[Any] = []
            if isinstance(keys_raw, str):
                try:
                    parsed = json.loads(keys_raw)
                    if isinstance(parsed, list):
                        surfaced_keys = parsed
                except json.JSONDecodeError:
                    surfaced_keys = []
            elif isinstance(keys_raw, list):
                surfaced_keys = keys_raw
            ep["steps"].append(
                {
                    "kind": "surfacing",
                    "ts_ns": ts,
                    "query_token_count": a.get("agent.surfacing.query_token_count"),
                    "candidate_count": a.get("agent.surfacing.candidate_count"),
                    "threshold_eligible": a.get("agent.surfacing.threshold_eligible"),
                    "surfaced_count": a.get("agent.surfacing.surfaced_count"),
                    "dropped_for_budget": a.get("agent.surfacing.dropped_for_budget"),
                    "truncated_blocks": a.get("agent.surfacing.truncated_blocks"),
                    "recency_dropped": a.get("agent.surfacing.recency_dropped"),
                    "surfaced_keys": surfaced_keys,
                }
            )
        elif n == "embed":
            # Emit nothing per-span; embed events are high-volume and
            # not directly diagnostic. Counts can be derived if needed.
            pass

    # Sort steps within each episode by ts_ns (already mostly true).
    for ep in attempts:
        ep["steps"].sort(key=lambda x: x["ts_ns"])
        ep["trail"].sort(key=lambda x: x["ts_ns"])

    return {
        "agent": agent,
        "run_id": run_id,
        "model": model,
        "host": host,
        "first_ts_ns": first_ts,
        "last_ts_ns": last_ts,
        "episodes": attempts,
    }


def fetch_world(host: str) -> dict[str, Any]:
    """GET /maps, /resources, /monsters, /items from a HeroBench FastAPI.

    Map data is static for any given HeroBench server, so this output
    can be reused across traces. Failure to reach the server is non-
    fatal — extraction proceeds without world data.
    """
    import urllib.error
    import urllib.request

    base = host.rstrip("/")
    out: dict[str, Any] = {}
    for endpoint, key in [
        ("/maps", "tiles"),
        ("/resources", "resources"),
        ("/monsters", "monsters"),
        ("/items", "items"),
    ]:
        url = f"{base}{endpoint}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                out[key] = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"warning: failed to fetch {url}: {e}", file=sys.stderr)
            out[key] = []
    out["fetched_from"] = base
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trace", type=Path, help="HeroBench trace JSONL file")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output episodes.json path (default: alongside trace)",
    )
    ap.add_argument(
        "--world-host",
        type=str,
        default=None,
        help="HeroBench server URL (e.g. http://127.0.0.1:8000) to fetch "
        "static world data (maps, resources, monsters, items) and embed "
        "in the output. Map data is static; one fetch is reused across traces.",
    )
    args = ap.parse_args()

    if not args.trace.exists():
        print(f"error: trace file not found: {args.trace}", file=sys.stderr)
        return 1

    print(f"loading {args.trace} ...", file=sys.stderr)
    spans = load_spans(args.trace)
    print(f"  {len(spans)} spans loaded", file=sys.stderr)

    episodes = build_episodes(spans)
    print(
        f"  {len(episodes['episodes'])} episodes; agent={episodes['agent']}",
        file=sys.stderr,
    )

    if args.world_host:
        print(f"fetching world from {args.world_host} ...", file=sys.stderr)
        episodes["world"] = fetch_world(args.world_host)
        n_tiles = len(episodes["world"].get("tiles", []))
        print(f"  {n_tiles} tiles fetched", file=sys.stderr)

    out_path = args.out or args.trace.with_suffix(".episodes.json")
    out_path.write_text(json.dumps(episodes, separators=(",", ":")))
    size_mb = out_path.stat().st_size / 1e6
    print(f"wrote {out_path} ({size_mb:.1f} MB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
