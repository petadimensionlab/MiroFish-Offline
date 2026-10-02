# Betrayal, revealed choices and reputation (NOTES #57)

Design, settings and how to run. Everything is off by default; with the new
settings at their defaults every prompt and log is byte-identical to before
(`backend/tests/golden_dyads_run.json`, `test_betrayal.py::test_golden_files_unchanged_with_defaults`).

## What it adds

| Piece | File | Role |
|---|---|---|
| Rules | `backend/app/services/betrayal.py` | Pure, stdlib only. `stated_intention`, `word_events`, `game_events_pd`, `game_events_pgg`, `beta_score`, `contact_multiplier`. |
| Hooks | `backend/app/services/dyad_hooks.py` | `DYAD_HOOKS['betrayal']`, `['reputation']`. Run under the bridge lock in `round_complete`. |
| Notes | `backend/app/services/memory.py` | `MemoryItem(kind='note')` rendered as "What you were told after earlier rounds:". |
| Contacts | `ExperimentBridge._contact_dyad` | Reputation-weighted neighbour choice. |
| Analysis | `backend/scripts/experiment/analyze_betrayal.py` | Recomputes events from any run's logs. Called by `analyze_network.py`; section in `dashboard.py`, columns in `dashboard_compare.py`. |

## Events

* **word**: the speaker's own messages in an answered conversation; one sentence that announces a choice
  (INTENT, or a weak verb after a first-person marker; no HEDGE; exactly one option named in the part
  from the announcement up to a contrast word such as "avoid" / "than" / "but"). PD: kept = stated == chose;
  pgg: kept = chose >= stated, deficit = stated - chose. `INTENT_RULE_VERSION = 1`, written to `dyads.json` method.
* **game** (PD): partner chose B while the victim chose A. `break` = at least one mutual-A round right before,
  `repeat` = the partner also chose B the round before, else `first`. Plus an `exploit` event for the exploiter.
* **game** (pgg): a member who gave at least the mean of the others last round and gives less than that mean
  and less than before now (`drop`, with its victims); `exploit` = below the mean of the others.
* Agents whose outcome is `missing` or from `llm_default` / `default` are skipped.

## Settings (`BridgeSettings`, oTree `bridge_net_*`)

| Setting | Default | Meaning |
|---|---|---|
| `net_dyad_hooks` | `''` | `betrayal` (events, per-agent figures, reveal), `betrayal,reputation` (adds scores). `reputation` needs `betrayal` before it. |
| `net_reveal_choices` | `none` | `talked`: after a round each agent is told what each person it talked with chose. `pair`: plus what that person's partner (pgg: the others in that person's group, amounts sorted descending, no identities) chose. Needs hook `betrayal`, `net_topology != none`, `net_memory_mode decay`. |
| `net_betrayal_salience` | `1.0` | s >= 1. Salience of a note / conversation that shows a broken word or (pair) an exploit; weight `2^(-delta / (h s))`. Needs a reveal. |
| `net_reputation_word_weight`, `net_reputation_choice_weight` | `0.0` | rho_w, rho_c >= 0. Neighbour weight is multiplied by `exp(rho_w (2W-1) + rho_c (2B-1))`. Need hook `reputation`, a reveal and `net_channels`. W = Beta score of the words j kept to i, B = Beta score of j's revealed choices being cooperative (pgg: share of the endowment), both only from revealed facts. |

Scores never appear in a prompt. Reply probabilities are not touched by reputation.

## Prompts (reveal on only)

* Decision prompt, inside the memory block, one line: "After each round, you and each person you talked with
  before it are told which option the other [and the other's partner] chose." (pgg: "how much the other [and the others in the other's group] put in").
* Network chat prompt, after "You do not talk with ...": "After this round, you will each be told which option
  the other [and the other's partner] chose in round N." (the speaker who starts: "If X replies, after this round you will each ...").
* Memory block, last section: one line per note down to the gist tier ("- After round 3, Ken chose ◇; Ken's partner chose △."),
  one line per person below it ("- Ken, after rounds 1-4: chose ◇ 3 times, △ once."). Under budget pressure the note
  aggregates are dropped oldest first, with the key `('note', person)`.

## Logs

* `dyads.jsonl` (hooks only): per round `events` and `agents` ({agent: {stated, kept, consistency, coop, exploits}}).
  Dyad `ext`: `game` (on victim, by), `word` (on speaker, listener), `rep` (what i knows about j; reveal on) or
  `latent_rep` (reveal off: the placebo, never used).
* `network_contacts.jsonl`: `reputation_weights` {i: {j: factor}} in rounds where a factor differs from 1.
* `memory_shown.jsonl`: items with `kind: 'note'`.

## Analysis

```
backend/.venv/bin/python backend/scripts/experiment/analyze_betrayal.py <sim_dir>/game/<session> [--json]
backend/.venv/bin/python backend/scripts/experiment/analyze_betrayal.py <game dirs> --audit 60 --seed 0   # extractions to read by hand
```

Sections: statements (coverage, status counts, kept rate by round), game events, aftermath (P(coop t+1) after being
betrayed), spillover (P(coop t+1) after being told a partner exploited / broke its word), selection (P(i starts a
conversation with j at t+1) by what i could know about j), initiation shares against base / reputation-weighted
expectation, speakers, honesty (past-tense self-reports against the record), gossip. With the reveal off the "told"
facts are latent, so spillover and selection are **placebos** and should be null; compare *bad* against *good*, not
against *not told* (pairs that talked once tend to talk again through the contact weights).

## G1: the extractor on the 7 archived PD runs

chbamem, chba, chba0, netba, neter, netws, netring: 1286 speaker-conversations, 1209 statements (coverage 94.0%),
1204 kept, 5 broken. The first rule (the plan's INTENT list) had coverage 84.8% and 1091 statements; hand audit of
60 random extractions found no wrong value, but showed that "selecting X" and "switch to / return to X" matched
reports of what somebody did ("by selecting X", "my partner's switch to X"). Tightened: those weak verbs count only after
a first-person marker, "selecting" dropped. Widened: an adverb after the subject ("I also intend"), "I'm sticking with",
"I've decided to", `$\diamond$`-style symbols, and the clause after a contrast word ("to avoid the volatility of Y") is not
part of the announced choice. A second random sample of 60 (seed 1) was 60/60 and all 126 extractions added by
widening, and the 8 dropped by tightening, were read by hand: all correct.

## Running

oTree configs (`mirofish_otree_test/settings.py`): `pd_net_ba_ch_mem_rev` (pair reveal, salience 2), `pd_net_ba_ch_mem_rev_talked`,
`pd_net_ba_ch_mem_rep` (reveal + rho_w = rho_c = 2), `pgg_net_ba_ch_mem_rev`, `pgg_net_ba_ch_mem_rep`. The control is `pd_net_ba_ch_mem` /
`pgg_net_ba_ch_mem`; any existing run is a reveal-off arm via `analyze_betrayal.py`. Planned queue and primary outcomes: NOTES #57.

Tests: `cd backend && .venv/bin/python -m pytest tests -q` (all offline, fake client).
