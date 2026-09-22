# MiroFish-Offline — oMLX backend & measured comparisons

**This fork (petadimensionlab derivative)** runs the MiroFish multi-agent social
simulation on a local **oMLX / MLX** inference server instead of Ollama, and splits
the models per pipeline stage (speed vs. quality).

**Language: English (primary).** &nbsp;·&nbsp; [日本語 / Japanese README](README.ja.md) &nbsp;·&nbsp; [Original README (pre-rewrite, archived)](README.original.md)

---

## TL;DR — what the measurements say

1. **oMLX is the better backend on the same host.** On an M1 Ultra, for the same model
   and prompt shape at n=8: **oMLX 120.6 tok/s vs Ollama 58–77 tok/s** (Ollama's own
   best-ever numbers, 208–305 tok/s, were measured on a *different* machine with a
   deliberately derived small-context model).
2. **Throughput saturates with concurrency.** gemma-4-e4b saturates at **n≈16**
   (164.5 tok/s on the real profile shape); adding slots only adds latency. 27B models
   saturate at ~23 tok/s and gain nothing from concurrency.
3. **oMLX parallelizes model families Ollama refuses.** Ollama forces `-np 1` for the
   `qwen35` architecture (serialized, ~48 tok/s flat). oMLX accepts the same family
   (`peak_active = 16`) and reaches ~75 tok/s — turning qwen from "3–7 hours per
   prepare" into "~31 minutes".
4. **The small model is not good enough for the quality-critical stages.** On the same
   ontology task, `gemma-4-e4b` produced *technical artifacts* (`Classifier_Model`,
   `Network_Topology`) instead of social actors, while `gemma-4-12B` and
   `Qwen3.8-27B` produced proper social entities — so the pipeline now uses a
   **per-stage model split**.
5. **Two regressions came with the oMLX switch, both fixed and re-verified** — a report
   crash from `content: null` (native tool calls) and insufficient client timeouts.
   The full pipeline now runs end-to-end with `verify_outputs.py` **18/18 PASS**.

---

## Experimental setup

| Item | Value |
|---|---|
| Inference host | Mac Studio — **M1 Ultra, 128 GB, 64 GPU cores** (tailnet) |
| Server | **oMLX 0.6.4** (MLX 0.32.0 / MLX-LM 0.31.3), OpenAI-compatible on `:8010` |
| Client | MacBook Pro (M3 Max) over tailnet; Neo4j 5.18 in Docker |
| Prompt shape | ~2,093-token prompt / 900-token output ("realistic profile shape") unless noted |
| Request params | `reasoning_effort: "none"`, `response_format: {"type":"json_object"}` |
| Metric | **aggregate = generated tokens ÷ wall time** (not `eval tok/s`) |

oMLX model ids are the repo name with `/` → `--`; IDs are used as served
(e.g. `mlx-community/gemma-4-e4b-it-4bit` → `gemma-4-e4b-it-4bit`).

---

## 1. oMLX vs Ollama — same host, same model, same shape

`gemma4:e4b` (Ollama, GGUF) vs `gemma-4-e4b-it-4bit` (oMLX, MLX), realistic shape, n=8:

| Backend | Configuration | aggregate |
|---|---|---|
| **oMLX** | defaults, ctx allocated per request | **120.6 tok/s** |
| Ollama | native API, `num_ctx=4096` forced | 58.0 tok/s (2 × HTTP 500) |
| Ollama | OpenAI API, auto `num_ctx=65536` | 77.0 tok/s |

→ **oMLX is 1.6–2.1× faster on identical hardware**, with no context tuning at all.

**Why Ollama needs babysitting and oMLX does not.** Ollama preallocates
`num_ctx × NUM_PARALLEL` and launches `llama-server -c <total> -np <N>`; the auto-chosen
`num_ctx=65536` alone collapses per-request speed, which is why this project historically
had to derive `-4k` / `-8k` / `-32k` model variants per stage. oMLX allocates context
per request, so **no derived models are needed**.

