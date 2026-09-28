#!/usr/bin/env bash
# Shared helper: (re)start Ollama so OLLAMA_NUM_PARALLEL actually takes effect.
# Sourced by scripts/run_cli.sh and scripts/start_all.sh.
#
# On macOS the menu bar app keeps its own `ollama serve` without these env vars,
# so a plain "already running -> reuse" would silently keep requests serialized.
# This helper quits/stops the existing server, then starts a fresh one with the
# concurrency settings.

start_ollama_parallel() {
  # Use a dedicated port so the macOS Ollama.app (which respawns on 11434 with a
  # bad -np) cannot clash with our tuned server. Override with OLLAMA_HOST.
  export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11500}"
  OLLAMA_PORT="${OLLAMA_HOST##*:}"
  export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-4}"
  export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-2}"
  export OLLAMA_MAX_QUEUE="${OLLAMA_MAX_QUEUE:-64}"
  export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
  export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"

  OLLAMA_PID=""

  if ! command -v ollama >/dev/null 2>&1; then
    echo "[ollama] WARNING: 'ollama' not found in PATH; skipping"
    return 0
  fi

  # macOS: also export into the launchd GUI session so a server the Ollama.app
  # (or its com.ollama.ollama agent) respawns also picks up the settings.
  if command -v launchctl >/dev/null 2>&1; then
    launchctl setenv OLLAMA_NUM_PARALLEL "$OLLAMA_NUM_PARALLEL" 2>/dev/null || true
    launchctl setenv OLLAMA_MAX_LOADED_MODELS "$OLLAMA_MAX_LOADED_MODELS" 2>/dev/null || true
    launchctl setenv OLLAMA_MAX_QUEUE "$OLLAMA_MAX_QUEUE" 2>/dev/null || true
    launchctl setenv OLLAMA_KEEP_ALIVE "$OLLAMA_KEEP_ALIVE" 2>/dev/null || true
    launchctl setenv OLLAMA_CONTEXT_LENGTH "$OLLAMA_CONTEXT_LENGTH" 2>/dev/null || true
  fi

  if lsof -nP -iTCP:"$OLLAMA_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[ollama] restarting to apply OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL (port $OLLAMA_PORT)"
    if command -v osascript >/dev/null 2>&1; then
      osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true
    fi
    pids=$(lsof -tiTCP:"$OLLAMA_PORT" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
    fi
    for _ in $(seq 1 15); do
      if ! lsof -nP -iTCP:"$OLLAMA_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  else
    echo "[ollama] starting with OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL (port $OLLAMA_PORT)"
  fi

  if lsof -nP -iTCP:"$OLLAMA_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[ollama] WARNING: could not stop the existing Ollama; parallel settings NOT applied"
    echo "[ollama]          quit the Ollama menu bar app manually and retry"
    return 0
  fi

  ollama serve >/tmp/mirofish-ollama.log 2>&1 &
  OLLAMA_PID=$!
  for _ in $(seq 1 20); do
    if lsof -nP -iTCP:"$OLLAMA_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
      break
    fi
    sleep 1
  done

  if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
    echo "[ollama] WARNING: Ollama failed to start; see /tmp/mirofish-ollama.log"
    OLLAMA_PID=""
  fi
}
