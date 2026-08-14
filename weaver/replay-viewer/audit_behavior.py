#!/usr/bin/env python3
"""Behavior-audit script for herobench episodes.json digests.

Measures the metrics defined in `project_walking_observe_prompt_experiment`:
  - Move-distance distribution (Chebyshev). Walking → mode at 1.
  - Observe-after-move proportion. Baseline: 2.6%.
  - Off-map move attempts (server should reject these).
  - Verdict-spam Pen-write count (lock-in failure-mode proxy).
  - Per-task attempt counts.

Usage:
    python3 audit_behavior.py <episodes.json> [<episodes.json> ...]

Pass multiple files to compare arms (e.g., baseline + test-arm digests).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def audit_one(path: Path) -> dict:
    d = json.loads(path.read_text())
    worldset = set((t["x"], t["y"]) for t in (d.get("world") or {}).get("tiles", []))

    # Move-distance distribution from the trail (successful moves).
    move_dists = []
    biggest_jump = (0, None)
    for ep in d["episodes"]:
        prev = None
        for pt in ep["trail"]:
            if prev is not None:
                dx = abs(pt["x"] - prev["x"])
                dy = abs(pt["y"] - prev["y"])
                cheb = max(dx, dy)
                move_dists.append(cheb)
                if cheb > biggest_jump[0]:
                    biggest_jump = (cheb, f"{ep['task']}#{ep['attempt']} ({prev['x']},{prev['y']})->({pt['x']},{pt['y']})")
            prev = pt

    # Off-map move attempts (any HeroBenchAct move where destination not in world).
    offmap_attempts = 0
    offmap_succeeded = 0
    if worldset:
        for ep in d["episodes"]:
            for s in ep["steps"]:
                if s.get("kind") == "act" and s.get("action") == "move":
                    p = s.get("params") or {}
                    x, y = p.get("x"), p.get("y")
                    if x is None or y is None:
                        continue
                    if (x, y) not in worldset:
                        offmap_attempts += 1
                        if not s.get("error"):
                            offmap_succeeded += 1

    # Observe-after-move proportion.
    moves_total = 0
    moves_observed_after = 0
    followups = Counter()
    for ep in d["episodes"]:
        steps = ep["steps"]
        for i, s in enumerate(steps):
            if s.get("kind") == "act" and s.get("action") == "move" and not s.get("error"):
                moves_total += 1
                # Find next observe or act (skip aql/llm/notepad/pen).
                for j in range(i + 1, len(steps)):
                    ns = steps[j]
                    if ns.get("kind") in ("act", "observe"):
                        if ns.get("kind") == "observe":
                            moves_observed_after += 1
                            followups["observe"] += 1
                        else:
                            followups[ns.get("action") or "?"] += 1
                        break

    # Verdict-spam — Pen writes whose topic looks like a lock-in verdict.
    verdict_keywords = ("impossible", "cannot complete", "blocked", "definitive", "final confirmation")
    verdict_writes = 0
    by_task_verdicts: dict[str, int] = {}
    for ep in d["episodes"]:
        for s in ep["steps"]:
            if s.get("kind") == "pen":
                topic = (s.get("topic") or "").lower()
                content = (s.get("content") or "").lower()[:300]
                if any(k in topic or k in content for k in verdict_keywords):
                    verdict_writes += 1
                    by_task_verdicts[ep["task"]] = by_task_verdicts.get(ep["task"], 0) + 1

    # Per-task attempt counts.
    by_task: dict[str, dict] = {}
    for ep in d["episodes"]:
        t = ep["task"]
        e = by_task.setdefault(t, {"attempts": 0, "solved": 0, "best_score": 0.0})
        e["attempts"] += 1
        if ep.get("solved"):
            e["solved"] += 1
        if (ep.get("score") or 0) > e["best_score"]:
            e["best_score"] = ep.get("score") or 0

    return {
        "agent": d.get("agent"),
        "model": d.get("model"),
        "n_episodes": len(d["episodes"]),
        "move_distances": Counter(move_dists),
        "biggest_jump": biggest_jump,
        "offmap_attempts": offmap_attempts,
        "offmap_succeeded": offmap_succeeded,
        "moves_total": moves_total,
        "moves_observed_after": moves_observed_after,
        "followups": followups,
        "verdict_writes": verdict_writes,
        "by_task_verdicts": by_task_verdicts,
        "by_task": by_task,
    }


def render(name: str, r: dict) -> None:
    print(f"\n=========================================================")
    print(f"  {name}  ({r['agent']} · {r['model']})")
    print(f"=========================================================")
    print(f"  episodes: {r['n_episodes']}")
    print()
    print("  Move-distance distribution (Chebyshev):")
    total_moves = sum(r["move_distances"].values())
    if total_moves:
        for d in sorted(r["move_distances"].keys()):
            n = r["move_distances"][d]
            pct = 100 * n / total_moves
            bar = "█" * int(pct / 2)
            label = "(adjacent/diag)" if d == 1 else "(jump)" if d > 1 else "(self)"
            print(f"    cheb={d:2d}  {n:5d}  {pct:5.1f}%  {bar} {label}")
        adj = r["move_distances"].get(1, 0)
        jumps = sum(n for d, n in r["move_distances"].items() if d > 1)
        print(f"    adjacent rate: {100*adj/total_moves:.1f}%   jump rate: {100*jumps/total_moves:.1f}%")
        print(f"    biggest jump: {r['biggest_jump'][1]} (cheb={r['biggest_jump'][0]})")
    else:
        print("    (no successful moves)")
    print()
    print(f"  Off-map move attempts: {r['offmap_attempts']} (succeeded: {r['offmap_succeeded']})")
    print()
    print(f"  Move → next-meaningful-step (n={r['moves_total']} successful moves):")
    if r["moves_total"]:
        obs_pct = 100 * r["moves_observed_after"] / r["moves_total"]
        print(f"    observed-after-move: {r['moves_observed_after']:5d}  {obs_pct:5.1f}%")
        for k, n in sorted(r["followups"].items(), key=lambda x: -x[1]):
            pct = 100 * n / r["moves_total"]
            print(f"    {k:12s}  {n:5d}  {pct:5.1f}%")
    print()
    print(f"  Verdict-style Pen writes (lock-in proxy): {r['verdict_writes']}")
    if r["by_task_verdicts"]:
        for t, n in sorted(r["by_task_verdicts"].items(), key=lambda x: -x[1]):
            print(f"    {t}: {n}")
    print()
    print(f"  Per-task summary:")
    for t in sorted(r["by_task"].keys()):
        e = r["by_task"][t]
        print(f"    {t}: {e['attempts']:3d} attempts · solved {e['solved']} · best {e['best_score']:.0f}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            continue
        r = audit_one(path)
        render(path.stem, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
