#!/usr/bin/env bash
# Start the whole MiroFish-Offline stack in a single terminal:
#   Neo4j (Docker, detached) + Ollama (parallel) + backend + frontend.
# Ctrl-C stops the backend/frontend (and Ollama if this script started it).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

log() { printf '\033[36m[start_all]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[start_all]\033[0m %s\n' "$*"; }

if lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1 || lsof -nP -iTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then
  warn "port 3000 or 5001 is already in use (a previous backend/frontend is running)."
  warn "Stop it first:  ./scripts/stop_all.sh"
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  log "starting Neo4j (docker compose up -d neo4j)"
  docker compose up -d neo4j
else
  warn "Docker is not available; ensure a Neo4j is reachable at \$NEO4J_URI"
fi

# shellcheck source=scripts/lib_ollama.sh
source "$ROOT/scripts/lib_ollama.sh"
start_ollama_parallel

if [ ! -x node_modules/.bin/concurrently ]; then
  log "installing root npm dependencies (concurrently)..."
  npm install
fi
if [ ! -d frontend/node_modules ]; then
  log "installing frontend npm dependencies..."
  (cd frontend && npm install)
fi

log "starting backend + frontend  ->  GUI http://localhost:3000  (Ctrl-C to stop)"
npm run dev
