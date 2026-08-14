#!/usr/bin/env python3
"""Audit associative-surfacing behavior across HeroBench episodes.json digests.

The read-side sibling of notepad_query_audit.py. Where the notepad audit
measures *pull* (did the agent query its own memory?), this measures *push*
(did the harness surface memory onto the model's turn, and did surfacing
precede task progress?). It reads the `agent.surfacing.*` span attributes
that extract_episodes.py folds into each episode's `surfacing` steps.

Metrics (per the Loop 1 eval gate, loop1-memory-push-eval-gate §8):

  - Surfacing rate: fraction of attempts on which the reader fired AND
    emitted at least one <memory> block (surfaced_count > 0). Attempts with
    a surfacing step that surfaced nothing count as "fired, empty."
  - Recipe-hit rate: fraction of *fired* turns that surfaced >= 1 block.
    For Loop 1 the preseed corpus IS the recipe book, so a non-empty
    surface is a recipe hit by construction.
  - Did-surface-precede-progress: of the attempts where surfacing surfaced
    >= 1 block, the fraction that solved OR scored strictly higher than the
    prior attempt of the same task. The lock-in-break signal the gate cares
    about.
  - Funnel summary: mean candidate -> threshold-eligible -> surfaced, plus
    drops (budget, recency) and truncations.

Usage:
    python3 surfacing_audit.py <episodes.json> [<episodes.json> ...]

Pass multiple digests to compare arms (control surfacing.enabled=false vs
treatment=true). A control-arm digest will simply show zero surfacing
activity, which is the expected shape for the pull-only control.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _num(v: Any) -> float:
    """Coerce a span-attribute count to float; missing/garbage -> 0.0."""
    if isinstance(v, bool):  # bool is an int subclass — exclude explicitly
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def _surfacing_steps(ep: dict) -> list[dict]:
    return [s for s in ep.get("steps", []) if s.get("kind") == "surfacing"]


def audit_one(path: Path) -> dict:
    d = json.loads(path.read_text())
    episodes = d.get("episodes", [])

    n_attempts = len(episodes)
    fired_attempts = 0  # attempts with >= 1 surfacing step
    surfaced_attempts = 0  # attempts that surfaced >= 1 block
    fired_turns = 0  # total surfacing spans (turns the reader ran)
    hit_turns = 0  # surfacing spans with surfaced_count > 0

    # Funnel accumulators (over fired turns).
    sum_candidate = 0.0
    sum_eligible = 0.0
    sum_surfaced = 0.0
    sum_dropped_budget = 0.0
    sum_truncated = 0.0
    sum_recency = 0.0

    per_task_fired: dict[str, int] = defaultdict(int)
    per_task_surfaced: dict[str, int] = defaultdict(int)
    per_task_attempts: dict[str, int] = defaultdict(int)

    # For did-surface-precede-progress: track prior-attempt score per task.
    # Episodes may not be ordered, so sort each task's attempts by attempt num.
    by_task: dict[str, list[dict]] = defaultdict(list)
    for ep in episodes:
        by_task[ep.get("task", "?")].append(ep)

    surfaced_progress_num = 0  # surfaced attempts that progressed
    surfaced_progress_den = 0  # surfaced attempts considered

    for task, eps in by_task.items():
        eps_sorted = sorted(eps, key=lambda e: (e.get("attempt") or 0))
        prev_score: float | None = None
        for ep in eps_sorted:
            per_task_attempts[task] += 1
            steps = _surfacing_steps(ep)
            score = _num(ep.get("score"))
            solved = bool(ep.get("solved"))

            attempt_surfaced = False
            if steps:
                fired_attempts += 1
                per_task_fired[task] += 1
                for s in steps:
                    fired_turns += 1
                    sc = _num(s.get("surfaced_count"))
                    sum_candidate += _num(s.get("candidate_count"))
                    sum_eligible += _num(s.get("threshold_eligible"))
                    sum_surfaced += sc
                    sum_dropped_budget += _num(s.get("dropped_for_budget"))
                    sum_truncated += _num(s.get("truncated_blocks"))
                    sum_recency += _num(s.get("recency_dropped"))
                    if sc > 0:
                        hit_turns += 1
                        attempt_surfaced = True

            if attempt_surfaced:
                surfaced_attempts += 1
                per_task_surfaced[task] += 1
                # Progress = solved this attempt, or scored strictly higher
                # than the prior attempt of the same task.
                surfaced_progress_den += 1
                progressed = solved or (prev_score is not None and score > prev_score)
                if progressed:
                    surfaced_progress_num += 1

            prev_score = score

    return {
        "agent": d.get("agent"),
        "run_id": d.get("run_id"),
        "n_attempts": n_attempts,
        "fired_attempts": fired_attempts,
        "surfaced_attempts": surfaced_attempts,
        "fired_turns": fired_turns,
        "hit_turns": hit_turns,
        "funnel": {
            "candidate": sum_candidate,
            "eligible": sum_eligible,
            "surfaced": sum_surfaced,
            "dropped_for_budget": sum_dropped_budget,
            "truncated_blocks": sum_truncated,
            "recency_dropped": sum_recency,
        },
        "surfaced_progress_num": surfaced_progress_num,
        "surfaced_progress_den": surfaced_progress_den,
        "per_task_attempts": dict(per_task_attempts),
        "per_task_fired": dict(per_task_fired),
        "per_task_surfaced": dict(per_task_surfaced),
    }


def _pct(num: float, den: float) -> str:
    return f"{(100.0 * num / den):.1f}%" if den else "n/a"


def _mean(total: float, den: float) -> str:
    return f"{(total / den):.2f}" if den else "n/a"


def render(name: str, r: dict) -> None:
    print(f"\n=== {name} ===")
    print(f"  agent: {r.get('agent')}  run_id: {r.get('run_id')}")
    n = r["n_attempts"]
    print(f"  attempts: {n}")
    if r["fired_turns"] == 0:
        print("  surfacing: reader never fired (control arm / disabled / no spans)")
        return
    print(
        f"  surfacing rate (attempts surfaced >=1): "
        f"{r['surfaced_attempts']}/{n} = {_pct(r['surfaced_attempts'], n)}"
    )
    print(
        f"  reader fired on: {r['fired_attempts']}/{n} attempts "
        f"({r['fired_turns']} turns total)"
    )
    print(
        f"  recipe-hit rate (fired turns surfaced >=1): "
        f"{r['hit_turns']}/{r['fired_turns']} = {_pct(r['hit_turns'], r['fired_turns'])}"
    )
    print(
        f"  did-surface-precede-progress: "
        f"{r['surfaced_progress_num']}/{r['surfaced_progress_den']} = "
        f"{_pct(r['surfaced_progress_num'], r['surfaced_progress_den'])}"
    )
    f = r["funnel"]
    ft = r["fired_turns"]
    print("  funnel (mean per fired turn):")
    print(
        f"    candidate {_mean(f['candidate'], ft)} -> "
        f"eligible {_mean(f['eligible'], ft)} -> "
        f"surfaced {_mean(f['surfaced'], ft)}"
    )
    print(
        f"    dropped(budget) {_mean(f['dropped_for_budget'], ft)}  "
        f"recency-dropped {_mean(f['recency_dropped'], ft)}  "
        f"truncated {_mean(f['truncated_blocks'], ft)}"
    )
    print("  per-task surfacing rate:")
    for task in sorted(r["per_task_attempts"]):
        att = r["per_task_attempts"][task]
        surf = r["per_task_surfaced"].get(task, 0)
        print(f"    {task}: {surf}/{att} = {_pct(surf, att)}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rc = 0
    for arg in argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            rc = 1
            continue
        try:
            r = audit_one(path)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"error: failed to audit {path}: {e}", file=sys.stderr)
            rc = 1
            continue
        render(path.name, r)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
