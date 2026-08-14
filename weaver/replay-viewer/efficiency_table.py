#!/usr/bin/env python3
"""Compute per-task action efficiency vs perfect-knowledge minimum.

For each task (d1_1..d9_1) and each agent's run, computes:
  - Target item the task asked for (inferred from the agent's last successful craft)
  - Minimum action count assuming perfect knowledge (spawn → gather → refine → craft, batched)
  - Actual action count summed across all attempts of that task
  - Ratio actual/minimum

Usage:
    python3 efficiency_table.py <episodes.json> [<episodes.json> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def find_resource_for_item(item_code, world):
    """Which resource drops this item?"""
    for r in world.get("resources", []):
        for drop in r.get("drops", []):
            if drop.get("code") == item_code:
                return r["code"]
    return None


def find_resource_tile(resource_code, content_loc):
    locs = content_loc.get(("resource", resource_code), [])
    return locs[0] if locs else None


def find_workshop_tile(skill, content_loc):
    locs = content_loc.get(("workshop", skill), [])
    return locs[0] if locs else None


def recipe_chain(item_code, items):
    """Return the ordered list of (item_code, qty, kind) where kind is
    'gather' (raw material) or 'craft' (intermediate or final). The list
    is in dependency order (deepest first), so executing them sequentially
    builds the chain.
    """
    out = []
    item = items.get(item_code)
    if not item:
        return out
    craft = item.get("craft")
    if not craft:
        # Raw item; just gather.
        out.append((item_code, 1, "gather"))
        return out
    # Crafted item. Recurse on each ingredient first.
    qty_per_craft = craft.get("quantity", 1)
    for ing in craft.get("items", []):
        ing_code = ing["code"]
        ing_qty = ing["quantity"]
        # If the ingredient itself is crafted, recurse.
        ing_item = items.get(ing_code, {})
        if ing_item.get("craft"):
            sub_chain = recipe_chain(ing_code, items)
            # Scale ingredient quantity (we need ing_qty of this intermediate
            # item, and each sub-craft makes some number of them — sub_chain
            # is per-1-craft, scale to ing_qty/sub_qty crafts).
            sub_qty = ing_item["craft"].get("quantity", 1)
            n_subcrafts = (ing_qty + sub_qty - 1) // sub_qty  # ceiling
            # Scale gather counts in sub_chain by n_subcrafts
            for code, qty, kind in sub_chain:
                if kind == "gather":
                    out.append((code, qty * n_subcrafts, "gather"))
                elif kind == "craft":
                    out.append((code, n_subcrafts, "craft"))
        else:
            # Raw ingredient; gather directly.
            out.append((ing_code, ing_qty, "gather"))
    out.append((item_code, 1, "craft"))
    return out


def aggregate_chain(chain):
    """Sum gather/craft counts by item_code."""
    gathers = {}
    crafts = {}
    for code, qty, kind in chain:
        if kind == "gather":
            gathers[code] = gathers.get(code, 0) + qty
        else:
            crafts[code] = crafts.get(code, 0) + qty
    return gathers, crafts


def min_steps_for_target(target_code, items, world, content_loc, spawn=(0, 0)):
    """Compute minimum-action path to craft target_code from spawn.

    Assumes:
      - Perfect knowledge of all tile locations.
      - Batched gather/craft calls (one call with quantity=N, not N calls).
      - Sequential visit order: gather all raw materials at one tile, then
        next raw-material tile, then refine intermediates at their
        workshops in order, then final craft at the target workshop.
      - Each move action = 1 step (Chebyshev distance for diagonal moves).
      - One initial observe.
    """
    chain = recipe_chain(target_code, items)
    if not chain:
        return None, "no recipe"
    gathers, crafts = aggregate_chain(chain)

    # Build the visit order. Gather raw materials first (resources), then
    # refine intermediates, then final craft.
    waypoints = []
    raw_codes = list(gathers.keys())
    for raw_code in raw_codes:
        res_code = find_resource_for_item(raw_code, world)
        if not res_code:
            return None, f"no resource drops {raw_code}"
        loc = find_resource_tile(res_code, content_loc)
        if not loc:
            return None, f"no tile for resource {res_code}"
        waypoints.append((loc, f"gather {gathers[raw_code]}× {raw_code} at {res_code}"))

    # Crafts in order (intermediates first, then the target).
    for craft_code, n_calls in crafts.items():
        item = items.get(craft_code, {})
        skill = item.get("craft", {}).get("skill")
        ws = find_workshop_tile(skill, content_loc)
        if not ws:
            return None, f"no {skill} workshop for {craft_code}"
        waypoints.append((ws, f"craft {n_calls}× {craft_code} at {skill} workshop"))

    # Compute path length.
    steps = 1  # initial observe
    pos = spawn
    detail = ["observe"]
    for loc, label in waypoints:
        d = cheb(pos, loc)
        steps += d  # moves
        steps += 1  # the action at that tile
        detail.append(f"  move {d}× ({pos} -> {loc})  then  {label}")
        pos = loc
    return steps, detail


def analyze(path: Path):
    d = json.loads(path.read_text())
    world = d.get("world", {})
    items = {it["code"]: it for it in world.get("items", [])}
    content_loc = {}
    for t in world.get("tiles", []):
        if t.get("content"):
            key = (t["content"]["type"], t["content"].get("code", ""))
            content_loc.setdefault(key, []).append((t["x"], t["y"]))

    # Group episodes by task
    by_task = {}
    for ep in d["episodes"]:
        by_task.setdefault(ep["task"], []).append(ep)

    rows = []
    for task in sorted(by_task.keys()):
        eps = by_task[task]
        # Find target item: last successful craft in any solving attempt
        target = None
        for ep in eps:
            for s in ep["steps"]:
                if (s.get("kind") == "act" and s.get("action") == "craft"
                        and not s.get("error")):
                    params = s.get("params") or {}
                    code = params.get("code")
                    if code:
                        target = code  # take the LAST successful craft
        if not target:
            rows.append({"task": task, "target": "?", "n_attempts": len(eps),
                         "min_steps": None, "actual": None, "ratio": None,
                         "solved": any(e.get("solved") for e in eps)})
            continue
        min_steps, _ = min_steps_for_target(target, items, world, content_loc)
        actual = sum((e.get("actions_taken") or 0) for e in eps)
        ratio = (actual / min_steps) if (min_steps and min_steps > 0) else None
        rows.append({
            "task": task, "target": target, "n_attempts": len(eps),
            "min_steps": min_steps, "actual": actual, "ratio": ratio,
            "solved": any(e.get("solved") for e in eps),
        })
    return d.get("agent", path.stem), rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    results = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            continue
        agent, rows = analyze(p)
        results.append((agent, rows))

    # Print one combined table.
    print(f"\n{'task':10s}  {'target':18s}  {'min':>4s}  ", end="")
    for agent, _ in results:
        short = agent.replace("herobench-benchero-", "b")
        print(f"{short:>13s}  ", end="")
    print()
    print("-" * (10 + 18 + 4 + 5 + 15 * len(results)))

    tasks = sorted(set(r["task"] for _, rows in results for r in rows))
    for task in tasks:
        first_row = next((r for _, rows in results for r in rows if r["task"] == task), None)
        target = first_row["target"] if first_row else "?"
        min_s = first_row["min_steps"] if first_row else None
        min_str = f"{min_s:4d}" if min_s else "  ? "
        print(f"{task:10s}  {target:18s}  {min_str}  ", end="")
        for agent, rows in results:
            r = next((x for x in rows if x["task"] == task), None)
            if r and r["actual"] is not None and r["ratio"] is not None:
                marker = "✓" if r["solved"] else "✗"
                print(f"{r['actual']:4d}/{r['n_attempts']:2d}×{r['ratio']:5.1f}×{marker} ", end="")
            else:
                print(f"{'—':>13s}  ", end="")
        print()
    print()
    print("Format per agent cell: <total_actions>/<attempts>×<ratio_to_min>× <solved>")
    print("Min is the perfect-knowledge minimum action count (single attempt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
