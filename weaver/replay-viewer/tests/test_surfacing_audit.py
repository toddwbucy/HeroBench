#!/usr/bin/env python3
"""Self-contained tests for surfacing_audit.audit_one.

Run directly (no pytest dependency):
    python3 scripts/herobench-replay-viewer/tests/test_surfacing_audit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import surfacing_audit as sa  # noqa: E402


def _surf_step(ts, candidate, eligible, surfaced, dropped=0, recency=0, truncated=0):
    return {
        "kind": "surfacing",
        "ts_ns": ts,
        "query_token_count": 7,
        "candidate_count": candidate,
        "threshold_eligible": eligible,
        "surfaced_count": surfaced,
        "dropped_for_budget": dropped,
        "truncated_blocks": truncated,
        "recency_dropped": recency,
        "surfaced_keys": [f"k{i}" for i in range(surfaced)],
    }


def _digest(episodes):
    return {"agent": "t", "run_id": "r", "episodes": episodes}


def test_empty_control_arm(tmp_write):
    # No surfacing steps anywhere → reader never fired.
    eps = [
        {"task": "task_d1_1", "attempt": 1, "score": 0.0, "solved": False, "steps": []},
        {"task": "task_d1_1", "attempt": 2, "score": 100.0, "solved": True, "steps": []},
    ]
    r = sa.audit_one(tmp_write(_digest(eps)))
    assert r["fired_turns"] == 0
    assert r["surfaced_attempts"] == 0
    assert r["n_attempts"] == 2


def test_funnel_and_rates(tmp_write):
    eps = [
        # fired + surfaced, no progress (first attempt, score 0)
        {"task": "task_d1_1", "attempt": 1, "score": 0.0, "solved": False,
         "steps": [_surf_step(10, candidate=8, eligible=4, surfaced=2)]},
        # fired + surfaced, progress (score rose 0 -> 50)
        {"task": "task_d1_1", "attempt": 2, "score": 50.0, "solved": False,
         "steps": [_surf_step(20, candidate=8, eligible=3, surfaced=1, recency=1)]},
        # fired but surfaced nothing (empty) — counts as fired, not surfaced
        {"task": "task_d1_1", "attempt": 3, "score": 50.0, "solved": False,
         "steps": [_surf_step(30, candidate=2, eligible=0, surfaced=0)]},
        # fired + surfaced, progress via solve
        {"task": "task_d1_1", "attempt": 4, "score": 100.0, "solved": True,
         "steps": [_surf_step(40, candidate=6, eligible=2, surfaced=1)]},
    ]
    r = sa.audit_one(tmp_write(_digest(eps)))

    assert r["n_attempts"] == 4
    assert r["fired_attempts"] == 4
    assert r["fired_turns"] == 4
    # surfaced >=1 block on attempts 1, 2, 4 (not 3)
    assert r["surfaced_attempts"] == 3
    assert r["hit_turns"] == 3
    # funnel sums over the 4 fired turns
    assert r["funnel"]["candidate"] == 24.0  # 8+8+2+6
    assert r["funnel"]["eligible"] == 9.0  # 4+3+0+2
    assert r["funnel"]["surfaced"] == 4.0  # 2+1+0+1
    assert r["funnel"]["recency_dropped"] == 1.0
    # progress among surfaced attempts: attempt 1 (no prev) no; attempt 2 yes
    # (0->50); attempt 4 yes (solved). den=3, num=2.
    assert r["surfaced_progress_den"] == 3
    assert r["surfaced_progress_num"] == 2
    assert r["per_task_surfaced"]["task_d1_1"] == 3


def test_attempt_ordering_independent_of_input_order(tmp_write):
    # Same as a 0->100 progression but episodes given out of order; the
    # auditor must sort by attempt before computing progress.
    eps = [
        {"task": "task_d2_1", "attempt": 2, "score": 100.0, "solved": True,
         "steps": [_surf_step(20, candidate=5, eligible=2, surfaced=1)]},
        {"task": "task_d2_1", "attempt": 1, "score": 0.0, "solved": False,
         "steps": [_surf_step(10, candidate=5, eligible=2, surfaced=1)]},
    ]
    r = sa.audit_one(tmp_write(_digest(eps)))
    # attempt 1 surfaced, no prior → not progress; attempt 2 surfaced + solved
    # → progress. If ordering were wrong, attempt-2-first would mark progress
    # spuriously and attempt 1 (0 < 100) would not.
    assert r["surfaced_progress_den"] == 2
    assert r["surfaced_progress_num"] == 1


def _run():
    import json
    import tempfile

    created: list[Path] = []

    def tmp_write(obj):
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(obj, fd)
        fd.close()
        p = Path(fd.name)
        created.append(p)
        return p

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(tmp_write)
                print(f"ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    for p in created:
        p.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