*Historical reference (different machine — M3 Max, tuned, derived `gemma4-4k`)*:
Ollama reached NP=16 → 140.8, NP=32 → 247.7, NP=64 → 305.4, NP=128 → 270.3 tok/s
(64-token generations), and 208.2 tok/s on a 24-req × 400-tok bench. Those numbers
require a tuned dedicated server (`OLLAMA_NUM_PARALLEL=64/128`) and are **not**
reproducible with a default server, which is what the same-host comparison above shows.

---

## 2. Concurrency scaling on oMLX

### gemma-4-e4b-it-4bit — short prompt (42 tok in / 512 tok out)

| n | 1 | 2 | 4 | **8** | 24 | 32 |
|---|---|---|---|---|---|---|
| aggregate tok/s | 76.0 | 115.6 | 146.4 | **174.1** | ~159 | 174.0 |
| per-request tok/s | 76.0 | 57.8 | 36.6 | 21.8 | ~6.6 | 5.4 |

### Realistic profile shape (~2,093 tok in / 900 tok out)

| Model | n=1 | n=8 | **n=16** | n=32 |
|---|---|---|---|---|
| **`gemma-4-e4b-it-4bit`** | 76.0\* | 120.6 | **164.5** | 165.0 |
| `Qwen3.5-9B-MLX-4bit` | 50.7 | 75.0 | 75.3 | — |
| `Qwen3.8-27B-oQ4e-mtp` | 20.0 | 22.9 | — | — |
| `Ternary-Bonsai-2-27B-MLX-4bit` | 21.3 | 23.8 | — | — |

\* single-stream figure for gemma was taken with a short prompt; only the n=8/n=16
columns are directly comparable across rows.

**Reading:** small models scale (~2.3× from n=1→8) and saturate around n≈16; 27B models
are bandwidth-bound and gain **nothing** from concurrency (1.1–1.15×). More slots beyond
saturation only increase latency (n=32 → p50 86 s vs n=16 → 57.5 s).

---

## 3. qwen: parallelizable on oMLX, serialized on Ollama

| Runtime | Model | n=1 | n=8 | n=16 | Parallel accepted? |
|---|---|---|---|---|---|
| Ollama 0.34.2 | `qwen3.5:9b` | ~48 tok/s | ~48 | ~48 | **no** — `-np 1`, `n_seq_max=1` |
| **oMLX 0.6.4** | **`Qwen3.5-9B-MLX-4bit`** | 50.7 | **75.0** | **75.3** | **yes** — `peak_active` 8 / 16 |

Ollama logs `model architecture does not currently support parallel requests;
architecture=qwen35` and serializes regardless of client concurrency. oMLX accepts the
same family (`config_model_type: qwen3_5`), but scales poorly — it saturates at ~75 tok/s
versus 164.5 for gemma-4-e4b. Practical effect on a 157-agent prepare (~141 k output
tokens): **3–7 h (Ollama) → ~31 min (oMLX)**.

> A 27B model on the same host is slower still (~23 tok/s) and, at 8 concurrent
> requests, **stalled oMLX entirely** — `active=8` for ~9 minutes with prompt/completion
> counters, memory and logs all frozen. It recovers instantly when the client
> disconnects. See §7.

---

## 4. Model compatibility (oMLX 0.6.4, measured)

| MLX repo | `config.json` `model_type` | Loads? | Realistic-shape aggregate |
|---|---|---|---|
| `mlx-community/gemma-4-e4b-it-4bit` | `gemma4` | **yes** | 76.0 → 120.6 → **164.5** tok/s (n=1/8/16) |
| `mlx-community/gemma-4-12B-it-qat-4bit` | `gemma4_unified` | **yes** | ontology/report verified (see §5) |
| `Jundot/Qwen3.8-27B-oQ4e-mtp` | `qwen3_5` (`mtp_compatible`) | **yes** | 20.0 → 22.9 tok/s (n=8; ~no scaling) |
| `mlx-community/Qwen3.5-9B-MLX-4bit` | `qwen3_5` | yes, **but unusable for JSON** | 50.7 → 75.0 tok/s |
| `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit` | `qwen3_5` (4-bit affine) | **yes** | 21.3 → 23.8 tok/s |
| `prism-ml/Ternary-Bonsai-2-27B-mlx-2bit` | `prism_hadamard_qwen35` | **NO** | load rejected (HTTP 409) |
| `inductiveML/Ternary-Bonsai-2-27B-mlx-lossless-1.75bpw` | `ternel_hadamard_qwen35` | **NO** | load rejected |
| `gemma4:e4b` (Ollama GGUF) | — | **NO** | oMLX needs MLX safetensors |

