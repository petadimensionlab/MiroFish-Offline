#!/usr/bin/env bash
# TensorFold: OpenAI-compatible LLM server with request-level parallelism
# (--parallel N streams decoded together on a single port). Runs on this Mac's
# Metal GPU on dedicated ports (11510 bulk / 11511 large) so it never clashes
# with Ollama.app (11434) or the tuned Ollama (11500).
#
# Usage:
#   scripts/start_tensorfold.sh              # start both servers
#   TF_BULK_MODEL=... TF_LARGE_MODEL=none scripts/start_tensorfold.sh
#   scripts/start_tensorfold.sh --stop
#
# Environment:
#   TF_BULK_MODEL    default: mlx-community/Qwen3.8-27B-4bit (TENSOR UNITS-free:
#                    native 1.1.4 serves the qwen3_5 dense family; the 26B MoE is
#                    for the 0.6.6 lane engine only. Set TF_NATIVE_BIN=... or
#                    TF_BULK_MODEL=... to fall back to the MoE.)
#   TF_BULK_NAME     served name for bulk (NER/profiles/simulation): mirofish
#   TF_BULK_PORT     :11510     TF_BULK_CTX: 16384   TF_BULK_PARALLEL: 8
#   TF_LARGE_MODEL   default: mlx-community/Qwen3.8-27B-4bit (ontology/config/report)
#                    'none' -> serve only the bulk model
#   TF_LARGE_NAME    : mirofish-27b   TF_LARGE_PORT: 11511  TF_LARGE_CTX: 8192  TF_LARGE_PARALLEL: 16
#   TF_REASONING_EFFORT  large model's thinking default: low (bulk is always --no-thinking)
#   TF_API_KEY_FILE  bearer key file (minted on first start: ~/.tensorfold/api_key)
#
# M1 Ultra measurements (2026-10-25, this machine):
#   - native binary 1.1.4 refuses everything here ("GemmaNeedsTensorUnits" /
#     "UnqualifiedQwenShape"): it needs M5-class Tensor Units. Use 0.6.6.
#   - 27B low-thinking: agg ~15 tok/s n=1, 64 @4, 81 @8, 84 @16, 91 @32, 94 @64
#     (saturates ~n=16; beyond that only latency grows). --decode-share 100
#     doubles n=8 throughput vs the default (~40).
#   - 27B checkpoint must be mlx-community/Qwen3.8-27B-4bit (or the tested
#     TensorFold/Qwen3.8-27B-MLX-4bit); lmstudio's Qwen3.5-9B is refused
#     (UnqualifiedQwenShape).
#   - 26B MoE bulk (no thinking): ~13 tok/s n=1, ~72 tok/s n>=4.
#   - Gemini/Gemma non-MoE (e4b, 12B) and DeepSeek are NOT servable here.
set -euo pipefail

TF_BIN="${TF_BIN:-/opt/homebrew/opt/tensorfold@0.6/bin/tensorfold}"
TF_NATIVE="${TF_NATIVE_BIN:-tensorfold}"   # native 1.1.4 (brew install ashhart/tensorfold/tensorfold)
KEYFILE="${TF_API_KEY_FILE:-$HOME/.tensorfold/api_key}"
mkdir -p "$(dirname "$KEYFILE")"
if [ ! -s "$KEYFILE" ]; then
  (umask 077; openssl rand -hex 24 > "$KEYFILE")
fi
KEY="$(cat "$KEYFILE")"

stop_one() {
  local port="$1" pidfile="$2"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")"   # SIGTERM: keeps prompt states
    echo "[tensorfold] stopped :$port (pid $(cat "$pidfile"))"
  else
    local pids; pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then kill $pids; echo "[tensorfold] stopped :$port ($pids)"; else echo "[tensorfold] :$port not running"; fi
  fi
  rm -f "$pidfile"
}

if [ "${1:-}" = "--stop" ]; then
  stop_one "${TF_BULK_PORT:-11510}" /tmp/mirofish-tensorfold.pid
  stop_one "${TF_LARGE_PORT:-11511}" /tmp/mirofish-tensorfold-large.pid
  # also clean up any manual test servers started during benchmarking
  for pf in /tmp/tf_27b_64.pid /tmp/tf_fn_serve.pid /tmp/tf_glm.pid /tmp/tf_27b_serve.pid /tmp/tf_9b.pid /tmp/tf_26b_8k.pid; do
    [ -f "$pf" ] && kill "$(cat "$pf")" 2>/dev/null && rm -f "$pf"
  done
  exit 0
fi

