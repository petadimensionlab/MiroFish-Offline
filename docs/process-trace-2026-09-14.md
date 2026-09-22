# Process trace — qwen3.5:9b → gemma4:12b (2026-09-13/14)

One-time, end-to-end record of the **sub-processes** (素過程) that were executed:
what was done, what was observed, what was decided, and what was left open. The
point: failures happened at the sub-process level, and sloppy sub-processes are the
root cause — so they are written down explicitly.

Legend: **did → observed → decided**.

---

## S1. Model inspection
- **did**: `ollama show qwen3.5:9b`; parsed GGUF metadata (block_count, head_count_kv,
  key_length, full_attention_interval); sent probe requests.
- **observed**: architecture `qwen35`, hybrid attention (`full_attention_interval=4`,
  KV heads only every 4th layer), `context_length=262144`, **thinking** capability;
  KV ≈ `2 × 32 × 256 × 2 = 32 KiB/token`.
- **decided**: pin the effective context via a **derived model** (verified
  `extra_body.options.num_ctx` is ignored).

## S2. Disabling "thinking"
- **did**: probed `think:false`, `options.think:false`, `reasoning_effort:"none"` on
  the OpenAI-compatible endpoint.
- **observed**: only `reasoning_effort:"none"` disables it; others leave `content`
  empty (`finish_reason=length`, all tokens in the separate `reasoning` field).
- **decided**: add `reasoning_kwargs()` in `llm_client.py` and to **every** camel
  client (`run_*_simulation.py` via `model_config_dict`); `.env LLM_REASONING_EFFORT`.

## S3. Derived models (context pinning)
- **did**: `ollama create` of `qwen3.5-8k/32k`, `gemma4-32k`, `gemma4-12b-8k/32k`
  from `--modelfile` + `PARAMETER num_ctx …`.
- **observed**: the derived model's `num_ctx` is the **only** effective per-slot
  context (extra_body ignored; verified via `ollama ps`).

## S4. Ollama server / port hygiene
- **did**: started `ollama serve`, checked `ollama ps` + runner args.
- **observed**: the macOS **Ollama.app respawns** and steals port 11434 with
  `-np 1/4`; double LISTEN; `run_cli.sh`'s restart also fought it.
- **decided**: **dedicated port** (`OLLAMA_HOST=127.0.0.1:11500`) + point
  `LLM_BASE_URL`/`OPENAI_API_BASE_URL`/`EMBEDDING_BASE_URL` at it; `lib_ollama.sh`
  honors `OLLAMA_HOST`.

## S5. Context sizing per stage
- **did**: measured single-request rate at `num_ctx` 8192 vs 32768; ran with NP.
- **observed**: 8192 single ≈ **15 tok/s**; 32768 × NP16 ≈ **~1.5 tok/s**; runner is
  `-c <num_ctx × NP>`.
- **decided**: **small ctx for bulk** (NER/profiles/simulation) and large only for
  ontology/config/report → `LLM_MODEL_NAME` (small) / `LLM_MODEL_NAME_LARGE` (large),
  selected via `LLMClient(use_large=…)`.

## S6. NER extraction quality
- **did**: ran NER on real chunks with the ontology prompt.
- **observed**: strict "ontology types only / be precise" → **0 entities** even for
  reference lists; recall-priority prompt → **178 entities**.
- **decided**: rewrite `ner_extractor._SYSTEM_PROMPT` for recall (authors count).

## S7. Profile generation resilience
- **did**: added resume (reuse by `user_id`); audited writes.
- **observed**: killing backend mid-write **corrupted** `reddit_profiles.json`
  (93 → 13 profiles lost); two backends overwrote the same file (89 → 8).
- **decided**: atomic write (temp + `os.replace` + `fsync`); keep exactly **one**
  backend; salvage corrupt files via `json.JSONDecoder().raw_decode` loop.

