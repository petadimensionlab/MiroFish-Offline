#!/usr/bin/env bash
# Append a periodic MiroFish status snapshot to /tmp/mirofish-monitor.log.
# Usage: ./scripts/monitor.sh [interval_seconds]
set -uo pipefail
cd "$(dirname "$0")/.."
INTERVAL="${1:-60}"
LOG=/tmp/mirofish-monitor.log
SIM_DIR=backend/uploads/simulations

while true; do
  ts=$(date '+%F %T')
  cli=$(tail -1 /tmp/mirofish-resume.log 2>/dev/null | sed 's/^ *//' | cut -c1-90)
  runner=$(ps -o command= -ax | grep -i llama-server | grep -v grep | grep -v embedding | tail -1 | grep -oE "\-c [0-9]+|\-np [0-9]+" | tr '\n' ' ')
  mem=$(top -l 1 -n 0 2>/dev/null | grep PhysMem | sed 's/^ *//')
  prof=$(python3 -c "import json,glob,os
d='$SIM_DIR/sim_39b482b9c3c9/reddit_profiles.json'
print(len(json.load(open(d))) if os.path.exists(d) else '?')" 2>/dev/null || echo '?')
  echo "[$ts] profiles=$prof | ${runner:-no-runner} | $mem | $cli" >> "$LOG"
  sleep "$INTERVAL"
done
