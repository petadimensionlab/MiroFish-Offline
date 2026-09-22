**English** | [日本語](README.ja.md)

<div align="center">

<img src="./static/image/mirofish-offline-banner.png" alt="MiroFish Offline" width="100%"/>

# MiroFish-Offline

**Fully local fork of [MiroFish](https://github.com/666ghj/MiroFish) — no cloud APIs required. English UI.**

*A multi-agent swarm intelligence engine that simulates public opinion, market sentiment, and social dynamics. Entirely on your hardware.*

[![GitHub Stars](https://img.shields.io/github/stars/nikmcfly/MiroFish-Offline?style=flat-square&color=DAA520)](https://github.com/nikmcfly/MiroFish-Offline/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/nikmcfly/MiroFish-Offline?style=flat-square)](https://github.com/nikmcfly/MiroFish-Offline/network)
[![Docker](https://img.shields.io/badge/Docker-Build-2496ED?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square)](./LICENSE)

</div>

## What is this?

MiroFish is a multi-agent simulation engine: upload any document (press release, policy draft, financial report), and it generates hundreds of AI agents with unique personalities that simulate the public reaction on social media. Posts, arguments, opinion shifts — hour by hour.

The [original MiroFish](https://github.com/666ghj/MiroFish) was built for the Chinese market (Chinese UI, Zep Cloud for knowledge graphs, DashScope API). This fork makes it **fully local and fully English**:

| Original MiroFish | MiroFish-Offline |
|---|---|
| Chinese UI | **English UI** (1,000+ strings translated) |
| Zep Cloud (graph memory) | **Neo4j Community Edition 5.15** |
| DashScope / OpenAI API (LLM) | **Ollama** (qwen2.5, llama3, etc.) |
| Zep Cloud embeddings | **nomic-embed-text** via Ollama |
| Cloud API keys required | **Zero cloud dependencies** |

## Workflow

1. **Graph Build** — Extracts entities (people, companies, events) and relationships from your document. Builds a knowledge graph with individual and group memory via Neo4j.
2. **Env Setup** — Generates hundreds of agent personas, each with unique personality, opinion bias, reaction speed, influence level, and memory of past events.
3. **Simulation** — Agents interact on simulated social platforms: posting, replying, arguing, shifting opinions. The system tracks sentiment evolution, topic propagation, and influence dynamics in real time.
4. **Report** — A ReportAgent analyzes the post-simulation environment, interviews a focus group of agents, searches the knowledge graph for evidence, and generates a structured analysis.
5. **Interaction** — Chat with any agent from the simulated world. Ask them why they posted what they posted. Full memory and personality persists.

## Screenshot

<div align="center">
<img src="./static/image/mirofish-offline-screenshot.jpg" alt="MiroFish Offline — English UI" width="100%"/>
</div>

## Quick Start

### Prerequisites

- Docker & Docker Compose (recommended), **or**
- Python 3.11+, Node.js 18+, Neo4j 5.15+, Ollama

### Option A: Docker (easiest)

```bash
git clone https://github.com/nikmcfly/MiroFish-Offline.git
cd MiroFish-Offline
cp .env.example .env

# Start all services (Neo4j, Ollama, MiroFish)
docker compose up -d

# Pull the required models into Ollama
docker exec mirofish-ollama ollama pull qwen2.5:32b
docker exec mirofish-ollama ollama pull nomic-embed-text
```

Open `http://localhost:3000` — that's it.

### Option B: Manual

**1. Start Neo4j**

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/mirofish \
  neo4j:5.15-community
```

**2. Start Ollama & pull models**

```bash
ollama serve &
ollama pull qwen2.5:32b      # LLM (or qwen2.5:14b for less VRAM)
ollama pull nomic-embed-text  # Embeddings (768d)
```

**3. Configure & run backend**

```bash
cp .env.example .env
# Edit .env if your Neo4j/Ollama are on non-default ports

cd backend
pip install -r requirements.txt
python run.py
```

**4. Run frontend**

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## Configuration

All settings are in `.env` (copy from `.env.example`):

```bash
# LLM — oMLX (MLX inference server for Apple Silicon, OpenAI-compatible)
LLM_API_KEY=<oMLX API key>
LLM_BASE_URL=http://<omlx-host>:8010/v1
LLM_MODEL_NAME=gemma-4-e4b-it-4bit           # bulk: NER / profiles / simulation
LLM_MODEL_NAME_LARGE=Qwen3.8-27B-oQ4e-mtp    # quality: ontology / config / report
LLM_TIMEOUT_LARGE=1800                       # the 27B LARGE stages are ~10x slower

# Neo4j — MiroFish's own container (mirofish-neo4j)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mirofish
NEO4J_BOLT_PORT=7687
NEO4J_HTTP_PORT=7474

# Embeddings — keep on Ollama; oMLX ships no embedding model by default
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://<ollama-host>:11434
EMBEDDING_API_STYLE=ollama
EMBEDDING_API_KEY=ollama
```

**oMLX is the required backend for the parallel stages of this fork.** It is not a
preference — the pipeline's throughput and its ability to run several model families
concurrently depend on it:

- **Context is allocated per request.** Ollama preallocates `num_ctx × NUM_PARALLEL`
  (`llama-server -c …`), which collapses per-request speed at high concurrency and
  forces this project to derive `-4k` / `-8k` / `-32k` model variants. oMLX has no such
  allocation, so **no derived `-4k`/`-8k`/`-32k` models are needed**. The two
  `LLM_MODEL_NAME*` variables are still used — but now to split by **model capability**
  rather than context: bulk stages keep `gemma-4-e4b`, while the quality-critical stages
  run a larger model (see [Model compatibility](#model-compatibility-omlx-064-measured-on-mac-studio-m1-ultra--128-gb)).
- **More models parallelize.** Ollama refuses parallelism for the `qwen35`
  architecture (`-np 1`); oMLX accepts it. See
  [oMLX backend: qwen parallelization](#omlx-backend-qwen-can-be-parallelized-where-ollama-cannot-measured).
- **Measured on the same host** (Mac Studio M1 Ultra / 128 GB, realistic profile
  shape, n=8–16): oMLX **120.6–164.5 tok/s** vs Ollama **58–77 tok/s**.

Ollama is still used for **embeddings only** (`nomic-embed-text`, 768-dim). Any
OpenAI-compatible chat API can be substituted by changing `LLM_BASE_URL` /
`LLM_API_KEY`; embeddings additionally need `EMBEDDING_API_STYLE` (see below).

### oMLX backend (required)

oMLX is an MLX inference server for Apple Silicon. The API is FastAPI, so every
endpoint is discoverable at `GET /openapi.json`; there is an admin UI at `/admin`.

```bash
# 1. Install (Homebrew tap). --with-custom-kernel adds native kernels for
#    Bonsai / GLM-5.2 / MiniMax M3 / Qwen3.5-3.6 acceleration (optional).
brew tap jundot/omlx && brew install omlx
brew reinstall omlx --with-custom-kernel     # only if you need those models

# 2. Bind to the network (default is 127.0.0.1, which is invisible to other hosts)
#    in ~/.omlx/settings.json:  "host": "0.0.0.0",  "port": 8010
omlx restart            # or: omlx serve --host 0.0.0.0 --port 8010

# 3. Set the admin API key (until this is done the inference API is unauthenticated)
curl -X POST -H 'Content-Type: application/json' \
     -d '{"api_key":"<key>","api_key_confirm":"<key>"}' \
     http://<omlx-host>:8010/admin/api/setup-api-key

# 4. Download an MLX model straight through the admin API (no SSH needed)
curl -X POST -H "Authorization: Bearer <key>" -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/gemma-4-e4b-it-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download     # 5.18 GB, ~100 s
```

Useful endpoints: `GET /api/status` (public counters, `active_requests`),
`GET /v1/models/status`, `GET /admin/api/models`, `GET /admin/api/global-settings`
(`max_concurrent_requests`), `POST /admin/api/server/restart`.

> **oMLX is not Ollama.** `gemma4:e4b` is a **GGUF** tag and cannot be loaded by oMLX,
> which needs MLX safetensors — use `mlx-community/gemma-4-e4b-it-4bit` (the MLX build
> of the same model). Model ids keep the repo name with `/` → `--`
> (e.g. `mlx-community/gemma-4-e4b-it-4bit` is served as `gemma-4-e4b-it-4bit`).

#### Model compatibility (oMLX 0.6.4, measured on Mac Studio M1 Ultra / 128 GB)

| MLX model | `config.json` `model_type` | Loads? | aggregate, realistic shape |
|---|---|---|---|
| `mlx-community/gemma-4-e4b-it-4bit` | `gemma4` | **yes** | 76.0 → 120.6 → **164.5** tok/s (n=1/8/16) |
| `mlx-community/Qwen3.5-9B-MLX-4bit` | `qwen3_5` | **yes** | 50.7 → 75.0 → 75.3 tok/s |
| `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit` | `qwen3_5` (4-bit affine) | **yes** | 21.3 → 23.8 tok/s (n=8; ~no scaling) |
| `Jundot/Qwen3.8-27B-oQ4e-mtp` | `qwen3_5` (`mtp_compatible`) | **yes** | 18.5–20.0 → 22.9 tok/s (n=8; ~no scaling) |
| `prism-ml/Ternary-Bonsai-2-27B-mlx-2bit` (official ternary) | `prism_hadamard_qwen35` | **no** | load rejected |
| `inductiveML/Ternary-Bonsai-2-27B-mlx-lossless-1.75bpw` | `ternel_hadamard_qwen35` | **no** | load rejected |

- The official **Bonsai 2 27B ternary 2-bit** build (released 2026-09-16) **cannot load**
  on oMLX 0.6.4:
  `Model type prism_hadamard_qwen35 not supported. Error: No module named
  'mlx_vlm.speculative.drafters.prism_hadamard_qwen35'` (HTTP 409).
- **Rebuilding with the Bonsai custom kernels will NOT fix this.** `--with-custom-kernel`
  targets the earlier Bonsai family (Qwen3.6-based `Ternary-Bonsai-27B`, `Bonsai-8B`);
  the Hadamard packs need a `prism_hadamard_qwen35` loader that oMLX 0.6.4 does not
  register at all (the string appears nowhere in the source). Upstream: open issues
  [#3768](https://github.com/jundot/omlx/issues/3768),
  [#3727](https://github.com/jundot/omlx/issues/3727); open PRs
  [#3734](https://github.com/jundot/omlx/pull/3734),
  [#3782](https://github.com/jundot/omlx/pull/3782) (maintainer's, 2026-09-21) —
  **not merged into any release yet**.
  Our install additionally lacks the optional kernels
  (`custom_kernels.bonsai.available = false`), but that is a separate, non-blocking
  issue: for the Bonsai models oMLX *does* support, the kernel is a **speed-only** path
  (pure-MLX fallbacks are correct, just much slower).
- **Workaround that does work:** an MLX conversion whose `config.json` declares the
  plain `qwen3_5` architecture (e.g. `pipenetwork/Ternary-Bonsai-2-27B-MLX-4bit`,
  16.07 GB) loads and generates — but only reaches ~24 tok/s aggregate, i.e. ~7×
  slower than `gemma-4-e4b-it-4bit` on the same workload.
- **Practical rule:** check a model's `config.json` `model_type` before downloading.
  `gemma4` and `qwen3_5` load; `*_hadamard_*` ternary formats currently do not.
  (Ollama/GGUF models such as `gemma4:e4b` can never be used — oMLX needs MLX safetensors.)
- **27B-class models are bandwidth-bound, not parallel.** Every 27B build above lands at
  ~19–24 tok/s aggregate and barely scales past n=8 (1.1–1.15×) — i.e. **~7–8× slower than
  `gemma-4-e4b-it-4bit`**. `mtp_compatible` did **not** help: enabling `mtp_enabled` /
  `vlm_mtp_enabled` / `mtp_num_draft_tokens` left throughput unchanged (19.0 → 18.5 tok/s)
  and produced no MTP log output. A 157-agent prepare would take **~1 h 40 m**, versus
  **~13 min** on `gemma-4-e4b-it-4bit`. → Keep a small model for the bulk stages
  (profiles, simulation) and reserve a 27B model for the accuracy-critical stages via
  `LLM_MODEL_NAME_LARGE` (ontology / config / report).

### Dedicated Neo4j

MiroFish needs its own Neo4j instance — Neo4j Community Edition supports only a single database, so it cannot share one with another project. Start the bundled container:

```bash
docker compose up -d neo4j
```

If ports `7474`/`7687` are already used by another Neo4j, remap the host ports and update `NEO4J_URI` to match:

```bash
NEO4J_HTTP_PORT=7475
NEO4J_BOLT_PORT=7688
NEO4J_URI=bolt://localhost:7688
```

In full-Docker mode (`docker compose up -d`), `docker-compose.yml` points the backend at the `neo4j`/`ollama` services by name automatically; the host-port values above only apply when the backend runs on the host (`npm run backend`).

## Performance & Concurrency

**oMLX is the production backend.** Server-side concurrency is
`scheduler.max_concurrent_requests`; client-side concurrency is the per-stage knobs
below. Context is allocated per request, so there is no `-c` blow-up and no derived
`-4k`/`-8k`/`-32k` models; `LLM_MODEL_NAME` (bulk) and `LLM_MODEL_NAME_LARGE`
(ontology / config / report) now split by **model capability** instead.

| Setting | Where | Production value |
|---|---|---|
| `max_concurrent_requests` | oMLX `global-settings` | **32** (headroom — above saturation only latency grows) |
| `--parallel-profiles` | client (`sim-prepare`) | **16** (measured saturation for long outputs) |
| `CONFIG_BATCH_PARALLEL` | `.env` | 8 |
| `GRAPH_BUILD_PARALLEL` | `.env` | 4 |
| `OASIS_SEMAPHORE` | `.env` | 16 |

Measured on a Mac Studio M1 Ultra / 128 GB with `gemma-4-e4b-it-4bit` (≈2.1 k-token
prompt / 900-token output): **n=1 76.0 · n=8 120.6 · n=16 164.5 · n=32 165.0 tok/s** —
the aggregate saturates at **n≈16** (more slots only add latency). A full 157-agent
`sim-prepare --parallel-profiles 16` ran in **16 m 21 s** (12.03 profiles/min,
`active=16` / `waiting=8` exactly as declared) and passed `scripts/verify_outputs.py`
**17/18** (the single FAIL was the not-yet-run `simulation.db`).

### Legacy: Ollama backend

Ollama is no longer the chat backend — it is retained for the **embedding** endpoint
and as a fallback. It supports parallel inference, but defaults to
`OLLAMA_NUM_PARALLEL=1`, which serializes requests. The simulation fires many
concurrent agent calls, so raise it. Recommended defaults for a 128 GB Apple Silicon
machine (see [Benchmark Results](#benchmark-results-apple-m3-max-128-gb)):

```bash
OLLAMA_NUM_PARALLEL=128       # parallel slots per model (llama.cpp hard ceiling: 256)
OLLAMA_MAX_LOADED_MODELS=2    # keep chat + embedding models resident (do not set 1)
OLLAMA_MAX_QUEUE=256          # queue depth before requests are rejected with HTTP 503
OLLAMA_KEEP_ALIVE=30m         # keep the model resident across the run
OLLAMA_CONTEXT_LENGTH=4096    # per-request context for the bulk of the steps
OLLAMA_NUM_CTX=4096           # per-request context used by the LLM client
OLLAMA_NUM_CTX_ONTOLOGY=65536 # ontology generation only (may feed up to 50,000 chars)
```

Ollama allocates `num_ctx × NUM_PARALLEL` tokens in total and launches the runner as `-c <total> -np <NUM_PARALLEL>`. The memory cost depends on the model's attention type: sliding-window models (e.g. Gemma 4) stay roughly flat (≈17 GiB even at `NUM_PARALLEL=128`), while full-attention models (e.g. Bonsai) scale with `num_ctx`.

- **Docker:** these values are read from `.env` by `docker-compose.yml` (the `ollama` service).
- **Local (macOS):** the Ollama menu bar app ignores shell environment variables and its launchd agent respawns a server with `-np 1`. `scripts/start_all.sh` and `scripts/run_cli.sh` automatically quit/stop it (`launchctl bootout`) and start a server that honours the values above; to do it alone, run `scripts/start_ollama_parallel.sh`.
- **Apple Silicon:** use GGUF models, **not** `*-mlx` tags. Ollama's MLX engine processes requests one at a time, so `OLLAMA_NUM_PARALLEL` has no effect on it. Some MoE architectures also force `parallel=1`; if throughput does not improve, check the Ollama server log.

### Benchmark Results (Apple M3 Max, 128 GB)

Measured on an Apple M3 Max / 128 GB by issuing N concurrent 64-token generations and recording aggregate throughput, median latency and loaded size.

**1-bit Bonsai (`digitsflow/bonsai-8b`, `num_ctx` 8192)**

| NUM_PARALLEL | aggregate | p50 latency |
|---|---|---|
| 4 | 39.5 tok/s | 4.8 s |
| 8 | 41.6 tok/s | 8.8 s |

Bonsai plateaus at ~40 tok/s; extra slots only add latency.

**Gemma 4 (`gemma4:e4b`, derived `gemma4-4k`, `num_ctx` 4096)**

| NUM_PARALLEL | aggregate | p50 latency | loaded |
|---|---|---|---|
| 2 | 34.6 tok/s | 3.7 s | 9.03 GiB |
| 4 | 41.5 tok/s | 6.2 s | 9.19 GiB |
| 8 | 50.5 tok/s | 10.1 s | 9.54 GiB |
| 16 | 140.8 tok/s | 7.3 s | 11.02 GiB |
| 32 | 247.7 tok/s | 8.2 s | 13.09 GiB |
| 64 | 305.4 tok/s | 13.4 s | 17.23 GiB |
| 128 | 270.3 tok/s | 29.7 s | 16.07 GiB |
| 256 | 292.6 tok/s | 53.9 s | 17.30 GiB |
| 512 | **fails** | — | — |

**Findings**

- Throughput saturates at **~300 tok/s**; beyond ~128 slots the gain is marginal and per-request latency grows, because total work is memory-bandwidth bound. **`NUM_PARALLEL=128` is the recommended default.**
- **256 is the hard ceiling.** 512 always fails: llama.cpp rejects it with `n_seq_max must be <= 256`.
- **Model choice dominates.** On this machine 1-bit Bonsai is *slower* (~40 tok/s) than Q4_K_M Gemma 4 (~300 tok/s) at high parallelism — 1-bit kernels are still immature on Metal.
- Memory: Gemma 4 stays ≈16–17 GiB from NP=8 to NP=256 (sliding-window KV). Bonsai's full-attention KV scales with `num_ctx` (e.g. `num_ctx=65536` × NP=8 → ~75 GiB), so keep its `num_ctx` small at high parallelism.
- **Prepare concurrency** is bounded by `parallel_profile_count` (default 5), not by `OLLAMA_NUM_PARALLEL`. Raise it to actually use the slots: `--parallel-profiles 128` on `resume`/`sim-prepare`/`pipeline`.

**Controlling per-slot context.** Ollama uses the model's `num_ctx` as the per-slot context; pin it by deriving a model:

```bash
ollama show --modelfile gemma4:e4b > /tmp/m.modelfile
printf '\nPARAMETER num_ctx 4096\n' >> /tmp/m.modelfile
ollama create gemma4-4k -f /tmp/m.modelfile
```

### vLLM (and other OpenAI-compatible backends)

vLLM offers much better throughput at high concurrency, but it has **no native macOS/Apple Silicon backend** — it runs on Linux with NVIDIA (CUDA) or AMD (ROCm). If you have such a machine, the app uses it for chat via config only, and for embeddings with one extra setting:

```bash
LLM_BASE_URL=http://gpu-host:8000/v1
LLM_MODEL_NAME=<served-model-name>
OPENAI_API_BASE_URL=http://gpu-host:8000/v1

EMBEDDING_BASE_URL=http://gpu-host:8000/v1
EMBEDDING_API_STYLE=openai
EMBEDDING_MODEL=<served-embedding-model>
```

`EMBEDDING_API_STYLE=auto` (default) uses Ollama's native `/api/embed` when `EMBEDDING_BASE_URL` points at port 11434, otherwise the OpenAI-compatible `/v1/embeddings`. The embedding model must produce **768-dimensional** vectors to match the Neo4j vector index; using another dimension requires updating `neo4j_schema.py` and `VECTOR_DIMENSION` in `embedding_service.py`, then rebuilding the graph. Without a GPU server, tuning `OLLAMA_NUM_PARALLEL` is the practical path on Apple Silicon.

## Command-line Interface

The web UI is a thin client over the REST API, so the whole workflow can be driven from the terminal with `scripts/mirofish_cli.py` (Python 3.11+, standard library only).

To run it **without the web UI, from a single terminal**, use `scripts/run_cli.sh`. It ensures Neo4j is up, restarts Ollama with the parallel settings, starts the backend, runs the CLI command you pass, and stops the backend on exit (Ollama is left running):

```bash
./scripts/run_cli.sh pipeline --requirement "..." --file press_release.pdf --max-rounds 50 --report-out report.md
./scripts/run_cli.sh sim-status --simulation-id sim_xxxx
./scripts/run_cli.sh --help
```

For the GUI instead, use `scripts/start_all.sh` (opens the web app at http://localhost:3000).

If the backend is already running (`npm run backend`), call the CLI directly:

```bash
python scripts/mirofish_cli.py projects                      # newest first, readable table
python scripts/mirofish_cli.py project --project-id proj_xxxx
python scripts/mirofish_cli.py resume --index 3              # show the next step for row #3
python scripts/mirofish_cli.py resume --index 3 --run        # run remaining steps until done
python scripts/mirofish_cli.py resume --index 3 --run --once # run just one step
```

New projects are auto-named from the document plus a timestamp (e.g. `paper (2026-09-13 09:56)`), so they are easy to find later. `resume` inspects the project, its simulations and reports, then prints the next command — or with `--run` executes the remaining pipeline steps (build → prepare → simulate → report) until the report is ready.

```bash
# End-to-end: ontology -> graph build -> prepare -> simulate -> report
python scripts/mirofish_cli.py pipeline \
  --requirement "How will the public react to this policy?" \
  --file press_release.pdf --max-rounds 50 \
  --report-out report.md

# Individual steps
python scripts/mirofish_cli.py ontology --requirement "..." --file doc.pdf
python scripts/mirofish_cli.py build --project-id proj_xxxx
python scripts/mirofish_cli.py sim-create --project-id proj_xxxx
python scripts/mirofish_cli.py sim-prepare --simulation-id sim_xxxx
python scripts/mirofish_cli.py sim-start --simulation-id sim_xxxx --follow
python scripts/mirofish_cli.py report-generate --simulation-id sim_xxxx
python scripts/mirofish_cli.py interview --simulation-id sim_xxxx --agent-id 0 --prompt "Why?"
python scripts/mirofish_cli.py report-chat --simulation-id sim_xxxx --message "Summarize the sentiment"
```

`--base-url` (or `MIROFISH_API_BASE`) points at the backend; `--json` prints raw JSON; `--help` lists every command. `--timeout` is the HTTP request timeout and `--max-wait` is the per-step polling limit (default 7200s) — raise `--max-wait` for slow local models.

## Operating policy (READ FIRST — follow this simple policy above all rules)

The failures in this project were almost never from missing a detailed rule; they
came from **not applying this simple policy**. When in doubt, follow this, not the
long lists below.

1. **Model: gemma4:12b first** (accuracy). e4b only when speed is the goal.
2. **Context: keep it SMALL by default.** Required context differs per stage, so pick
   a small `num_ctx` that is *generally sufficient* (bulk = 8192, which covers NER,
   profiles, simulation). Only the stages that genuinely need more — ontology,
   simulation_config, and the **final report/PDF** — use a larger one (32768).
   Never run the whole pipeline on one large context.
3. **Run in parallel.** With the small context, make `NP` (server slots) and `C`
   (client concurrency) actually take effect, and verify the runner args.
4. **Be recoverable from failure.** Resume from saved artifacts; write progress files
   atomically; keep exactly one server and one backend. A failure must never lose
   completed work or require starting over.
5. **Prefer this simple policy over adding more rules.** If you feel the urge to add
   a rule, first check whether the simple policy already covers it.

### Characteristic: agents do NOT understand this simple policy

Observed repeatedly: agents read detailed rules but still run one big context, kill
parallelism, lose work on failure, or run blind for hours. That is the real defect —
**not knowing / not applying the simple policy** — and it is a *system characteristic*
of the agent, not a one-off bug.

- Symptom: one large ctx for everything / parallelism neutralized / total loss on a
  crash / multi-hour runs with no measured rate.
- Check before acting: *does my next action satisfy (small ctx) + (parallel) +
  (recoverable)?* If not, fix that first.
- The detailed failure log below exists to illustrate this policy — not to replace it.

### Characteristic: agents do NOT notice problems and leave them unattended

Another recurring agent defect: real anomalies sit in plain view and are never
noticed or acted on, so a broken/slow run is treated as "fine" and built upon.

Examples actually observed:
- The model generated ~**1,810 tokens** per profile while only ~**850** were saved
  (~50% wasted on thinking) — nobody compared `n_gen`/`completion_tokens` against the
  saved output length.
- Declared `NP=16` but the runner was `-np 4` (and an app server stole the port) —
  nobody compared declared vs actual.
- Per-request latency far above what the observed token rate implies — the
  inconsistency was not investigated.

**Required behavior:** actively compare *expected vs observed* on every run —
tokens generated vs tokens saved, declared concurrency vs runner args, observed
latency vs `tokens / rate` — and investigate any mismatch **before** continuing.
Treat "a number that looks off" as a defect to explain, not background noise.

### Characteristic: agents fail at the sub-process level (sloppy fundamentals)

The failures here were **not** strategy failures — they were careless execution of
the **sub-processes (素過程)**: context sizing, port hygiene, write atomicity,
measuring the right metric, verifying the runner args, noticing anomalies. Because
the fundamentals were done sloppily, no amount of high-level planning saved the run.

- A one-time, written trace of every sub-process is kept in
  [`docs/process-trace-2026-09-14.md`](docs/process-trace-2026-09-14.md). Read it and
  keep it current: **if a sub-process is not written down, it gets repeated wrong.**
- Required: treat each sub-process as a first-class deliverable — do it rigorously,
  verify it (expected vs observed), and record it.

## Operational Tooling & Skills

Running MiroFish locally is a long, failure-prone job (LLM timeouts, truncated
JSON, stalls). The repo ships helper scripts and relies on a few agent skills
for **monitoring, error detection, and reporting**.

### Helper scripts

| Script | Purpose |
|---|---|
| `scripts/run_cli.sh` | One terminal, no GUI: ensures Neo4j, restarts Ollama with the tuned env, starts the backend, runs a CLI command, stops the backend on exit. |
| `scripts/start_all.sh` | One terminal with the web UI (Neo4j + Ollama + backend + frontend). |
| `scripts/stop_all.sh [--ollama] [--neo4j] [--all]` | Stop backend/frontend (optionally Ollama and/or Neo4j). |
| `scripts/start_ollama_parallel.sh` | Restart Ollama with the parallel settings, foreground. |
| `scripts/lib_ollama.sh` | Shared helper the launchers use to (re)start Ollama correctly (quits the macOS app/agent, exports the tuning env). |
| `scripts/monitor.sh [interval]` | Append a status snapshot (profile count, runner `-c/-np`, memory, CLI progress line) to `/tmp/mirofish-monitor.log` every N seconds (default 60). |
| `scripts/supervise.sh` | Stage-aware supervisor: watches **multiple** progress signals (profile count, config-generation progress, artifacts, CLI line), tolerates normal in-generation errors/retries, **restarts only on a genuine stall** (reusing completed work), and stops when prepare completes. |
| `scripts/verify_outputs.py` | Fixed output-verification checklist for a simulation: artifact presence, JSON validity, required/unique/contiguous ids, config↔profile id equality, truncation/fallback/timeout counts, and per-request speed. Exits non-zero on FAIL. |

### Output verification checklist (run before claiming success)

Process exit is not proof. Run `python scripts/verify_outputs.py --simulation-id sim_xxxx`
and show the PASS/FAIL list. Full checklist (also a skill, `mirofish-output-verification`):

| Area | Check |
|---|---|
| A. artifacts | profiles / config / simulation `.db` exist and parse; no `.corrupt.bak` left behind |
| B. profiles | valid JSON; `user_id` unique **and contiguous `0..N-1`**; required fields non-empty; names unique; persona length median sane |
| C. config | `agent_configs` count == profiles; `time_config`/`event_config`/`llm_model` present; **`{agent_id}` == `{profile user_id}`** |
| D. quality | `LLM output truncated` low; `using rule-based generation` ≈ 0; `Request timed out` == 0 |
| E. speed | per-request tok/s (from `eval time` in the Ollama log) × **active slots** = aggregate; reconcile with observed completion rate; ETA = `tokens_per_unit × N / aggregate` |

**Rule:** always reconcile *declared concurrency* vs *active slots* vs *observed
rate*. If per-request-rate × concurrency ≫ observed rate, something else dominates
(prefill / queueing / retries / swap) — diagnose before reporting. Fixing a FAIL
requires evidence (measured value), not "looks fine".

### Skills for monitoring / error detection / reporting

| Skill | Use |
|---|---|
| `operational-guardrails` | Behavior rules for long-running work: no unilateral stop/restart, follow instructions literally, diagnose with evidence before acting, multi-signal stall detection, non-destructive recovery, terse milestone reporting. |
| `debugging` | Hypothesis-driven runtime debugging (≥3 hypotheses, parallel investigation, confirm root cause, lock with a failing test, fix minimally, verify by using the system). |
| `verification-planning` | Build a project-specific evidence path for a claim before changing a non-trivial system. |
| `reflect` | Review recent work and turn recurring friction (e.g. supervision mistakes) into reusable skills or config. |
| `oh-my-opencode-slim` | Tune agents/models/prompts when workflow friction repeats. |

### Typical supervised run

```bash
# terminal 1: supervisor (non-destructive restart only on a real stall)
./scripts/supervise.sh
# terminal 2 (optional): periodic snapshots
./scripts/monitor.sh 60
tail -f /tmp/mirofish-supervise.log    # stall / restart events
tail -f /tmp/mirofish-resume.log       # job progress
```

Rules the tooling follows: in-generation errors such as `LLM output truncated`,
`JSON parsing failed (attempt N)`, `Request timed out`, and `using rule-based
generation` are **normal** and are not treated as stalls; a stall requires
**no change in any progress signal** for the stall window; recovery reuses
completed artifacts instead of regenerating them.

### Step timing, ETA, and anomaly-driven fixes

Do not just wait. Measure each step and act on the slow ones:

1. **Measure per step** — take the timestamps of consecutive progress lines
   (e.g. `[3/13] -> [4/13]`) and compute the delta.
2. **Estimate completion** — remaining steps x observed average (per step type).
3. **Flag anomalies** — a step taking > ~2x the median (or over a hard cap) is
   suspicious, not merely slow.
4. **Investigate immediately** — read that step's log lines (output truncation,
   JSON parse failures, timeouts, retries, fallback-to-rule-based, connection
   errors) and check CPU/memory/swap; find the root cause.
5. **Fix and verify** — apply the fix (config or code), re-run non-destructively
   (reuse completed artifacts), and confirm the step delta improved.

Known operational lessons learned this way:

- **Config generation truncation.** `simulation_config_generator` originally sent
  no per-request context, so Ollama used the model default (4096) and large
  multi-agent JSON output was truncated (`finish_reason=length`) and failed to
  parse, causing retry storms. Fix: per-request context via
  `OLLAMA_NUM_CTX_CONFIG` (default 16384) plus `LLM_TIMEOUT`.
- **Per-request timeout vs concurrency.** Under high concurrency a single profile
  can exceed the client timeout; using a shorter timeout plus retries, and a
  moderate `--parallel-profiles`, avoids a whole batch being blocked.
- **Lid close = sleep.** Closing the laptop suspends the job (and can trigger
  swap); `caffeinate` does not prevent lid-close sleep.
- **Supervisor false positives.** A supervisor keyed on a single counter (saved
  profiles) falsely restarted during the config phase; use multiple progress
  signals and stop supervising once the guarded phase completes.
- **Process hygiene.** During each check, count required processes (`ollama serve`,
  `llama-server`, backend, CLI run, supervisor, monitors) and stop duplicates,
  orphans, and leftovers immediately; watch per-process memory so a large runner
  does not push the machine into swap.

### Operations failure log (do not repeat)

| Symptom | Root cause | Required behavior |
|---|---|---|
| Progress lost by restarts | unilateral stop/restart | get explicit approval; reuse artifacts; state the cost |
| "Resume"/"from the remaining N" became "from scratch" | instruction reinterpreted | follow the literal instruction; ask once if ambiguous |
| NUM_PARALLEL had no effect; serialized | macOS Ollama app/launchd respawned `ollama serve` with `-np 1` | quit/bootout the app; one server; verify runner `-np` |
| Still truncated at 4096 | `extra_body options.num_ctx` ignored by Ollama's OpenAI endpoint | set the model's `num_ctx` (derived model); verify runner `-c` |
| Multi-minute config steps, retry storm | output truncated + JSON parse failures | raise effective context; fix over-budget steps immediately |
| Supervisor restarted during config | single-counter stall detection | multi-signal progress; stop supervising after the guarded phase |
| Restart regenerated all profiles | generation had no resume | reuse saved profiles (by `user_id`) |
| No completions under load | concurrency vs client timeout | shorter timeout + retries; moderate parallelism |
| Job pauses mid-run | laptop lid closed (sleep); can trigger swap | keep lid open / AC+display; `caffeinate` does not prevent lid close |
| Throughput collapse | swap thrash (unused RAM ~0) | keep swap ~0 and headroom; free other consumers; watch `vm.swapusage` |
| Parallelism > 256 fails | llama.cpp `n_seq_max <= 256` | cap at 256; 128 practical default |
| Memory wasted / conflicts | duplicate or orphaned processes | audit counts each cycle; kill duplicates/orphans |
| Stop script missed the backend | process-name mismatch | match the real command line; verify ports after stopping |
| Claimed a fix without proof | unverified assumption | verify actual runner args/artifacts |
| Idle waiting | long blocking sleeps | check frequently and act |
| Completed steps redone on restart | no partial persistence | persist per-step/batch progress (`simulation_config.partial.json`); regenerate only unfinished items |
| Resume state failed/corrupted | dataclasses not serializable; non-atomic write | `asdict` + atomic write (temp + `os.replace`); validate the partial file parses |
| Supervisor restarted during long in-flight work | stall window shorter than the step; no activity signal | set the window above the longest step; include LLM server-log/CPU activity as a progress signal |
| Waited without verifying | passive sleep, no status check | after every wait verify process + status API + newest artifact/log; if progress cannot be confirmed, investigate |
| NUM_PARALLEL had no effect for one model | Ollama: `architecture does not support parallel requests` (qwen35) → runner forced `-np 1` | check the Ollama log for this warning **before** tuning NP; use a model that supports parallel (gemma4) for production |
| JSON mode returned empty content | thinking model put everything in `reasoning` | set `reasoning_effort:"none"` in **all** clients (`llm_client.py` + camel `run_*_simulation.py`); `think:false` does not work on the OpenAI endpoint |
| Multi-process "parallel" gave no speedup | single GPU time-slices N processes | do not use N `ollama serve` + round-robin proxy on one GPU; measured +20% at N=4, N× latency, swap thrash at N=8 |
| `reddit_profiles.json` corrupted, 93 → 13 profiles lost | non-atomic full-file rewrite + kill mid-write | write atomically (temp + `os.replace` + `fsync`); recover by salvaging the complete prefix (`json.JSONDecoder().raw_decode` loop) |
| Progress silently overwritten / count dropped | two backends running prepare on the same file | keep **exactly one** backend; audit `pgrep -f run.py` each cycle; mismatch of profiles count is the tell |
| Hours wasted on unvalidated long runs | config guessed, not measured | measure aggregate throughput on a 2–3 min representative slice, then run once |

### Failure categories (comprehensive — every failure observed so far)

Grouped so nothing is missed. Each item = symptom → cause → required behavior.

**A. Process / lifecycle**
1. Progress lost after a restart → unilateral stop/restart → get explicit approval;
   state the cost; reuse artifacts.
2. "Resume" / "from the remaining N" silently became "from scratch" → instruction
   reinterpreted → follow the literal instruction; ask one short question if ambiguous.
3. Duplicate / orphaned processes (`ollama serve`, `llama-server`, backend, CLI,
   supervisor, monitors) → wasted memory and conflicts → audit counts each cycle;
   keep exactly one of each.
4. Two backends ran prepare on the same file → progress silently overwritten / count
   dropped (89 → 8) → keep exactly one backend; mismatch of the profile count is the tell.
4b. **Two config generations raced on `simulation_config.partial.json`** (batch count
   went 5 → 3). The backend runs prepare as a **background task that survives the CLI**,
   so restarting the CLI starts a *second* prepare while the first still runs. →
   Ensure a **single prepare invocation**; verify
   `grep -c "Starting intelligent simulation configuration generation"` == 1 before
   trusting progress; on restart, resume from the partial rather than re-running.
4c. **Pipeline stops after the simulation; report never auto-generates.** The
   simulation (platform `parallel`) keeps the env in **wait mode**, so `sim-status`
   never reports "finished"; the CLI polls it to the **20-step cap** and exits with
   `error: resume did not finish after 20 steps`. `reddit_completed=True` but
   `twitter_running=True`, and no report is produced. → After the run completes,
   **close the env first** (`sim-close --simulation-id …`) so the sim process exits,
   then run `report-generate --simulation-id …`. (Verification then passes 19/19.)
5. Stop helper missed the backend → process-name mismatch → match the real command
   line; verify ports/processes after stopping.
6. macOS Ollama.app respawned `ollama serve` with `-np 1/4` and stole port 11434 →
   double LISTEN, serialized → quit/`pkill` the app; single server; verify `-np`.
7. Idle waiting (long blocking sleeps) and waiting without verifying → a job may be
   stalled/dead → after every wait verify process + status/artifacts; investigate if
   progress can't be confirmed.
8. Supervisor false restarts → single-counter stall detection, or stall window shorter
   than the longest step → multi-signal progress; window > longest step; include LLM
   activity; stop supervising once the guarded phase completes.
9. Closing the laptop lid = sleep = the job freezes (can trigger swap) → keep lid
   open / AC + display; `caffeinate` does NOT prevent lid-close sleep.

**B. Context / configuration**
10. Output truncated at 4096 despite requesting more → **Ollama's OpenAI endpoint
    ignores `extra_body.options.num_ctx`** → set the derived model's `num_ctx`; verify
    runner `-c`.
11. One large `num_ctx` for every stage → `-c = num_ctx × NP` explodes and parallel
    per-request speed collapses (15 → 1.5 tok/s) → **split models per stage**
    (`LLM_MODEL_NAME` small for bulk; `LLM_MODEL_NAME_LARGE` for ontology/config/report).
12. Config intent ≠ runner reality → `-c`/`-np` never verified → always read the
    runner's actual args (and the model blob) before trusting a config.
13. Ollama **silently lowered NP** (asked NP=16, runner got `-np 4`) → verify `-np`.
14. Truncation + JSON parse failures → retry storms and multi-minute steps → raise the
    effective (model) context; treat over-budget steps as suspicious and fix at once.
15. Parallelism > 256 impossible (`n_seq_max must be <= 256`) → cap at 256 (practical
    default 128).

**C. Concurrency / performance**
16. NUM_PARALLEL had no effect / stayed serialized → Ollama architecture limitation
    (`model architecture does not support parallel requests`, e.g. qwen35) → check the
    Ollama log for this warning **before** tuning NP; use a model that parallelizes.
17. N `ollama serve` + round-robin proxy gave no speedup → one GPU time-slices N
    processes → don't do this (measured +20% at N=4, N× latency, swap thrash at N=8).
18. No completions under load → client timeout shorter than per-request latency at
    high concurrency → `timeout > 2 × per-request`; moderate concurrency per stage.
19. Misjudged speed → judged by `eval tok/s` only, and misread `n_gen`/`n_tokens` as
    output length (they are **prompt + output**) → measure the **end-to-end completion
    rate** (items/min).
20. Broad NP sweeps (2..128) run for hours → no short targeted measurement → measure a
    2–3 minute slice and pick; run once.
21. Throughput collapse → swap thrash (unused RAM ~0) → keep swap ~0 and real headroom;
    size concurrency to RAM; watch `vm.swapusage`.
22. Hours wasted on unvalidated long runs → config guessed, not measured → measure a
    representative slice, then commit to a single run.

**D. Data integrity / persistence**
23. `reddit_profiles.json` corrupted (93 → 13 profiles lost) → non-atomic full-file
    rewrite + kill mid-write → atomic write (temp + `os.replace` + `fsync`); salvage
    the complete prefix via `json.JSONDecoder().raw_decode` loop.
24. Resume state failed/corrupted → dataclasses not serializable; non-atomic write →
    `asdict` + atomic write; validate the file parses before relying on it.
25. Completed steps redone on restart → no partial persistence → persist per-step/batch
    (e.g. `simulation_config.partial.json`); regenerate only unfinished items.
26. Restart regenerated all profiles → generation had no resume → reuse saved profiles
    (match by `user_id`).

**E. Model-specific**
27. JSON mode returned **empty `content`** → thinking model spent its budget in the
    separate `reasoning` field → set `reasoning_effort:"none"` in **every** client
    (`llm_client.py` AND camel `run_*_simulation.py` via `model_config_dict`);
    `think:false` does not work on the OpenAI endpoint.
28. NER extracted **0 entities** → prompt said "only ontology types / be precise" too
    strictly → allow recall (include authors in reference lists etc.).

**F. Agent behavior (operational discipline)**
29. Started long runs without measuring the end-to-end completion rate.
30. Ignored an explicit instruction (small ctx for bulk stages).
31. Restart churn corrupted state and lost work.
32. Wrote project-specific rules into **global** config (scope violation).
33. Kept investing in a known-bad idea (N-process proxy) instead of changing method.
34. Reporting swung between spam and too-sparse; report tersely at milestones.
35. Did not proactively reduce scope when the machine could not handle it.
36. Claimed a fix without proof → always verify actual runner args/artifacts.

### Failure → lesson → generalized rule (how these guardrails were derived)

Most failures here were **not** from disobeying an instruction; they came from
following the *literal* instruction while **not understanding its purpose/intent**,
so effort optimized the wrong thing. This section records that process so the rules
generalize beyond this project.

| Failure (what happened) | Purpose/intent that was missed | Generalized rule to ADD |
|---|---|---|
| Ran every stage with one large `num_ctx` | The point of ctx is per-stage: bulk must stay small so parallelism works; only ontology/config/report need large | **Do not apply one uniform setting to heterogeneous stages.** Identify each stage's requirement and configure per stage |
| Started multi-hour runs without measuring the completion rate | Success = a *finished output*, not a *started job*; `eval tok/s` ≠ throughput | **Define success as completion.** Before any long run, measure the end-to-end rate (items/min) on a short slice |
| Misread `n_gen`/`n_tokens` as output length | Metrics have precise semantics; acting on an unverified definition is guessing | **[?] Do not act on a metric whose definition you have not verified** in the docs/logs |
| Repeated restarts; lost 93→13 profiles | "resume/keep going" means *preserve and continue*, not *restart* | **Do not stop/restart/reconfigure without explicit approval + cost accounting; reuse artifacts** |
| Wrote project rules into global config | The intent was to record lessons *for this project* | **Respect scope boundaries: never put project-specific content into shared/global config** |
| Pursued N-process + round-robin proxy on one GPU | A single GPU time-slices; the premise (independent parallel hardware) was false | **Do not invest in an idea whose fundamental premise (hardware/architecture) is unverified** |
| Ran NP=2..128 sweeps for hours | The goal was a *decision*, not exhaustive data | **Do not run exhaustive sweeps.** Take one targeted 2–3 min measurement and decide |
| Trusted config intent over the runner's real args | What matters is observed behavior, not intended config | **Do not claim a setting is applied until you read the actual runner args / artifacts** |
| Reporting swung from spam to silence | The user needs steady, terse milestone updates | **Report terse milestone updates; neither spam nor go silent** |
| Kept the full scale though the machine couldn't finish | The intent is a *feasible* end-to-end result; scale is negotiable | **Do not silently run an infeasible plan.** Surface the constraint and propose the tradeoff |
| Claimed a fix without proof | The intent is a *verified* fix | **Do not claim success without evidence** (measured value / artifact) |

### Generalized prohibitions (apply to any project)

1. **Don't optimize the literal text of an instruction; optimize its purpose.** Before
   an expensive action, state the purpose and the success criterion, and check that the
   literal request actually serves them. If they conflict (e.g. "run it" but the config
   cannot finish in acceptable time/resources), STOP and reconcile — do not just run.
2. **Don't start any long/expensive job until you can predict its end-to-end rate and
   its end condition.** "It started" is not "it will finish".
3. **Don't trust intended config; verify actual behavior** (runner args, artifacts).
4. **Don't act on unverified metrics or assumptions.** Verify the definition and the value.
5. **Don't stop/restart/change a running job without explicit approval** and a stated cost.
6. **Don't sweep parameters.** One hypothesis, one short measurement, then decide.
7. **Don't exceed scope** (project vs global; the task vs a broader rewrite).
8. **Don't leave the user guessing.** Report terse milestones, failures with evidence,
   and the next decision point.
9. **Don't repeat a failed action unchanged.** Diagnose, change one variable, measure.
10. **Write the lesson down at the moment it's learned** (skill/README), and generalize
    it — a rule that only fixes one case will be broken again by the next case.
11. **Don't flail when you're out of good ideas — consult the user once.** If an
    approach isn't working and you cannot generate a *better* idea, STOP trying
    variations and ask: state the current state, the options you can see, and your
    recommendation. Blindly retrying is what turns one failure into many. (A short,
    well-framed consultation is cheap; another wasted multi-hour run is not.)

### System characteristics (design around these)

- **`extra_body.options.num_ctx` is ignored** by Ollama's OpenAI endpoint → the
  effective per-slot context is the **derived model's `num_ctx`** only.
- Runner = `llama-server -c <num_ctx × NP> -np NP`. **A large `-c` collapses
  per-request speed under concurrency** (8192 single = 15 tok/s vs 32768×NP8 ≈ 1.5).
- **NP may be silently lowered** by Ollama (asked 16, got 4) — always read `-np`.
- **Architecture decides parallelism — and so does the runtime.** On **Ollama**,
  gemma4 (sliding-window) parallelizes while qwen35 (hybrid attention) does **not**
  (forced `-np 1`, `n_seq_max = 1`). On **oMLX** the same qwen3_5 architecture *is*
  accepted in parallel (`peak_active = 16`); it just scales poorly (~75 tok/s
  saturated). See [oMLX backend: qwen parallelization](#omlx-backend-qwen-can-be-parallelized-where-ollama-cannot-measured).
- **Ollama.app respawns** and can steal port 11434 with a bad `-np` (double LISTEN).
  **Fix: run the tuned server on a dedicated port** (`OLLAMA_HOST=127.0.0.1:11500`)
  and point `LLM_BASE_URL` / `OPENAI_API_BASE_URL` / `EMBEDDING_BASE_URL` at it
  (`EMBEDDING_API_STYLE=ollama`). Then the app can come and go on 11434 harmlessly.
  `scripts/lib_ollama.sh` does this by default.
- `n_gen` / `n_tokens` = **prompt + output** (real profile output is ~**850 tokens**,
  even though `n_gen` shows ~2,500).
- **One GPU**: N processes time-slice; multi-process "parallel" ≠ throughput.
- **Swap thrash** (unused RAM ≈ 0) collapses throughput.
- llama.cpp hard cap: `n_seq_max ≤ 256`.
- **Exactly one backend** — two backends overwrite the same progress file.
- Progress files must be **atomic** (temp + `os.replace` + `fsync`).

### Parallelization audit

Independent per-item work in the pipeline should run concurrently (bounded by
`OLLAMA_NUM_PARALLEL`, so it uses spare slots without extra context allocation).

| Site | Env knob | Default |
|---|---|---|
| Agent-config batches (`simulation_config_generator`) | `CONFIG_BATCH_PARALLEL` | 8 |
| Graph-build chunk processing (`graph_builder.add_text_batches`) | `GRAPH_BUILD_PARALLEL` | 4 |
| Profile generation (`oasis_profile_generator`) | `--parallel-profiles` | 16 |
| Embedding requests | already batched (`embed_batch`) | — |
| Report sections | serial (dependent context) | — |

When adding new per-item loops, parallelize them and add the knob here.

**Time-budget rule when parallelizing:** aggregate throughput saturates, so
`per-request rate ≈ aggregate / concurrency` and `per-unit time ≈ tokens ×
concurrency / aggregate`. A per-request timeout that worked at concurrency 1 is
too short at concurrency N; recompute it (with margin) before raising a
concurrency knob, and estimate total wall time as `total tokens / aggregate`, not
from the unit count or the serial per-unit time.

### Model selection & real-workload concurrency (measured)

**Terminology** (no shorthand without a definition):
- **NP** = `OLLAMA_NUM_PARALLEL` — server-side slots for one model. Ollama runs
  the runner as `llama-server -c <num_ctx × NP> -np NP`.
- **C** = client-side concurrency for one stage: `--parallel-profiles`,
  `CONFIG_BATCH_PARALLEL`, `GRAPH_BUILD_PARALLEL`, `OASIS_SEMAPHORE`.
- **aggregate** = generated tokens ÷ wall time for the whole job (the machine's
  real ceiling; more concurrency does not raise it past saturation).

| Model | Parallel? | ~Aggregate | Real-workload behavior |
|---|---|---|---|
| **gemma4:12b** (first choice — accuracy) | yes | 152 tok/s peak (NP=64, short prompts) | profiles ~2,500 tok; C=8 → ~14 tok/s, C=12 → ~6.7 (contention). Use **NP=16, C=8, `LLM_TIMEOUT=1800`** |
| **gemma4:e4b** (speed) | yes | 208 tok/s (NP=64) | profiles ~880 tok; fastest end-to-end (~30–45 min prepare) |
| **qwen3.5:9b** | **no** on Ollama / yes but slow on oMLX | ~48 tok/s (Ollama, flat) / ~75 tok/s (oMLX, saturates) | Ollama forces `-np 1` (`architecture does not support parallel`); **oMLX runs it in parallel but saturates early** — see [oMLX backend: qwen parallelization](#omlx-backend-qwen-can-be-parallelized-where-ollama-cannot-measured). Thinking model needs `reasoning_effort=none` |

Hard-won rules:
- **Check the Ollama log for `does not support parallel requests` before tuning
  NP.** If present, `-np` is forced to 1 and NP tuning is wasted.
- **Do not parallelize a serial model with N processes + round-robin proxy** — a
  single GPU time-slices (measured: N=4 gave only +20% aggregate and N× latency).
- **Tune concurrency per stage**: high for short outputs (NER chunks, sim actions),
  lower for long outputs (profiles, config batches); keep
  `timeout > 2 × per-request time`.
- **Probe with a 2–3 minute measurement, then run once.** Do not launch multi-hour
  runs to "see if it works"; benchmark with the real prompt/output shape.

### oMLX backend: qwen can be parallelized where Ollama cannot (measured)

Ollama refuses parallelism for the `qwen35` architecture, which made `qwen3.5:9b`
unusable for throughput (always `-np 1`, `n_seq_max = 1`, serialized). **oMLX
(MLX on Apple Silicon) removes that restriction.** Measured with the same realistic
prompt shape (~2,093-token prompt / 900-token output, `reasoning_effort:none`):

| Runtime | Model | n=1 | n=8 | n=16 | Parallel accepted? |
|---|---|---|---|---|---|
| Ollama 0.34.2 | `qwen3.5:9b` | ~48 tok/s | ~48 tok/s | ~48 tok/s | **no** — `-np 1`, `n_seq_max=1`, serialized |
| **oMLX 0.6.4** | **`Qwen3.5-9B-MLX-4bit`** | 50.7 tok/s | **75.0 tok/s** | **75.3 tok/s** | **yes** — peak `active_requests` = 8 / 16 |
| oMLX 0.6.4 | `gemma-4-e4b-it-4bit` (reference) | 76.0\* | 120.6 tok/s | 164.5 tok/s | yes |

\* gemma single-stream: 76.0 tok/s was measured with a short prompt; the n=8/n=16
columns are the same realistic shape used for qwen, so only those columns are
directly comparable.

Hosts: oMLX = Mac Studio M1 Ultra / 128 GB; the Ollama `qwen3.5:9b` figure is from
the earlier handover measurement on an M3 Max — but serialization is an
**architecture** limitation, so that number is flat regardless of concurrency or host.

Conclusions:

- **oMLX does parallelize qwen3.5.** `Qwen3.5-9B-MLX-4bit` reports
  `config_model_type = qwen3_5` — the very family Ollama rejected — yet 16 requests
  were genuinely in flight (`peak_active = 16`) with no architecture refusal.
  Ollama's `-np 1` serialization does **not** occur.
- **But qwen3.5 scales poorly.** Aggregate saturates at ~75 tok/s (only 1.5× the
  50.7 tok/s single stream, and flat from n=8), versus 120.6 → 164.5 tok/s for
  gemma-4-e4b on the same host and shape. The cause is neither memory
  (10.85 GB / 107.5 GB used) nor scheduling (n=16 was active) — the
  hybrid-attention decode simply batches inefficiently under MLX.
- **Practical impact.** For a 157-agent prepare (~141 k output tokens):
  **3–7 hours** on Ollama (serialized) → **~31 min** on oMLX (`total_tokens / 75`).
  qwen therefore becomes usable, though still ~2.2× slower than gemma-4-e4b
  (~13 min for the profile stage).
- **Recommendation.** Keep gemma-4 for throughput; pick qwen only when its accuracy
  is required, and size the ETA as `total_tokens / 75`.
- **Not measured yet.** MoE qwen variants (e.g. `Qwen3.6-35B-A3B`) may batch better;
  a separate run would be needed to confirm.

Add a qwen MLX model to oMLX (no SSH needed — admin API key required):

```bash
curl -s -X POST -H "Authorization: Bearer $OMLX_API_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"repo_id":"mlx-community/Qwen3.5-9B-MLX-4bit"}' \
     http://<omlx-host>:8010/admin/api/hf/download      # ~6 GB, ~100 s
# then call the model id as served: Qwen3.5-9B-MLX-4bit
```

> **Why oMLX needs no derived models here.** Ollama preallocates
> `num_ctx × NUM_PARALLEL` (`-c`), which is why this project derives
> `gemma4-4k` / `-8k` / `-32k` models per stage. oMLX allocates context per request,
> so the 8k/32k model split does not apply and the `-c` collapse cannot happen.

### Capturing lessons (mandatory)

Agents operating this system are not reliable at adapting on the fly. Therefore:

- Every failure (stall, timeout, corrupted resume state, false restart,
  misinterpreted instruction) MUST be captured in a skill
  (`~/.config/opencode/skills/operational-guardrails/SKILL.md`) and in the
  failure log above. Do not stop at an ad-hoc fix.
- Use the learning/debugging skills to extract and generalize lessons:
  `reflect` (turn recurring friction into reusable skills/config),
  `debugging` (root-cause), `verification-planning` (evidence path).
- If a heuristic misfires, fix the heuristic and record it; never leave the same
  trap for the next agent.


> Handover note for switching the model: [`docs/handover-qwen3.5-9b.md`](docs/handover-qwen3.5-9b.md)

## Architecture

This fork introduces a clean abstraction layer between the application and the graph database:

```
┌─────────────────────────────────────────┐
│              Flask API                   │
│  graph.py  simulation.py  report.py     │
└──────────────┬──────────────────────────┘
               │ app.extensions['neo4j_storage']
┌──────────────▼──────────────────────────┐
│           Service Layer                  │
│  EntityReader  GraphToolsService         │
│  GraphMemoryUpdater  ReportAgent         │
└──────────────┬──────────────────────────┘
               │ storage: GraphStorage
┌──────────────▼──────────────────────────┐
│         GraphStorage (abstract)          │
│              │                            │
│    ┌─────────▼─────────┐                │
│    │   Neo4jStorage     │                │
│    │  ┌───────────────┐ │                │
│    │  │ EmbeddingService│ ← Ollama       │
│    │  │ NERExtractor   │ ← Ollama LLM   │
│    │  │ SearchService  │ ← Hybrid search │
│    │  └───────────────┘ │                │
│    └───────────────────┘                │
└─────────────────────────────────────────┘
               │
        ┌──────▼──────┐
        │  Neo4j CE   │
        │  5.15       │
        └─────────────┘
```

**Key design decisions:**

- `GraphStorage` is an abstract interface — swap Neo4j for any other graph DB by implementing one class
- Dependency injection via Flask `app.extensions` — no global singletons
- Hybrid search: 0.7 × vector similarity + 0.3 × BM25 keyword search
- Synchronous NER/RE extraction via local LLM (replaces Zep's async episodes)
- All original dataclasses and LLM tools (InsightForge, Panorama, Agent Interviews) preserved

## Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB |
| VRAM (GPU) | 10 GB (14b model) | 24 GB (32b model) |
| Disk | 20 GB | 50 GB |
| CPU | 4 cores | 8+ cores |

CPU-only mode works but is significantly slower for LLM inference. For lighter setups, use `qwen2.5:14b` or `qwen2.5:7b`.

## Use Cases

- **PR crisis testing** — simulate the public reaction to a press release before publishing
- **Trading signal generation** — feed financial news and observe simulated market sentiment
- **Policy impact analysis** — test draft regulations against simulated public response
- **Creative experiments** — someone fed it a classical Chinese novel with a lost ending; the agents wrote a narratively consistent conclusion

## License

AGPL-3.0 — same as the original MiroFish project. See [LICENSE](./LICENSE).

## Credits & Attribution

This is a modified fork of [MiroFish](https://github.com/666ghj/MiroFish) by [666ghj](https://github.com/666ghj), originally supported by [Shanda Group](https://www.shanda.com/). The simulation engine is powered by [OASIS](https://github.com/camel-ai/oasis) from the CAMEL-AI team.

**Modifications in this fork:**
- Backend migrated from Zep Cloud to local Neo4j CE 5.15 + Ollama
- Entire frontend translated from Chinese to English (20 files, 1,000+ strings)
- All Zep references replaced with Neo4j across the UI
- Rebranded to MiroFish Offline