## S8. Concurrency experiments
- **did**: NP sweeps (2..128) on short prompts; N-process `ollama serve` + round-robin
  proxy.
- **observed**: isolated optimum NP=64 (152 tok/s), but **real workload** (long
  graph-context + ~850-token output) shows contention (C=8 ≈ 14 tok/s, C=12 ≈ 6.7);
  multi-process proxy +20% only (single GPU) and swap thrash at N=8.
- **decided**: no sweeps; moderate C; **small ctx**; verify runner args; no proxy.

## S9. Speed measurement correctness
- **did**: compared per-request `eval tok/s`, `n_gen`, HTTP latency, completion count.
- **observed**: `eval tok/s` **≠** end-to-end rate; `n_gen`/`n_tokens` = **prompt +
  output**, not output length; observed latency inconsistent with rate.
- **decided**: always measure the **completion rate** (items/min) and compare
  expected vs observed.

## S10. Per-stage models + dedicated port (final config)
- **did**: implemented `LLM_MODEL_NAME_LARGE` (ontology/config/report), small default
  for bulk; dedicated port.
- **observed**: runner confirmed `-c 131072 -np 16` with the 8k derived model.
- **decided**: adopt as the standard config.

---

## Resolved during the trace

- **Thinking-token waste (RESOLVED).** gemma4:12b emits `reasoning` by default.
  Measured raw response fields:

  | request | completion_tokens | content_chars | reasoning_chars | finish |
  |---|---|---|---|---|
  | default | 400 (hit cap) | 127 | **1525** | length |
  | `reasoning_effort:"none"` | **40** | 156 | 0 | stop |

  → default wastes ~90% on thinking. **Fix: `LLM_REASONING_EFFORT=none`** (was removed
  when switching from qwen to gemma; re-enabled). Effect on the live run: `n_gen`
  dropped from ~1,810 to ~445.

## Completion (2026-09-14)

End-to-end run finished with the final config (12b, small ctx bulk / large ctx
report, dedicated port, NP=16, thinking off):

- project `proj_827ece137595`, simulation `sim_cbb1e1df9e74`, report
  `report_bf58115b0fa8`.
- **`scripts/verify_outputs.py`: 19/19 PASS** — 157 profiles (unique, contiguous,
  all fields), config 157 agents with ids == profile ids, no truncation/parse/timeout,
  `full_report.md` (174 lines) written.

Stage timings (12b): profiles 157 done; config 11 batches; simulation 1 round
(`reddit_completed=True`).

### Observation: 1 round produces almost no agent activity (expected, but know it)
- `reddit_simulation.db`: `trace` = 157 `sign_up` + 5 `create_post`; `comment=like=follow=0`.
  The 5 posts are the `event_config` initial posts (manual), not agent-owned.
- Cause: with `--max-rounds 1`, round 0 is `simulated_hour=0` (an **off-peak** hour),
  so `_get_active_agents_for_round` activates few/no agents → no agent actions.
- **The previous successful run (`sim_39b482b9c3c9`) shows the same pattern**
  (`sign_up` + 4 `create_post`, no comments/likes), so this is **existing behavior**,
  not a regression. For meaningful interaction, run more rounds (or start at a peak
  hour).

### Anomaly found at the end (recorded)
- The simulation (platform `parallel`) kept the env in **wait mode**, so the CLI
  polled `sim-status` to its **20-step cap** and exited (`resume did not finish after
  20 steps`); the report was not auto-generated. Fix in practice: `sim-close` then
  `report-generate`. See README failure category A.4c.

## Open / unresolved (must verify, do not leave unattended)

- Automate `sim-close` + `report-generate` at the end of `resume`/`pipeline` so the
  final report is produced without manual intervention.

## Why this trace exists

Every failure in this project was a **sub-process failure** (context sizing, port
hygiene, write atomicity, measuring the wrong metric, not noticing anomalies), not
a lack of a fancy rule. If a sub-process is not done rigorously and written down,
it is repeated wrong.
