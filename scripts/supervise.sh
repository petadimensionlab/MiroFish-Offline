#!/usr/bin/env bash
# Stage-aware supervisor for the MiroFish prepare/run.
# Detects genuine stalls using MULTIPLE progress signals (not a single counter),
# tolerates normal in-generation errors/retries, and recovers non-destructively
# by restarting with resume/reuse. Stops once the guarded phase is complete.
set -uo pipefail
cd "$(dirname "$0")/.."
SIM="${SIM_ID:-sim_39b482b9c3c9}"
PROJECT_ID="${PROJECT_ID:-proj_b44cd4c0e1ff}"
DIR="backend/uploads/simulations/$SIM"
BLOG="backend/logs/$(date +%F).log"
RESUME_LOG=/tmp/mirofish-resume.log
LOG=/tmp/mirofish-supervise.log
STALL="${STALL_SECONDS:-600}"
MAX_RESTARTS="${MAX_RESTARTS:-20}"
PARALLEL="${PARALLEL_PROFILES:-16}"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }
running() { pgrep -f "mirofish_cli.py resume" >/dev/null; }
prof_count() { python3 -c "import json;print(len(json.load(open('$DIR/reddit_profiles.json'))))" 2>/dev/null || echo 0; }
prepare_done() { [ -f "$DIR/simulation_config.json" ]; }

# Multi-signal fingerprint: ANY change means the job is progressing.
fingerprint() {
  local p cfg tw cprog cli oll
  p=$(prof_count)
  cfg=$([ -f "$DIR/simulation_config.json" ] && echo 1 || echo 0)
  tw=$([ -f "$DIR/twitter_profiles.csv" ] && echo 1 || echo 0)
  cprog=$(grep -a "report_progress" "$BLOG" 2>/dev/null | tail -1 | grep -oE "[0-9]+/[0-9]+" | head -1)
  cli=$(tail -1 "$RESUME_LOG" 2>/dev/null | cksum | cut -d' ' -f1)
  oll=$(tail -1 /tmp/mirofish-ollama.log 2>/dev/null | cksum | cut -d' ' -f1)
  echo "$p|$cfg|$tw|$cprog|$cli|$oll"
}

start_run() {
  pkill -f "run_cli.sh" 2>/dev/null; pkill -f "scripts/mirofish_cli.py" 2>/dev/null; pkill -f "bin/python run.py" 2>/dev/null
  sleep 4
  set -a; . ./.env; set +a
  : > "$RESUME_LOG"
  nohup ./scripts/run_cli.sh resume --project-id "$PROJECT_ID" --run --max-rounds 1 --parallel-profiles "$PARALLEL" --max-wait 21600 > "$RESUME_LOG" 2>&1 &
  sleep 20
}

log "supervisor start (STALL=${STALL}s, PARALLEL=$PARALLEL)"
running || { start_run; log "started run"; }
fp=$(fingerprint); changed=$(date +%s); restarts=0
last_step=""; prev_tsec=""

step_delta() {
  local line ts st tsec
  line=$(grep -a "report_progress" "$BLOG" 2>/dev/null | tail -1)
  [ -z "$line" ] && return
  ts=$(printf '%s' "$line" | grep -oE "\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8}\]" | tr -d '[]')
  st=$(printf '%s' "$line" | grep -oE "\[[0-9]+/[0-9]+\]" | head -1)
  [ -z "$ts" ] && return
  tsec=$(date -j -f "%Y-%m-%d %H:%M:%S" "$ts" +%s 2>/dev/null) || return
  if [ -n "$prev_tsec" ] && [ "$st" != "$last_step" ]; then
    log "config step $st took $((tsec - prev_tsec))s"
  fi
  prev_tsec=$tsec; last_step=$st
}

while true; do
  if prepare_done; then log "prepare complete (config generated); stop supervising"; break; fi
  cur=$(fingerprint)
  if [ "$cur" != "$fp" ]; then fp=$cur; changed=$(date +%s); log "progress: $cur"; step_delta; fi
  now=$(date +%s)
  if [ $((now - changed)) -ge "$STALL" ]; then
    [ "$restarts" -ge "$MAX_RESTARTS" ] && { log "max restarts ($MAX_RESTARTS); giving up"; break; }
    log "STALL: no signal change for ${STALL}s (fp=$cur) -> restart #$((restarts+1))"
    start_run; restarts=$((restarts+1)); fp=$(fingerprint); changed=$(date +%s)
  elif ! running; then
    sleep 15
    if ! running; then
      [ "$restarts" -ge "$MAX_RESTARTS" ] && { log "max restarts ($MAX_RESTARTS); giving up"; break; }
      log "run died (fp=$cur) -> restart #$((restarts+1))"
      start_run; restarts=$((restarts+1)); fp=$(fingerprint); changed=$(date +%s)
    fi
  fi
  sleep 30
done
log "supervisor exit (restarts=$restarts)"
