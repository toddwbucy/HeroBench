#!/usr/bin/env python3
"""Snapshot the HeroBench world data into world.json next to the viewer.

The HeroBench map is static, so one fetch is reused for every trace.
This script exists so the viewer can load world data from the same
origin as itself (avoiding CORS issues when fetching from
http://127.0.0.1:<herobench-port> while the viewer is served from
http://localhost:<viewer-port>).

Usage:
    python3 fetch_world.py [--host http://127.0.0.1:8000] [--out world.json]

Run this once after starting HeroBench. The viewer prefers a
same-origin world.json over a cross-origin fetch when present.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fetch(url: str) -> object:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="http://127.0.0.1:8000", help="HeroBench server URL")
    ap.add_argument("--out", type=Path, default=Path("world.json"), help="Output path")
    args = ap.parse_args()
    base = args.host.rstrip("/")

    out = {"fetched_from": base}
    for endpoint, key in [
        ("/maps", "tiles"),
        ("/resources", "resources"),
        ("/monsters", "monsters"),
        ("/items", "items"),
    ]:
        url = f"{base}{endpoint}"
        try:
            out[key] = fetch(url)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"warning: failed to fetch {url}: {e}", file=sys.stderr)
            out[key] = []

    args.out.write_text(json.dumps(out, separators=(",", ":")))
    print(
        f"wrote {args.out} — tiles={len(out['tiles'])}, "
        f"resources={len(out['resources'])}, monsters={len(out['monsters'])}, "
        f"items={len(out['items'])}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