- **Bonsai 2 27B (the official ternary 2-bit build, released 2026-09-16) cannot load**:
  `Model type prism_hadamard_qwen35 not supported. Error: No module named
  'mlx_vlm.speculative.drafters.prism_hadamard_qwen35'`. Rebuilding with
  `--with-custom-kernel` does **not** fix it — that kernel targets the earlier Bonsai
  family; upstream this is open issues
  [#3768](https://github.com/jundot/omlx/issues/3768) /
  [#3727](https://github.com/jundot/omlx/issues/3727) and open PRs
  [#3734](https://github.com/jundot/omlx/pull/3734) /
  [#3782](https://github.com/jundot/omlx/pull/3782) (unmerged).
  Workaround: a conversion whose `config.json` says plain `qwen3_5` (loads, but slow).
- **Qwen3.5-9B is disqualified for JSON stages**: it emits a `Thinking Process:` trace
  into `content`, so the ontology stage fails with
  `Invalid JSON format from LLM` after burning the 4096-token budget.
- **Always check `config.json` `model_type` before downloading.** `gemma4` and
  `qwen3_5` load; `*_hadamard_*` ternary packs currently do not.

**Known limitation (not fatal):** oMLX logs
`response_format requested but grammar-constrained decoding is unavailable; output will
not be schema-enforced (falling back to prompt injection)` — JSON is prompt-guided, not
grammar-enforced, unless oMLX is built with `--with-grammar` (xgrammar).

---

## 5. Stage-level A/B: which model for the quality-critical stages?

The pipeline's `LLM_MODEL_NAME_LARGE` is used by **ontology / simulation_config /
report** (`ontology_generator.py`, `simulation_config_generator.py`,
`report_agent.py`); `LLM_MODEL_NAME` is used by the bulk stages (NER, profiles,
simulation). Both were measured on identical inputs.

### Ontology (same 45,326-char document, same requirement)

| Variant | Time | entity types | edge types | Generated entity types |
|---|---|---|---|---|
| `gemma-4-e4b` | **37.5 s** | 10 | 6 | ❌ `Model_Developer, Network_Topology, Classifier_Model, Generosity_Campaign` — **technical artifacts, not social actors** |
| `gemma-4-12B` | 103.1 s | 10 | 8 | ✅ `Conservationist, SustainabilityOfficer, PolicyMaker, MediaOutlet, NGO, TechDeveloper, Netizen` |
| `Qwen3.8-27B` | 218.4 s | 10 | **9** | ✅ `ConservationNGO, GovernmentAgency, MediaOutlet, SustainabilityCompany, SocialMediaPlatform, AcademicInstitution, EnvironmentalAdvocate` |

Edge types follow the same pattern: e4b produced `SIMULATES, TESTS_HYPOTHESIS,
IS_MODELED_BY` (ML-centric); 12B/27B produced `REPORTS_ON, ADVOCATES_FOR, REGULATES,
OPPOSES, FUNDS, ENGAGES_WITH` (social interactions).

> Note: the pre-existing production graph (generated earlier with `gemma4:12b`) also
> contained social actors, so the degradation is **specific to the small e4b**, not to
> the gemma family.

### Report (same simulation, same requirement)

| Variant | Time | Sections | Total chars | Tool calls | Text quality (sampled) |
|---|---|---|---|---|---|
| `gemma-4-e4b` | **99.2 s** | 3 | 7,607 | 8 | ❌ dumps graph facts as block-quotes, almost no synthesis |
| `gemma-4-12B` | 344.9 s | 4 | 18,476 | 12 | ✅ analytical prose (thesis → mechanisms → implications) |
| `Qwen3.8-27B` | **1025.8 s** | 4 | **38,529** | 10 | ✅✅ structural analysis (co-authorship chains, shared venues, unified field) |

**Conclusion:** the quality-critical stages need **≥12B**. `Qwen3.8-27B` is the richest
(~5× the text of e4b) at ~10× the wall time; `gemma-4-12B` is the cheaper middle ground
(3.5× e4b, 2.4× the text).

---

## 6. End-to-end run with the final configuration

`sim_b18e332db6b7` — 157 agents, full pipeline on the M1 Ultra (new config, see §8):

| Stage | Model | Measured | Notes |
|---|---|---|---|
| ontology (production path) | 27B | **3 m 40 s** | 10 entity / 8 edge types |
| profiles ×157 (`--parallel-profiles 16`) | e4b | **12.5 min** | 12.03 profiles/min, `active=16`/`waiting=8` |
| simulation_config (11 batches) | **27B** (C=1) | **26.9 min** | ~2.1 min/batch, ~25 tok/s aggregate |
| simulation (1 round) | e4b | 4 s | initial posts only (`Published 7 initial posts`); LLM-driven actions need round ≥ 2 |
| report | **27B** | **~19 min** | 4 sections, 35,026 chars |
| `scripts/verify_outputs.py` | — | **18/18 PASS** | 0 FAIL |
| **total** | | **≈ 62 min** | |

Bulk-stage throughput matches the micro-benchmarks (12.03 profiles/min ≈ the 12.1–12.3
measured at n=16), i.e. **in-situ aggregate ≈ 88 % of the micro-benchmark** — budget the
remaining ~12 % for shape variance, prefill mixing and retries.

⚠️ An earlier attempt with `CONFIG_BATCH_PARALLEL=8` **stalled** and wasted ~27 min
(see §7) — the numbers above are for `CONFIG_BATCH_PARALLEL=1`.

---

## 7. Regressions and failures found with oMLX (all fixed)

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `report-generate` FAILED for **every** model: `expected string or bytes-like object, got 'NoneType'` (regression vs. the Ollama-era report) | oMLX answers a tool-protocol prompt with **native `tool_calls` and `content: null`** (`finish_reason: tool_calls`); `llm_client.chat()` assumed a string | `llm_client.chat()` now converts native `tool_calls` back into `<tool_call>{"name":…,"parameters":…}</tool_call>` text, which `report_agent._parse_tool_calls()` expects. Re-verified: all 3 models COMPLETED |
| 2 | ontology / report could time out on 27B | those clients were hardcoded to a **300 s** timeout (ontology measured 218 s; report single call max ~153 s) | new `LLM_TIMEOUT_LARGE` (default 1800) honoured by `ontology_generator.py` and `report_agent.py`; `LLM_TIMEOUT_CONFIG=1800` |
| 3 | `simulation_config` **hung**: 8 requests issued, then `active=8` for ~9 min with prompt/completion/memory/logs all frozen | **27B × 8 parallel** stalls oMLX (recovers instantly once the client disconnects) | `CONFIG_BATCH_PARALLEL=1`. 27B gains nothing from concurrency anyway (§2) |

Lesson recorded: on an OpenAI-compatible server, **`content` can be `null`** — never
assume it is a string.

---

## 8. Production configuration

```bash
# --- oMLX (OpenAI-compatible) ---
LLM_API_KEY=<oMLX API key>
LLM_BASE_URL=http://<omlx-host>:8010/v1
OPENAI_API_BASE_URL=http://<omlx-host>:8010/v1

LLM_MODEL_NAME=gemma-4-e4b-it-4bit           # bulk: NER / profiles / simulation
LLM_MODEL_NAME_LARGE=Qwen3.8-27B-oQ4e-mtp    # quality: ontology / config / report
LLM_TIMEOUT_LARGE=1800                       # 27B per-call latency
LLM_TIMEOUT_CONFIG=1800
LLM_TIMEOUT=600
LLM_MAX_ATTEMPTS=5
LLM_REASONING_EFFORT=none

CONFIG_BATCH_PARALLEL=1     # 27B: >1 stalls oMLX (§7)
GRAPH_BUILD_PARALLEL=4
OASIS_SEMAPHORE=16

# --- embeddings stay on Ollama (oMLX ships no embedding model) ---
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://<ollama-host>:11434
EMBEDDING_API_STYLE=ollama
EMBEDDING_API_KEY=ollama
```

oMLX side: `scheduler.max_concurrent_requests = 32` (headroom; latency only grows above
n≈16), `server.host = 0.0.0.0`, admin API key set via `POST /admin/api/setup-api-key`
(until it is set, the inference API is unauthenticated).

`scripts/mirofish_cli.py sim-prepare --parallel-profiles 16` is the recommended bulk
concurrency.

### Adding a model / checking a repo

```bash
# download straight through the oMLX admin API (no SSH needed)
curl -X POST -H "Authorization: Bearer $OMLX_API_KEY" -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/gemma-4-e4b-it-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download      # 5.18 GB, ~100 s

# check compatibility before downloading
curl -s https://huggingface.co/<repo>/raw/main/config.json | grep model_type
```

---

## Quick start

```bash
cp .env.example .env     # then fill in the oMLX host/key (see §8)
docker compose up -d neo4j        # MiroFish's own Neo4j (ports 7475/7688)
npm run backend                   # cd backend && uv run python run.py

# drive the pipeline from the terminal
python scripts/mirofish_cli.py ontology   --requirement "..." --file doc.pdf
python scripts/mirofish_cli.py build      --project-id proj_xxxx
python scripts/mirofish_cli.py sim-create --project-id proj_xxxx
python scripts/mirofish_cli.py sim-prepare --simulation-id sim_xxxx --parallel-profiles 16
python scripts/mirofish_cli.py sim-start  --simulation-id sim_xxxx --platform parallel --max-rounds 1
python scripts/mirofish_cli.py sim-close  --simulation-id sim_xxxx     # required before report
python scripts/mirofish_cli.py report-generate --simulation-id sim_xxxx

# verification (run before claiming success)
python scripts/verify_outputs.py --simulation-id sim_xxxx
```

Notes: the simulation enters *wait mode* after its rounds, so `sim-close` is required
before report generation; and `--max-rounds 1` only publishes the configured initial
posts (LLM-driven agent behaviour starts at round ≥ 2).

---

## Operating rules

Operational guardrails, known failure modes and required settings live in
[`AGENTS.md`](AGENTS.md) (R13 documents the oMLX backend, measured concurrency, the
per-stage model split and the mandatory `CONFIG_BATCH_PARALLEL=1`). Helper scripts
(`supervise.sh`, `monitor.sh`, `verify_outputs.py`) and the
`mirofish-output-verification` skill support long, failure-prone runs.

---

## License

AGPL-3.0 — same as the original MiroFish project. See [LICENSE](./LICENSE).

## Credits & Attribution

This is a modified fork of [MiroFish](https://github.com/666ghj/MiroFish) by
[666ghj](https://github.com/666ghj), originally supported by
[Shanda Group](https://www.shanda.com/). The simulation engine is powered by
[OASIS](https://github.com/camel-ai/oasis) from the CAMEL-AI team.

Modifications in this derivative:
- Backend switched from Ollama to **oMLX (MLX)** on Apple Silicon, with a measured
  per-stage model split (bulk = `gemma-4-e4b`, quality = `Qwen3.8-27B`)
- Report-generation regression on OpenAI-compatible servers fixed (`content: null`)
- Configurable LARGE-stage timeouts; model-compatibility and concurrency documentation
- Zep Cloud → local Neo4j CE 5.18; UI translated to English; rebranded MiroFish-Offline
