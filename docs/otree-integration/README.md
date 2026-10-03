# oTree × MiroFish-Offline — Technical Specification

**日本語版: [README.ja.md](README.ja.md)** · Implementation & results report: [REPORT.md](REPORT.md) / [report.html](report.html), second report (Japanese) [REPORT_2026-10.md](REPORT_2026-10.md) / [report_2026-10.html](report_2026-10.html) · Issue log: [NOTES.md](NOTES.md) · Audio versions (Japanese): [spec](audio/readme_ja.mp3), [report](audio/report_ja.mp3) — scripts in [narration/](narration/)

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

## 11. Technical changes (2026-09-29 to 10-02)

Changes on branch `feat/network-chat-dashboard-channels`, after PRs #3 and #4 of MiroFish-Offline were merged. Pair chat, games and workplace personas are described in §5 and "Games other than the PD" above; this section is an index plus the settings documented nowhere else (the Japanese [README.ja.md](README.ja.md) has the longer text, including the dyad / memory section). History: [NOTES.md](NOTES.md) #47–#56; results: [REPORT_2026-10.md](REPORT_2026-10.md) / [report_2026-10.html](report_2026-10.html) (Japanese).

| Component | Added / changed | Key settings (defaults) |
|---|---|---|
| Pair chat | `chat_turns` (0), `label_unit=pair`, `prompts/game_chat.j2`. **Reference only** (NOTES #51) | `chat_memory_rounds=3`, `chat_max_chars=400`; session configs `pd_debate_llm_chat`, `_chat_debate`, `_nochat` |
| Prompts v2 | Decision and chat prompts say: not a social-media post, points are real rewards, symbols carry no meaning, name your choice and give a reason | `prompts/_game_header.j2`, `_game_footer.j2`, `game_*.j2`, `comprehension_*.j2`; do not edit templates during a run |
| Model / env | Main model gemma4:26b (MoE, with thinking) | `.env`: `LLM_MODEL_NAME=gemma4:26b`, `MODEL_TIMEOUT=600`; `LLM_REASONING_EFFORT=none` switches thinking off (12b needs it, but then always picks the first-listed option, NOTES #49) |
| Games registry | `backend/app/services/games.py`: `pgg`, `beauty`, `trust`, `ultimatum`; second movers via `/stage_complete` | override with `game_params` (pgg: endowment 20, ×1.6, groups of 4; beauty: p=2/3, 0–100, prize 20; trust: 10, ×3; ultimatum: pie 20) |
| Workplace personas | `make_workplace_sim.py`, `personas_meta.json` `agent_order` | oTree env `MF_AGENT_IDS` (JSON list) seats agents; first 16 of `agent_order`: `[3,4,6,14,1,7,8,17,5,9,10,13,0,2,11,12]` |
| Persona channels | `add_channels.py` (no LLM, ~2 s), `workplace_channels.json` taxonomy | per-person level for every channel (ignore / skim / read / act) and habit for every medium; `--src`, `--out` (new dir), `--taxonomy`, `--seed`, `--max-chars 420`, `--verify-only` |
| Network chat | `net_*` settings (below), `app/services/network_chat.py`, `prompts/game_network_chat.j2` | one-to-one talk with graph neighbours who are not the PD partner / pgg group mates |
| Channel-compatible dyads | `net_channels`, `net_channel_beta`, `net_reply_model`, `net_channel_topic_weight`, `net_channel_prompt`, `net_pair_chat_model`, `net_dyad_hooks`; `dyads.json` / `dyads.jsonl` | `false`, `0.0`, `always`, `0.5`, `none`, `always` (`compat` is reference only), `""`; `DYAD_HOOKS` is empty |
| Memory | `net_memory_mode`, `net_memory_half_life`, `net_memory_budget_chars`, `net_memory_summary`, `memory_shown.jsonl`, `prompts/_memory.j2` | `window`, `2.0`, `3200`, `extract` (`llm` not implemented) |
| Analysis / dashboards | see below | |
| oTree side | `MF_BRIDGE_SEED`, `network_transcript` column, `pd_net_*` / `pgg_net_*` session configs | see the MiroFish-oTree README |
| Tests | `backend/tests/` | see below |

### Network chat settings (`bridge_net_<name>`, NOTES #54)

Off by default (`net_topology=none`; prompts, contacts and logs are byte-identical to before).

| Setting | Default | Meaning |
|---|---|---|
| `net_topology` | `none` | `none`, `er`, `ba`, `ws`, `ring` (PD and pgg only) |
| `net_mean_degree` | `4.0` | er: round(N·k/2) edges; ba: m=max(1, round(k/2)); ws / ring: even k ≥ 2 |
| `net_ws_p` | `0.1` | WS rewiring probability |
| `net_seed` | `-1` | -1 = `seed`; graph and contact rates never depend on the session code |
| `net_exclude_partners` | `true` | no edge to the PD partner / pgg group members (#51) |
| `net_contact_mean` / `net_contact_dispersion` | `1.0` / `0.5` | μ / r: λ_i ~ Gamma(r, μ), conversations started k_i ~ Poisson(λ_i) (negative binomial); r ≤ 0 gives λ = μ |
| `net_lambda_assign` | `random` | `degree`: largest λ to the highest degree |
| `net_max_initiate` / `net_max_load` | `3` / `4` | conversations started per round / started + received |
| `net_turns` | `2` | messages per conversation (run in waves) |
| `net_memory_rounds` / `net_max_convs_in_prompt` | `2` / `8` | earlier rounds / conversations shown in `window` memory |
| `net_identity` | `profile` | `anon`: "Participant 7" instead of names |
| `label_order_per_agent` | `false` | symbol mapping shared per session, but each agent lists the options in its own random order (position bias, #49); `true` in all network / channel runs including controls |

Dyad / memory semantics: A_ij = w·topic + (1−w)·medium (`net_channel_topic_weight`=w); neighbour choice weight exp(β·z); `net_reply_model=reach` draws medium and reply per conversation, an unanswered conversation shows the initiator one message and the responder nothing; `decay` memory weights items w = 2^(−Δ/h), shown verbatim (w ≥ 0.5), first sentence (≥ 0.25), one-line gist (≥ 0.1), else a per-partner aggregate, demoted only when over `net_memory_budget_chars`. Outputs in `<sim_dir>/game/<session>/`: `network.json`, `network_contacts.jsonl`, `network_chat.jsonl`, `dyads.json`, `dyads.jsonl`, `memory_shown.jsonl`, plus `network_built` / `network_phase` / `network_failed` events in `bridge_log.jsonl`; oTree CSV column `network_transcript` (pd_debate).

### Analysis (`backend/scripts/experiment/`)

| Script | Purpose |
|---|---|
| `analyze_session.py` | one PD session (cooperation, conditional cooperation, first-listed share, pair chat) |
| `analyze_pgg.py` | public goods: decline, end_game_drop, trend_per_round, within-person conditional_slope, nash_share |
| `analyze_games.py --game beauty\|trust\|ultimatum` | comparison with the game-theory benchmark |
| `analyze_network.py --otree-csv … --sim-dir … [--session] [--json]` | network stats, Spearman, negative-binomial check, exposure tables, edge concordance (1000 permutations), talk quality, dyads and memory sections |
| `collect_results.py [--manifest results_manifest.json] [--out-dir docs/otree-integration/results]` | calls the analyzers as libraries and writes `results/results.json` and `results_rounds.csv`; no LLM, no servers |
| `dashboard.py --otree-csv … [--sim-dir] [--session] [--baseline-csv] [--out] [--title]` | static HTML for one run (Altair / Vega-Lite; vega, vega-lite and vega-embed come from jsdelivr) |
| `dashboard_compare.py --run LABEL=CSV[:SIMDIR][@SESSION] … [--out] [--title]` | comparison page for several conditions |
| `preview_channel_contacts.py` | dry check of compatibility and contacts (no LLM) |

### oTree side

`MF_BRIDGE_SEED` (default 0) becomes the session config `bridge_seed` and the bridge `seed` (labels, graph, contact rates, contact draws). The LLM's own sampling is not seeded, so equal seeds do not give equal results (used for replication runs).

### Tests

No LLM, no servers (fake client):

```sh
cd backend && .venv/bin/python -m pytest tests -q
```

`test_network_chat.py` (contacts and prompts; with the network off they must match the golden files `golden_*.json`), `test_channel_dyads.py`, `test_memory.py`, `test_add_channels.py`, `test_dashboard.py`, `test_dashboard_compare.py`. Tests that need a real simulation directory are skipped when it is absent (115 tests at NOTES #55, 9 skipped).


## 12. Technical changes (2026-10-02 to 10-03)

Branch `feat/dyad-betrayal-reputation` adds betrayal, revealed choices and reputation to the dyads. **Everything is off by default**; with the defaults every prompt and log is byte-identical to before (`backend/tests/golden_dyads_run.json`). Design, event definitions and how to run: [betrayal_reputation.md](betrayal_reputation.md); history: [NOTES.md](NOTES.md) #57 to #59; results: [REPORT_2026-10-03.md](REPORT_2026-10-03.md) / [report_2026-10-03.html](report_2026-10-03.html) (Japanese).

| Setting (`bridge_<name>`) | Default | Meaning |
|---|---|---|
| `net_dyad_hooks` | `""` | `betrayal`, `reputation` (comma list). `betrayal` writes word events (announced vs actual choice) and game events (PD exploits, pgg against the announced amount) to `dyads.jsonl` each round |
| `net_reveal_choices` | `none` | `talked`: what each person you talked with chose is put in your memory for the next round ("What you were told after earlier rounds"). `pair`: also what their game partner chose (pgg: their group's contributions). Announced at the start of the conversation. Needs `net_memory_mode=decay` |
| `net_betrayal_salience` | `1.0` | Memory weight s of the notes and conversations that show a mismatch (broken word, exploit) |
| `net_reputation_word_weight` / `net_reputation_choice_weight` | `0.0` / `0.0` | rho_w / rho_c. Neighbour choice weight times exp(rho_w(2W-1)+rho_c(2B-1)); W = share of kept words, B = share of revealed choices that were cooperative (Beta posterior means), both from what the chooser was told |

| New file | Role |
|---|---|
| `backend/app/services/betrayal.py` | Rules (pure, stdlib only): `stated_intention`, `word_events`, `game_events_pd` / `_pgg`, `beta_score`, `contact_multiplier` |
| `backend/app/services/dyad_hooks.py` | `DYAD_HOOKS['betrayal']`, `['reputation']`, run under the bridge lock in `round_complete` |
| `backend/scripts/experiment/analyze_betrayal.py` | Recomputes every event from the logs (no LLM): kept words, exploits, spillover, partner selection (selection / initiation_shares), honesty of self-reports, gossip (third-party mentions); checks online == offline. In runs without reveal the "would-have-been-told" facts are placebos |
| `backend/tests/test_betrayal.py` | Rules, hooks, byte identity with defaults (189 tests in total) |

oTree configs: `pd_net_ba_ch_mem_rev` (`pair`, s=2), `pd_net_ba_ch_mem_rev_talked`, `pd_net_ba_ch_mem_rep` (+ rho_w=rho_c=2), `pgg_net_ba_ch_mem_rev` / `_rep`. Replications use `MF_BRIDGE_SEED=<seed>` and `MF_AGENT_IDS`.

**Operational note**: LLM sampling has no seed, so round-1 cooperation differs by about 0.2 under identical settings and the difference persists (Spearman rho=0.84 between round 1 and rounds 6-10 over 33 runs, NOTES #59). Compare conditions over several seeds, with round 1 as a covariate or on the change after round 1.
