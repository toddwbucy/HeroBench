#!/usr/bin/env bash
# Point live-b<N>.jsonl symlinks at the most-recently-modified
# run_*.jsonl in each agent's trace directory. Idempotent —
# run any time the agents restart or you change which agents
# you're monitoring.
#
# Usage: ./refresh_symlinks.sh [agent-numbers...]
#        ./refresh_symlinks.sh           # default: 0..5
#        ./refresh_symlinks.sh 4 5       # just the test arm
set -euo pipefail
cd "$(dirname "$0")"

agents=("${@:-0 1 2 3 4 5}")
for n in ${agents[@]}; do
  dir="/bulk-store/weaver-traces/herobench-benchero-$n"
  if [[ ! -d "$dir" ]]; then
    echo "  skip benchero-$n: $dir does not exist"
    continue
  fi
  latest=$(ls -t "$dir"/run_*.jsonl 2>/dev/null | head -1 || true)
  if [[ -z "$latest" ]]; then
    echo "  skip benchero-$n: no run_*.jsonl in $dir"
    continue
  fi
  ln -sf "$latest" "./live-b$n.jsonl"
  size=$(stat -c%s "$latest" 2>/dev/null || echo "?")
  echo "  benchero-$n -> $(basename "$latest") ($size bytes)"
done
echo
echo "URLs:"
for n in ${agents[@]}; do
  [[ -L "./live-b$n.jsonl" ]] && echo "  http://localhost:8085/?file=live-b$n.jsonl"
done
