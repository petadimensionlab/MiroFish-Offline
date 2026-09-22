#!/usr/bin/env bash
# Run the MiroFish-Offline workflow from a SINGLE terminal, WITHOUT the GUI.
#
# Starts Neo4j + Ollama + the backend, then runs scripts/mirofish_cli.py with
# the arguments you pass. On exit it stops the backend (and any Ollama it
# started). Neo4j is left running so the next run starts fast.
#
# Usage:
#   ./scripts/run_cli.sh pipeline --requirement "..." --file doc.pdf --max-rounds 50
#   ./scripts/run_cli.sh sim-status --simulation-id sim_xxxx
#   ./scripts/run_cli.sh --help
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

log() { printf '\033[36m[run_cli]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[run_cli]\033[0m %s\n' "$*"; }

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  echo "       $0 --help   to list CLI commands" >&2
  exit 2
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  log "ensuring Neo4j is up"
  docker compose up -d neo4j >/dev/null
else
  warn "Docker unavailable; expecting an existing Neo4j at \$NEO4J_URI"
fi

# shellcheck source=scripts/lib_ollama.sh
source "$ROOT/scripts/lib_ollama.sh"
start_ollama_parallel

BACKEND_PID=""
BACKEND_LOG=/tmp/mirofish-backend.log
if lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1; then
  warn "port 5001 already in use; reusing the running backend"
else
  log "starting backend (log: $BACKEND_LOG)"
  ( cd backend && exec env FLASK_DEBUG=false .venv/bin/python run.py >"$BACKEND_LOG" 2>&1 ) &
  BACKEND_PID=$!
fi

cleanup() {
  set +e
  if [ -n "$BACKEND_PID" ]; then
    printf '[run_cli] stopping backend\n'
    pids=$(lsof -tiTCP:5001 -sTCP:LISTEN 2>/dev/null)
    [ -n "$pids" ] && kill $pids 2>/dev/null
    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
  fi
}
trap cleanup EXIT INT TERM

log "waiting for backend ..."
for _ in $(seq 1 40); do
  if curl -sf -m 2 http://localhost:5001/health >/dev/null 2>&1; then
    break
  fi
  if [ -n "$BACKEND_PID" ] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    warn "backend exited early; last log lines:"
    tail -20 "$BACKEND_LOG" >&2
    exit 1
  fi
  sleep 1
done
if ! curl -sf -m 2 http://localhost:5001/health >/dev/null 2>&1; then
  warn "backend did not become healthy; last log lines:"
  tail -20 "$BACKEND_LOG" >&2
  exit 1
fi
log "backend ready"

python3 scripts/mirofish_cli.py "$@"
