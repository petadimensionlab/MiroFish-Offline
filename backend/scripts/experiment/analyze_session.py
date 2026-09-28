"""
Summarize one oTree x MiroFish experiment session.

Reads
  - the oTree custom export (pd_debate_custom.csv) -- authoritative behavior
  - <sim_dir>/game/<session_code>/llm_answers.jsonl, bridge_log.jsonl
  - <sim_dir>/twitter/actions.jsonl -- discourse during debate rounds

Usage:
    python analyze_session.py --otree-csv export/pd_debate_custom.csv \
        --sim-dir <sim_dir> [--session <code>] [--json]
"""

import argparse
import csv
import json
import os
from collections import Counter, defaultdict


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize(otree_csv, sim_dir, session=None):
    rows = list(csv.DictReader(open(otree_csv, encoding="utf-8")))
    if session:
        rows = [r for r in rows if r["session_code"] == session]
    sessions = sorted({r["session_code"] for r in rows})
    if len(sessions) != 1:
        raise SystemExit(f"expected one session in the CSV, found {sessions}; pass --session")
    session = sessions[0]

    by_round = defaultdict(list)
    for r in rows:
        by_round[int(r["round_number"])].append(r)

    rounds = []
    for rnd in sorted(by_round):
        rs = by_round[rnd]
        pairs = defaultdict(list)
        for r in rs:
            pairs[r["pair_id"]].append(r["choice"])
        outcome = Counter("".join(sorted(c)) for c in pairs.values())
        rounds.append(dict(
            round=rnd,
            n=len(rs),
            cooperation_rate=round(sum(r["cooperated"] == "1" for r in rs) / len(rs), 3),
            mutual_cooperation=outcome.get("AA", 0),
            mutual_defection=outcome.get("BB", 0),
            split=outcome.get("AB", 0),
            missing=sum(r["decision_missing"] == "1" for r in rs),
            mean_payoff=round(sum(float(r["payoff"]) for r in rs) / len(rs), 2),
        ))

    # Conditional cooperation: P(cooperate at t | partner cooperated at t-1)
    history = {(r["agent_id"], int(r["round_number"])): r for r in rows}
    after = {"A": [0, 0], "B": [0, 0]}
    for (agent, rnd), r in history.items():
        prev = history.get((agent, rnd - 1))
        if prev is None:
            continue
        slot = after[prev["partner_choice"]]
        slot[0] += r["cooperated"] == "1"
        slot[1] += 1
    conditional = {f"after_partner_{k}": (round(v[0] / v[1], 3) if v[1] else None)
                   for k, v in after.items()}

    sources = Counter(r["decision_source"] for r in rows)

    game_dir = os.path.join(sim_dir, "game", session)
    answers = load_jsonl(os.path.join(game_dir, "llm_answers.jsonl"))
    bridge_log = load_jsonl(os.path.join(game_dir, "bridge_log.jsonl"))
    llm = dict(
        answers=len(answers),
        parse_errors=sum(1 for a in answers if a.get("parse_error")),
        strict_retries=sum(1 for a in answers if a.get("strict")),
        mean_feed_posts=(round(sum(a.get("feed_posts") or 0 for a in answers) / len(answers), 2)
                         if answers else None),
    )
    mismatches = sum(len(e.get("mismatches", [])) for e in bridge_log if e.get("event") == "round_complete")
    debate = [e for e in bridge_log if e.get("event") == "debate_phase"]
    failures = [e for e in bridge_log if e.get("event") in ("debate_failed", "prefetch_failed")]

    actions = load_jsonl(os.path.join(sim_dir, "twitter", "actions.jsonl"))
    game_agents = {int(r["agent_id"]) for r in rows}
    # round 0 holds the config's initial posts, not debate
    real = [a for a in actions if "action_type" in a and a.get("round", 0) > 0
            and not a.get("action_args", {}).get("injected")]
    injected = [a for a in actions if "action_type" in a and a.get("action_args", {}).get("injected")]
    discourse = dict(
        spontaneous_actions=len(real),
        by_game_agents=sum(1 for a in real if a.get("agent_id") in game_agents),
        action_types=dict(Counter(a["action_type"] for a in real)),
        injected_posts=len(injected),
        debate_phases=len(debate),
        debate_seconds=round(sum(e.get("elapsed_sec", 0) for e in debate), 1),
    )

    return dict(session=session, rounds=rounds, conditional_cooperation=conditional,
                decision_sources=dict(sources), llm=llm, mismatches=mismatches,
                failures=failures, discourse=discourse)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--otree-csv", required=True)
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--session")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    s = summarize(args.otree_csv, args.sim_dir, args.session)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"session {s['session']}")
    print("round  n  coop  AA  BB  AB  missing  mean_payoff")
    for r in s["rounds"]:
        print(f"{r['round']:>5} {r['n']:>2} {r['cooperation_rate']:>5.2f} {r['mutual_cooperation']:>3} "
              f"{r['mutual_defection']:>3} {r['split']:>3} {r['missing']:>8} {r['mean_payoff']:>12}")
    print("conditional cooperation:", s["conditional_cooperation"])
    print("decision sources:", s["decision_sources"])
    print("llm:", s["llm"], "mismatches:", s["mismatches"])
    print("discourse:", s["discourse"])
    if s["failures"]:
        print("FAILURES:", s["failures"])


if __name__ == "__main__":
    main()