[ -x "$TF_BIN" ] || { echo "[tensorfold] ERROR: $TF_BIN not found. brew install ashhart/tensorfold/tensorfold@0.6" >&2; exit 1; }

start_one() {
  local model="$1" name="$2" port="$3" ctx="$4" parallel="$5" effort="$6" pidfile="$7" log="$8"
  local extra tfbin="$TF_BIN"
  shift 8
  extra="$*"
  while [ $# -gt 0 ]; do
    case "$1" in
      --tfbin) tfbin="$2"; shift 2 ;;
      *) extra+=("$1"); shift ;;
    esac
  done
  [ "$model" = "none" ] && { echo "[tensorfold] skipping $name (:$port)"; return 0; }
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[tensorfold] :$port already listening; reuse"
    return 0
  fi
  if ! "$tfbin" models 2>/dev/null | grep -q "$model"; then
    echo "[tensorfold] pulling $model..."
    "$tfbin" pull "$model"
  fi
  local think="--no-thinking"
  [ -n "$effort" ] && [ "$effort" != "none" ] && think="--reasoning-effort $effort"
  echo "[tensorfold] serving $model as '$name' :$port parallel=$parallel ctx=$ctx"
  # shellcheck disable=SC2086
  nohup "$tfbin" serve "$model" \
    --name "$name" --host 127.0.0.1 --port "$port" \
    --context "$ctx" --parallel "$parallel" \
    --temperature 0.7 $think $extra \
    --api-key-file "$KEYFILE" >"$log" 2>&1 &
  local pid=$!
  echo "$pid" > "$pidfile"
  local deadline=$(( SECONDS + 600 ))
  while [ $SECONDS -lt $deadline ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[tensorfold] ERROR: $name exited early:"; tail -15 "$log"; rm -f "$pidfile"; return 1
    fi
    if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$port/health" 2>/dev/null || true)" = "200" ]; then
      grep -m1 "concurrency:" "$log" 2>/dev/null || true
      echo "[tensorfold] $name up :$port (pid $pid, log $log)"
      echo "[tensorfold]   base_url=http://127.0.0.1:$port/v1 key=$KEY model=$name"
      return 0
    fi
    sleep 2
  done
  echo "[tensorfold] WARNING: $name not healthy in 10min; watch: tail -f $log"
  return 1
}

TF_NATIVE="${TF_NATIVE_BIN:-/opt/homebrew/bin/tensorfold}"

# Bulk: 26B MoE (gemma-4-26b-a4b-it-4bit) on the 0.6.6 lane engine: loads ~21s,
# ~14 GiB resident, ~72 tok/s at n>=4 no-thinking. Flash Next / e4b / dense
# Gemma / Qwen3.5-9B are refused or swap-thrash here — the user vetoed
# Flash Next. The llama.cpp/Ollama path keeps its e4b if ever needed.
start_one "${TF_BULK_MODEL:-mlx-community/gemma-4-26b-a4b-it-4bit}" \
  "${TF_BULK_NAME:-mirofish}" "${TF_BULK_PORT:-11510}" \
  "${TF_BULK_CTX:-16384}" "${TF_BULK_PARALLEL:-8}" "" \
  /tmp/mirofish-tensorfold.pid /tmp/mirofish-tensorfold.log

# Large: Qwen3.8-27B (tested checkpoint TensorFold/Qwen3.8-27B-MLX-4bit) on the
# 0.6.6 lane engine with --reasoning-effort low + --decode-share 100.
# native 1.1.4 REFUSES it here (MCPFTV2KVDescriptorMismatch — no Tensor Units
# on M1 Ultra); the old "native loads it in ~12s" note was WRONG, and the
# "ms by rows" line only exists on 0.6.6. Measured (2026-10-25, 0.6.6):
# n=1 ~52 tok/s, n=8 ~81, n=16 ~84, n=32 ~91, n=64 ~94 (saturates ~n=16).
# --no-thinking on bulk + reasoning_effort "low" per request keep the 27B's
# thinking under control; max_tokens must be >=1024 or content comes back empty.
start_one "${TF_LARGE_MODEL:-TensorFold/Qwen3.8-27B-MLX-4bit}" \
  "${TF_LARGE_NAME:-mirofish-27b}" "${TF_LARGE_PORT:-11511}" \
  "${TF_LARGE_CTX:-8192}" "${TF_LARGE_PARALLEL:-16}" "${TF_REASONING_EFFORT:-low}" \
  /tmp/mirofish-tensorfold-large.pid /tmp/mirofish-tensorfold-large.log \
  --decode-share "${TF_DECODE_SHARE:-100}"
