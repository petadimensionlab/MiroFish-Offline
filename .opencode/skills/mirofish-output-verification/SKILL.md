---
name: mirofish-output-verification
description: Fixed checklist to verify MiroFish pipeline outputs (profiles, simulation config, simulation run, report) for validity, cross-consistency, and speed. Use whenever a simulation or pipeline stage completes, before claiming success, or when asked to "verify"/"validate" outputs, or when output looks wrong (empty, short, duplicate, corrupt). Runs scripts/verify_outputs.py.
---

# MiroFish Output Verification

## Simple policy (follow this above all rules)

1. Model: **gemma4:12b first** (accuracy); e4b only for speed.
2. Context: **small by default** (bulk = 8192). Only ontology / config / **report(PDF)**
   use larger (32768). Never one large ctx for everything.
3. **Run in parallel** (verify `-np`/`-c` actually take effect with the small ctx).
4. **Be recoverable** (resume from artifacts, atomic writes, one server + one backend).
5. Prefer this policy over adding more rules.

**Characteristic (agent defect):** agents often do NOT apply this simple policy —
they run one big ctx, neutralize parallelism, lose work on failure, or run blind for
hours. Before acting, ask: *does this satisfy (small ctx) + (parallel) + (recoverable)?*

**Characteristic (agent defect): does NOT notice problems — leaves them unattended.**
Observed: generated ~1,810 tokens/profile while only ~850 were saved (~50% wasted on
thinking); runner was `-np 4` though NP=16 was declared; latency inconsistent with the
token rate. All were ignored until pointed out.
**Required:** on every run compare *expected vs observed* — `n_gen`/`completion_tokens`
vs saved output, declared NP vs runner `-np`, observed latency vs `tokens/rate` — and
investigate any mismatch **before** continuing. A number that looks off is a defect.

**Characteristic (agent defect): fails at the SUB-PROCESS level (sloppy fundamentals).**
The failures are not from missing strategy but from carelessly executing the basic
sub-processes: context sizing, port hygiene, atomic writes, measuring the right
metric, verifying runner args, noticing anomalies. Do each sub-process rigorously,
verify it (expected vs observed), and write it down — see
`docs/process-trace-2026-09-14.md`. An unwritten sub-process is repeated wrong.


Never claim a stage "works" from process exit alone. Run this checklist and show
the evidence (counts, FAIL list, speed numbers). One script does the structural
checks; speed/quality require reading logs too.

## Run it

```bash
python scripts/verify_outputs.py --simulation-id sim_xxxx \
  [--project-id proj_xxxx] [--report-dir backend/uploads/reports/report_xxxx] [--json]
```
Exit code 0 = no FAIL. `[WARN]` items need judgement; `[FAIL]` must be explained.

## Checklist

### A. Artifacts present
- A1 `reddit_profiles.json` exists and parses.
- A2 `simulation_config.json` exists and parses.
- A3 simulation DB (`<platform>_simulation.db`) exists and has rows.
- A4 no `.corrupt.bak` left behind (means a prior non-atomic write was interrupted).

### B. Profile validity
- B1 valid JSON, N entries (compare to expected entity count from the graph).
- B2 `user_id` unique.
- B3 `user_id` **contiguous** `0..N-1` (gaps = lost/regenerated items after a crash).
- B4 required fields non-empty: `user_id, username, name, bio, persona, age,
  gender, mbti, country, profession, interested_topics`.
- B5 `name` unique (duplicate names → NER/graph duplication).
- B6 persona length distribution sane (median within ~100–20000 chars; report
  min/median/max — a tiny median means truncation/fallback).

### C. Config validity + cross-consistency
- C1 `agent_configs` parses; count == profile count.
- C2 `time_config`, `event_config`, `llm_model` present and non-empty.
- C3 **`{agent_id}` == `{profile user_id}`** (exact set equality).
- C4 (when available) config's `simulation_id`/`project_id`/`graph_id` match the run.

### D. Quality signals (from the backend log)
- D1 `LLM output truncated` count low relative to N.
- D2 `using rule-based generation` count ~0 (mass fallback = the LLM path failed).
- D3 `Request timed out` count 0 (timeouts silently drop work).
- D4 (when available) simulation DB rows > 0; posts/comments coherent.

