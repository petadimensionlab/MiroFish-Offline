#!/usr/bin/env bash
# Stop MiroFish-Offline processes.
# Usage: ./scripts/stop_all.sh [--ollama] [--neo4j] [--all]
#   (no flags) stops backend + frontend
#   --ollama   also stop the Ollama server
#   --neo4j    also stop the Neo4j container
#   --all      both of the above
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

stop_ollama=0
stop_neo4j=0
for arg in "$@"; do
  case "$arg" in
    --ollama) stop_ollama=1 ;;
    --neo4j) stop_neo4j=1 ;;
    --all) stop_ollama=1; stop_neo4j=1 ;;
  esac
done

stopped=0
if pkill -f "$ROOT/backend/.venv/bin/python3 run.py" 2>/dev/null; then
  echo "stopped backend"; stopped=1
fi
if pkill -f "$ROOT/frontend/node_modules/.bin/vite" 2>/dev/null; then
  echo "stopped frontend"; stopped=1
fi
pkill -f "$ROOT/backend/.venv/bin/python run.py" 2>/dev/null && stopped=1
[ "$stopped" = 0 ] && echo "no MiroFish backend/frontend process found"

if [ "$stop_ollama" = 1 ]; then
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true
  fi
  pids=$(lsof -tiTCP:11434 -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null
    echo "stopped Ollama"
  else
    echo "Ollama not running"
  fi
fi

if [ "$stop_neo4j" = 1 ]; then
  docker compose stop neo4j && echo "stopped Neo4j"
fi
