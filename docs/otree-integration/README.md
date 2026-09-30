# oTree × MiroFish-Offline — Technical Specification

**日本語版: [README.ja.md](README.ja.md)** · Implementation & results report: [REPORT.md](REPORT.md) / [report.html](report.html) · Issue log: [NOTES.md](NOTES.md) · Audio versions (Japanese): [spec](audio/readme_ja.mp3), [report](audio/report_ja.mp3) — scripts in [narration/](narration/)

MiroFish LLM agents play an oTree iterated prisoner's dilemma. Between game rounds the agents debate on a simulated social network (OASIS), so discourse and behavior can feed back into each other.

## 1. Components

| Component | Location | License | Role |
|---|---|---|---|
| oTree app `pd_debate` | [`MiroFish-oTree`](https://github.com/petadimensionlab/MiroFish-oTree) (separate public repo): `mirofish_otree_test/pd_debate/` | MIT | The game: pairing, payoffs, records. Bots ask the bridge for decisions |
| Bridge | `backend/app/api/experiment.py`, `backend/app/services/experiment_bridge.py` | AGPL-3.0 | Decision policies, prefetch, debate phase, reconciliation, logs |
| Decision prompts | `backend/app/services/game_decision.py`, `backend/app/prompts/*.j2` | AGPL-3.0 | Labels, prompt rendering, answer parsing |
| Step-server | `backend/scripts/run_experiment_env.py`, `backend/scripts/experiment/` | AGPL-3.0 | OASIS environment advanced round by round over file IPC |
| Runner refactor (R1) | `backend/scripts/run_parallel_simulation.py` | AGPL-3.0 | `setup_platform_env`, `publish_initial_posts`, `step_round` |
| Tools | `backend/scripts/experiment/analyze_session.py`, `make_general_sim.py` | AGPL-3.0 | Session metrics; general-public persona generator |

```
oTree bot ──HTTP──▶ Flask bridge ──file IPC──▶ step-server (OASIS) ──▶ Ollama
          ◀────────              ◀────────────
```

oTree is authoritative for behavior and payoffs. The bridge never overrides what oTree recorded.

## 2. Game

- Iterated prisoner's dilemma, 2-player fixed pairs, `NUM_ROUNDS = 10`
- Payoffs (session config `pd_payoffs`, default): R=30 (both cooperate), T=50 (defect on a cooperator), S=0 (cooperate against a defector), P=10 (both defect)
- Internal choice values are always `A` = cooperate, `B` = defect. What agents see is set by the bridge's label settings
- Page sequence: `Decide` → `PairWaitPage` (payoffs) → `Results` → `DebateBarrier` (`wait_for_all_groups=True`, fires `/round_complete` once per round)
- `participant.label = "agent_<agent_id>"`; `agent_id` maps to the MiroFish agent (session config `agent_ids`, default `0..N-1`)

## 3. Round cycle (llm policy)

1. `creating_session` → `/configure` with the pair table and settings. The bridge starts computing round 1 for all agents (optional comprehension check, belief survey, opening post and opening debate first)
2. Each bot calls `/decide`; the bridge returns the prefetched decision, waiting if it is still being computed
3. After all pairs submit, `/round_complete` sends what oTree recorded. The bridge, in the background: injects result posts → runs `debate_rounds` debate rounds → prefetches the next round. The next round's in-flight slots are claimed before `/round_complete` returns
4. After the last round: optional post-game belief survey

Prefetch exists because the oTree bot runner is strictly serial; one LLM call per `/decide` would multiply wall time by the number of agents.

## 4. Bridge HTTP API (`/api/experiment`)

Responses are `{"success": bool, "data": ...}`.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/configure` | `session_code`, settings (§5), `agents: [{agent_id, partner_agent_id}]` | effective settings |
| POST | `/decide` | `session_code`, `round_number`, `agent_id`, `history: [{round_number, own, partner, payoff}]` | `choice` (internal A/B), `reason`, `source`, `latency_sec`, `missing`, `cached` |
| POST | `/round_complete` | `session_code`, `round_number`, `n_players`, `cooperation_rate`, `outcomes: [{agent_id, choice, payoff, partner_agent_id, source, missing}]` | `accepted`, `duplicate`, `n_mismatches`, `prefetched_next` |
| GET | `/state/<session_code>` | — | decisions per round, missing per round, in-flight count |

Errors: 400 invalid input, 409 unknown session (never configured, e.g. bridge restarted), 503 injected fault, 500 other.

Guarantees: `/decide` is idempotent per (session, round, agent) and deduplicates concurrent retries; `/round_complete` is idempotent per round; mismatches between bridge decisions and oTree records are logged.

## 5. Bridge settings (`/configure` or oTree session config `bridge_<name>`)

| Setting | Default | Meaning |
|---|---|---|
| `policy` | `random` | `random`, `allc`, `alld`, `tft`, `llm` |
| `simulation_id` / `simulation_dir` | — | Simulation served by the step-server (llm) |
| `platform` | `twitter` | Platform used for interviews and debate |
| `include_feed` | `true` | Put the agent's current feed in the decision prompt |
| `feed_exclude_own` | `false` | Drop the agent's own posts from that feed |
| `label_scheme` | `symbols` | `letters` (A/B) or `symbols` (△□○◇) |
| `label_randomize` | `true` | Draw the label mapping and listing order at random |
| `label_unit` | `session` | `session`, `pair` or `agent`: who shares one random mapping |
| `chat_turns` | `0` | **Keep 0 for the actual experiments** (NOTES #51): the study asks whether discourse on the platform changes behavior, and a direct pair chat lets partners settle their choices between themselves, which leaves nothing for discourse to affect. Chat is a check/reference condition only. Private pair-chat messages before every decision (0 = no chat); needs `label_unit` `session` or `pair`. Transcript goes into both partners' decision prompts, log in `game/<session>/chat.jsonl`, oTree field `chat_transcript` |
| `chat_memory_rounds` / `chat_max_chars` | `3` / `400` | Earlier rounds of chat shown in prompts (-1 = all) / message length cap |
| `swap_labels` | `false` | Letters only, fixed swap (A shown as defect) |
| `debate_rounds` | `0` | Debate rounds between game rounds (0 = no debate) |
| `debate_players_only` | `true` | Only game agents can act in debate rounds |
| `debate_ignore_hours` | `true` | Ignore time-of-day activation in debate rounds |
| `debate_min_active` | `2` | Minimum active agents per debate round |
| `inject_results` | `each` | `each` (each agent posts its result), `summary` (one post), `none` |
| `opening_post` / `repeat_opening` | `""` / `false` | Topic post before round 1 / before every debate phase |
| `comprehension_check` | `false` | Two payoff questions before round 1 |
| `belief_survey` | `false` | 7-point Likert item before round 1 and after the last round |
| `default_choice` | `A` | Internal choice used when an answer is unusable (flagged missing) |
| `no_think` | `auto` | Append qwen3's `/no_think` (auto = qwen3 models only) |
| `payoffs`, `num_rounds` | from oTree | Sent by `creating_session` |
| `seed`, `inject_delay_sec`, `inject_error_rate` | `0` | Reproducibility; fault injection for tests |

The oTree app passes these as `bridge_<setting>` session-config keys (e.g. `bridge_debate_rounds=2`). Registered session configs: `pd_debate`, `pd_debate_faults`, `pd_debate_llm`, `pd_debate_llm_debate`, `pd_debate_llm_debate_topic`, `pd_debate_llm_opening`, `pd_debate_llm_debate_noinject`, `pd_debate_llm_debate_noinject_swap`.

## Games other than the PD

`game` = `pgg` (public goods, groups of 4), `beauty` (p-beauty contest, groups of 4), `trust` (investment game, pairs, sequential), `ultimatum` (pairs, sequential); definitions and defaults in `backend/app/services/games.py`, oTree apps of the same names, `*_llm` session configs (no chat, no debate, no feed). Agents are `[{agent_id, group_agent_ids, role}]`. In sequential games only first movers are prefetched per round; oTree calls `/stage_complete` once they have decided and the bridge prefetches the second movers with the first move in their prompt. Decisions with nothing to decide (returning from 0 sent) are `source='auto'`. Analysis: `analyze_pgg.py`, `analyze_games.py`.

Workplace personas: `make_workplace_sim.py` builds 48 employees of one fictional company group (4 companies × 12 departments, one per cell) who know each other only loosely and owe each other nothing in particular; `personas_meta.json` `agent_order`, passed to oTree as `MF_AGENT_IDS`, seats pairs / groups of 4 in the same company but different departments.

## 6. Step-server IPC commands

Command files in `<sim_dir>/ipc_commands/`, responses in `<sim_dir>/ipc_responses/` (see `experiment/ipc_protocol.py`).

| Command | Args | Effect |
|---|---|---|
| `run_rounds` | `rounds`, `platform`, `agent_ids`, `ignore_active_hours`, `min_active` | Advance k debate rounds via `step_round` |
| `inject_post` | `posts: [{agent_id, content}]`, `platform` | Publish all posts in one `env.step`; logged with `injected: true` |
| `game_interview` | `interviews: [{agent_id, prompt}]`, `platform`, `exclude_own_posts` | Batch interview; `{{FEED}}` replaced per agent with its current feed; returns response and the feed seen |
| `get_state` | `platform` | Round counters, busy flag |
| `interview`, `batch_interview`, `close_env` | — | Inherited from `ParallelIPCHandler` |

The step-server takes `<sim_dir>/step_server.pid` and refuses to start while another live server holds it.

## 7. Decision prompt and parsing

- Payoffs restated option by option: "If you choose △: and the other chooses △ … / □ …"
- Includes the round, the pair's history in shown labels, the agent's feed, and a JSON answer instruction `{"choice": ..., "reason": ...}`
- Parsing strips `<think>` blocks and code fences, reads the first JSON with a valid label, falls back to a `"choice": X` pattern; unusable answers get one strict re-ask, then `default_choice` with `decision_missing=1`

## 8. Outputs

| File | Content |
|---|---|
| `pd_debate_custom.csv` (oTree `--export`) | One row per agent × round: `session_code, participant_code, participant_label, agent_id, round_number, pair_id, id_in_pair, partner_agent_id, choice, cooperated, partner_choice, payoff, decision_source, decision_missing, decision_latency_sec, decision_reason, chat_transcript` |
| `<sim_dir>/game/<session>/bridge_log.jsonl` | configure (incl. labels), decide, round_complete (with mismatches), debate_phase, surveys, failures |
| `<sim_dir>/game/<session>/llm_answers.jsonl` | Every LLM answer: prompt, feed seen, raw response, parse error |
| `<sim_dir>/game/<session>/beliefs.jsonl`, `comprehension.jsonl` | Survey and comprehension answers |
| `<sim_dir>/twitter/actions.jsonl` | Debate actions; injected posts carry `action_args.injected=true` |

Summarize a session:

```sh
python backend/scripts/experiment/analyze_session.py \
    --otree-csv <export>/pd_debate_custom.csv --sim-dir <sim_dir>
```

## 9. Running an LLM session

```sh
# 0. Ollama with parallel slots (lost on reboot)
launchctl setenv OLLAMA_NUM_PARALLEL 8   # then restart the Ollama app
# .env: LLM_MODEL_NAME=gemma4:e4b

# 1. (optional) general-public population
backend/.venv/bin/python backend/scripts/experiment/make_general_sim.py --n 48 --seed 1

# 2. step-server: zero debate rounds at start, first debate round at 9:00
backend/venv311/bin/python backend/scripts/run_experiment_env.py \
    --config <sim_dir>/simulation_config.json --twitter-only --start-hour 9

# 3. bridge (FLASK_DEBUG=false is required: the reloader wipes bridge state)
cd backend && FLASK_DEBUG=false FLASK_PORT=5055 .venv/bin/python run.py

# 4. oTree bots (git clone https://github.com/petadimensionlab/MiroFish-oTree; python -m venv .venv; pip install "otree==6.0.15" requests)
cd MiroFish-oTree/mirofish_otree_test && source ../.venv/bin/activate
MF_BRIDGE_URL=http://127.0.0.1:5055/api/experiment MF_BRIDGE_TIMEOUT=1800 \
MF_SIMULATION_DIR=<sim_dir> otree test pd_debate_llm_debate 8 --export ./export
```

oTree client environment: `MF_BRIDGE_URL` (unset = fixed-strategy bots, no bridge), `MF_BRIDGE_TIMEOUT` (default 10 s; use 1800 for llm), `MF_BRIDGE_ATTEMPTS` (3), `MF_BRIDGE_BACKOFF` (0.5 s × attempt).

## 10. Operational rules

- Stop old processes and confirm they are gone before deleting a simulation's DB (a stopped step-server keeps running its current command and can take the next run's commands)
- Do not edit `backend/` code or prompt templates during a run
- The step-server deletes and recreates `<sim_dir>/twitter_simulation.db` on start; run experiments on a copy of a simulation directory
- The oTree bot runner is serial; concurrent-access safety has not been load-tested