### E. Speed (must be measured, not guessed)
- E1 per-request generation rate: `grep 'eval time' /tmp/mirofish-ollama.log`
  → tokens/s per request.
- E2 **concurrency actually running** = count of `processing task` slots currently
  active (do not assume C; verify).
- E3 aggregate ≈ E1 × E2 (tok/s). Sanity-check against observed completion rate.
- E4 observed rate = artifacts completed ÷ wall time (e.g. profiles/min).
  **If E3 ≫ E4, something else dominates** (prefill, queueing, retries, swap) — stop
  and find it before reporting.
- E5 compare to expected: `tokens_per_unit × N / aggregate` → ETA; report basis.

## Terminology (define before using)
- **NP** = `OLLAMA_NUM_PARALLEL` (server slots; runner `-c num_ctx×NP -np NP`).
- **C** = client concurrency (`--parallel-profiles`, `CONFIG_BATCH_PARALLEL`, …).
- **aggregate** = generated tokens ÷ wall time (machine ceiling).
- **per-request rate** ≈ aggregate ÷ C.

## Rules
- Always verify the **real runner args** (`-np`, `-c`) and the model blob in use.
- Always reconcile **declared concurrency vs active slots vs observed rate**; a
  mismatch (e.g. NP=16 but only 2 slots busy, or eval-rate×C ≫ observed) is a bug
  to diagnose, not to ignore.
- Report FAILs with the measured value; never round-trip to "looks fine".
- Clean up `.corrupt.bak` only after salvaging and noting the loss in the log.
- **When stuck, consult once — do not flail.** If the approach isn't working and you
  cannot produce a *better* idea, stop and ask: current state / options / recommendation.
  Blind retries turn one failure into many; a short consultation is cheap.

## Agent anti-patterns (observed — do not repeat)

1. Started long runs without measuring the **end-to-end completion rate**; judged by
   `eval tok/s` only. A job at 0.3 items/min (~8 h) looked "fine".
2. Ignored the "small ctx for bulk stages" rule; ran one large `num_ctx` for everything
   → `-c = num_ctx × NP` exploded and parallel throughput collapsed (15 → 1.5 tok/s).
3. Misread `n_gen`/`n_tokens` as output length (they are **prompt + output**).
4. Restart churn corrupted progress (93 → 13 profiles lost).
5. Wrote project-specific rules into **global** config (scope violation).
6. Invested in a known-bad approach (N `ollama serve` + round-robin proxy on one GPU).
7. Left config intent ≠ runner reality (`-c`/`-np` unchecked).
8. Ran broad sweeps (NP=2..128) instead of one targeted 2–3 min measurement.
9. Reporting swung between spam and too-sparse.

## System observations (design around these)

- Ollama **ignores `extra_body.options.num_ctx`** → the effective context is the
  **derived model's `num_ctx`**; split derived models per stage.
- Runner = `llama-server -c <num_ctx × NP> -np NP`; large `-c` collapses per-request
  throughput under concurrency.
- Ollama **may silently lower NP** (asked 16, got `-np 4`) — always read runner args.
- Some architectures cannot parallelize (qwen35 → forced `-np 1`).
- Ollama.app respawns and steals port 11434 with a bad `-np` (double LISTEN).
  **Fix: run the tuned server on a dedicated port** (`OLLAMA_HOST=127.0.0.1:11500`)
  and point `LLM_BASE_URL` / `OPENAI_API_BASE_URL` / `EMBEDDING_BASE_URL` at it
  (`EMBEDDING_API_STYLE=ollama`); `scripts/lib_ollama.sh` defaults to this.
- Real profile length ~850 tokens (`n_gen` includes the prompt).
- Two backends overwrite the same progress file — keep exactly one.
- The backend runs prepare as a **background task that survives the CLI**; restarting
  the CLI starts a *second* prepare → two config generations race on
  `simulation_config.partial.json` (observed batch count 5 → 3). Verify
  `grep -c "Starting intelligent simulation configuration generation"` == 1.
