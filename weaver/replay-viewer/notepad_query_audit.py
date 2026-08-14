#!/usr/bin/env python3
"""Audit Notepad query behavior across a HeroBench episodes.json digest.

Measures the metrics the post-fix fresh-agent experiment needs to know
whether the substrate fix is actually being engaged:

  - Notepad queries per attempt (substrate-engagement rate)
  - Distribution of query keywords (semantic-shape proxy — short generic
    keywords like "task_d1_1" suggest substring-match thinking;
    longer thematic phrases suggest the agent is searching by meaning)
  - Notepad hit rate (queries that returned >= 1 result)
  - Preseed-source coverage (queries that returned at least one
    document with provenance.source == "preseed")
  - Per-task variance in query rate (does the agent search more on
    harder tasks?)

Usage:
    python3 notepad_query_audit.py <episodes.json> [<episodes.json> ...]

Pass multiple digests to compare arms (e.g., b6/b7 pre-fix vs the
post-fix cohort).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path


def parse_params(p):
    """Step params come through as a stringified dict in the digest;
    handle either form defensively."""
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            return json.loads(p.replace("'", '"'))
        except (json.JSONDecodeError, ValueError):
            m = re.match(r"\{['\"]keyword['\"]\s*:\s*['\"]([^'\"]+)['\"]\}", p)
            return {"keyword": m.group(1)} if m else {}
    return {}


def parse_output(o):
    if isinstance(o, dict):
        return o
    if isinstance(o, str):
        try:
            return json.loads(o)
        except json.JSONDecodeError:
            return {}
    return {}


def audit_one(path: Path) -> dict:
    d = json.loads(path.read_text())

    n_attempts = len(d["episodes"])

    queries = 0
    hits = 0
    preseed_returning = 0
    keyword_counter: Counter = Counter()
    keyword_len_dist: Counter = Counter()
    per_task_queries: dict[str, int] = {}
    per_task_attempts: dict[str, int] = {}
    per_task_hits: dict[str, int] = {}

    for ep in d["episodes"]:
        task = ep.get("task", "?")
        per_task_attempts[task] = per_task_attempts.get(task, 0) + 1
        for s in ep.get("steps", []):
            if s.get("kind") != "notepad":
                continue
            queries += 1
            per_task_queries[task] = per_task_queries.get(task, 0) + 1
            params = parse_params(s.get("params"))
            keyword = (params.get("keyword") or "").strip()
            keyword_counter[keyword] += 1
            keyword_len_dist[len(keyword)] += 1
            out = parse_output(s.get("output"))
            count = out.get("count", 0)
            if count and count >= 1:
                hits += 1
                per_task_hits[task] = per_task_hits.get(task, 0) + 1
                notes = out.get("notes") or []
                for note in notes:
                    if not isinstance(note, dict):
                        continue
                    prov = (note.get("provenance") or {}).get("source")
                    if prov == "preseed":
                        preseed_returning += 1
                        break

    return {
        "agent": d.get("agent"),
        "model": d.get("model"),
        "n_attempts": n_attempts,
        "queries": queries,
        "hits": hits,
        "preseed_returning": preseed_returning,
        "keyword_counter": keyword_counter,
        "keyword_len_dist": keyword_len_dist,
        "per_task_queries": per_task_queries,
        "per_task_attempts": per_task_attempts,
        "per_task_hits": per_task_hits,
    }


def render(name: str, r: dict) -> None:
    print(f"\n=========================================================")
    print(f"  {name}  ({r['agent']} · {r['model']})")
    print(f"=========================================================")
    n_att = r["n_attempts"]
    q = r["queries"]
    h = r["hits"]
    p = r["preseed_returning"]
    qpa = q / n_att if n_att else 0.0
    hit_rate = (100 * h / q) if q else 0.0
    preseed_rate = (100 * p / h) if h else 0.0
    print(f"  attempts: {n_att}   notepad queries: {q}   queries/attempt: {qpa:.2f}")
    print(f"  hit rate: {h}/{q} = {hit_rate:.1f}%")
    print(f"  preseed-returning hit rate: {p}/{h} = {preseed_rate:.1f}%   (of queries: {100*p/q if q else 0:.1f}%)")

    print()
    print("  Keyword-length distribution (proxy for semantic shape):")
    total = sum(r["keyword_len_dist"].values())
    if total:
        buckets = [(0, 0, "empty"), (1, 10, "short (1-10)"),
                   (11, 30, "medium (11-30)"), (31, 80, "long (31-80)"),
                   (81, 1000, "verbose (81+)")]
        for lo, hi, label in buckets:
            n = sum(c for length, c in r["keyword_len_dist"].items() if lo <= length <= hi)
            pct = 100 * n / total if total else 0.0
            bar = "█" * int(pct / 2)
            print(f"    {label:20s}  {n:5d}  {pct:5.1f}%  {bar}")

    print()
    print("  Top keywords:")
    for kw, n in r["keyword_counter"].most_common(10):
        print(f"    {n:4d}  {kw!r}")

    print()
    print("  Per-task query rate (queries / attempt):")
    for t in sorted(r["per_task_attempts"].keys()):
        a = r["per_task_attempts"][t]
        q_t = r["per_task_queries"].get(t, 0)
        h_t = r["per_task_hits"].get(t, 0)
        rate = q_t / a if a else 0.0
        hit_pct = (100 * h_t / q_t) if q_t else 0.0
        print(f"    {t}: {a:3d} attempts · {q_t:4d} queries · {rate:5.1f} q/att · {hit_pct:5.1f}% hit")


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
