#!/usr/bin/env python3
"""Embed-on-write vs decoder compute-efficiency table for a cohort.

Reads OpenInference span files (the same `trace.jsonl` files
`extract_episodes.py` consumes) and emits per-agent compute timings:

  - decoder throughput (completion tokens/s)
  - decoder wall vs total wall (utilization)
  - embed median / p95 / max latency, total wall, share of trace wall
  - inter-decoder gap and what fills it (embed / aql.query /
    insert_document / unaccounted), as a measure of how often the
    decoder is stopped waiting on memory substrate I/O

Usage:
    compute_efficiency_table.py <agent1.jsonl> [<agent2.jsonl> ...]

Designed for the fresh-agent-postfix-2026-05-12 cohort report so the
embed-on-write keep-up claim has its own first-class artifact next to
the behavior tables (audit_behavior.py, efficiency_table.py).
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path
from typing import Iterable


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def _load(path: Path) -> list[dict]:
    spans: list[dict] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            if s.get("startTimeUnixNano") is None or s.get("endTimeUnixNano") is None:
                continue
            spans.append(s)
    return spans


def _ms(s: dict) -> float:
    return (s["endTimeUnixNano"] - s["startTimeUnixNano"]) / 1e6


def _time_in_intervals(work: Iterable[dict], intervals: list[tuple[int, int]]) -> int:
    iv = sorted(intervals, key=lambda x: x[0])
    total = 0
    for w in work:
        ws, we = w["startTimeUnixNano"], w["endTimeUnixNano"]
        for gs, ge in iv:
            if ge < ws:
                continue
            if gs > we:
                break
            total += max(0, min(we, ge) - max(ws, gs))
    return total


def _analyze(path: Path) -> dict:
    spans = _load(path)
    by_name: dict[str, list[dict]] = {}
    for s in spans:
        by_name.setdefault(s["name"], []).append(s)

    embeds = by_name.get("embed", [])
    llms = by_name.get("llm", [])
    inserts = by_name.get("insert_document", [])
    aqls = by_name.get("aql.query", [])

    e_durs = [_ms(s) for s in embeds]
    l_durs = [_ms(s) for s in llms]

    embed_wall_ms = sum(e_durs)
    llm_wall_ms = sum(l_durs)
    tot_tok = sum(
        (s.get("attributes", {}).get("llm.token_count.completion") or 0) for s in llms
    )

    t0 = min(s["startTimeUnixNano"] for s in spans)
    t1 = max(s["endTimeUnixNano"] for s in spans)
    wall_ms = (t1 - t0) / 1e6

    llms_sorted = sorted(llms, key=lambda s: s["startTimeUnixNano"])
    gaps: list[tuple[int, int, int]] = []
    for i in range(len(llms_sorted) - 1):
        a = llms_sorted[i]
        b = llms_sorted[i + 1]
        gap = b["startTimeUnixNano"] - a["endTimeUnixNano"]
        if gap > 0:
            gaps.append((gap, a["endTimeUnixNano"], b["startTimeUnixNano"]))
    gap_iv = [(s, e) for _, s, e in gaps]

    embed_in_gap = _time_in_intervals(embeds, gap_iv)
    insert_in_gap = _time_in_intervals(inserts, gap_iv)
    aql_in_gap = _time_in_intervals(aqls, gap_iv)

    work = sorted(
        ((s["startTimeUnixNano"], s["endTimeUnixNano"]) for s in embeds + inserts + aqls),
        key=lambda x: x[0],
    )
    merged: list[tuple[int, int]] = []
    for s, e in work:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    union_in_gap = 0
    for ws, we in merged:
        for gs, ge in gap_iv:
            if ge < ws:
                continue
            if gs > we:
                break
            union_in_gap += max(0, min(we, ge) - max(ws, gs))
    total_gap = sum(g for g, _, _ in gaps)
    unaccounted = total_gap - union_in_gap

    return {
        "agent": path.stem.split(".")[0],
        "wall_s": wall_ms / 1000,
        "decoder_util_pct": llm_wall_ms / wall_ms * 100 if wall_ms else 0,
        "decoder_tok_per_s": (tot_tok / (llm_wall_ms / 1000)) if llm_wall_ms else 0,
        "decoder_calls": len(llms),
        "embed_calls": len(embeds),
        "embed_med_ms": st.median(e_durs) if e_durs else float("nan"),
        "embed_p95_ms": _percentile(e_durs, 0.95),
        "embed_max_ms": max(e_durs) if e_durs else float("nan"),
        "embed_total_s": embed_wall_ms / 1000,
        "embed_share_pct": embed_wall_ms / wall_ms * 100 if wall_ms else 0,
        "gap_total_s": total_gap / 1e9,
        "gap_share_pct": total_gap / (wall_ms * 1e6) * 100 if wall_ms else 0,
        "gap_embed_s": embed_in_gap / 1e9,
        "gap_aql_s": aql_in_gap / 1e9,
        "gap_insert_s": insert_in_gap / 1e9,
        "gap_unaccounted_s": unaccounted / 1e9,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1

    rows = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            continue
        rows.append(_analyze(p))
    if not rows:
        return 1

    headers = [
        ("agent", "agent", "{:<22s}"),
        ("wall_s", "wall(s)", "{:>9.1f}"),
        ("decoder_util_pct", "dec util%", "{:>9.2f}"),
        ("decoder_tok_per_s", "tok/s", "{:>6.1f}"),
        ("embed_calls", "n_embed", "{:>7d}"),
        ("embed_med_ms", "emb_med", "{:>7.1f}"),
        ("embed_p95_ms", "emb_p95", "{:>7.1f}"),
        ("embed_share_pct", "emb%wall", "{:>8.3f}"),
        ("gap_share_pct", "gap%wall", "{:>8.3f}"),
        ("gap_embed_s", "gap_emb_s", "{:>9.2f}"),
        ("gap_aql_s", "gap_aql_s", "{:>9.2f}"),
        ("gap_unaccounted_s", "unacct_s", "{:>9.2f}"),
    ]
    print("  ".join(f"{label:>{max(len(label), 7)}s}" for _, label, _ in headers))
    print("-" * 130)
    for r in rows:
        print("  ".join(fmt.format(r[key]) for key, _, fmt in headers))
    print()
    print("Columns:")
    print("  dec util%  = decoder_wall / total_wall — how much of the run was decoder generating")
    print("  emb%wall   = embed_wall / total_wall   — encoder share of run")
    print("  gap%wall   = (total - decoder_wall) / total — how often decoder was stopped")
    print("  gap_emb_s  = embed time landing in inter-decoder gaps (decoder waiting on encoder)")
    print("  gap_aql_s  = aql.query time in gaps (decoder waiting on memory-graph reads)")
    print("  unacct_s   = gap time with no embed/aql/insert active (tool exec / HTTP / scheduling)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
