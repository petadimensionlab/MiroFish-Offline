#!/usr/bin/env bash
# Restart Ollama with parallel inference enabled, in the foreground.
# Quits an existing Ollama (macOS menu bar app or a previous server), then runs
# `ollama serve` with OLLAMA_NUM_PARALLEL etc. Ctrl-C stops it.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# shellcheck source=scripts/lib_ollama.sh
source "$ROOT/scripts/lib_ollama.sh"
start_ollama_parallel

if [ -n "$OLLAMA_PID" ]; then
  echo "[ollama] running (pid $OLLAMA_PID). Press Ctrl-C to stop."
  trap 'kill "$OLLAMA_PID" 2>/dev/null || true' EXIT INT TERM
  wait "$OLLAMA_PID" 2>/dev/null || true
else
  echo "[ollama] an existing server is still running (parallel settings may not be applied)."
fi
