"""
Summarize one session of the beauty, trust or ultimatum oTree app against
its game-theory benchmark (public goods: analyze_pgg.py, PD: analyze_session.py).

  beauty     mean / median guess per round (equilibrium 0; humans ~35 in
             round 1, then falling)
  trust      mean sent (of 10), mean returned share of what arrived
             (equilibrium: 0 sent, 0 returned)
  ultimatum  mean offer (of 20), acceptance rate, rejections by offer size
             (equilibrium: minimal offer, always accepted)

Usage:
    python analyze_games.py --game trust --otree-csv export/trust_custom.csv [--sim-dir <sim_dir>] [--json]
"""

import argparse
import csv
import json
import os
from collections import defaultdict
from statistics import mean, median


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def by_round(rows):
    out = defaultdict(list)
    for r in rows:
        out[int(r["round_number"])].append(r)
    return dict(sorted(out.items()))


def beauty(rows):
    rounds = []
    for rnd, rs in by_round(rows).items():
        g = [int(r["guess"]) for r in rs]
        rounds.append(dict(round=rnd, n=len(g), mean=round(mean(g), 2), median=median(g),
                           zero=sum(x == 0 for x in g), above_67=sum(x > 67 for x in g),
                           missing=sum(r["decision_missing"] == "1" for r in rs)))
    return dict(rounds=rounds, first_mean=rounds[0]["mean"], last_mean=rounds[-1]["mean"],
                equilibrium_share=round(sum(int(r["guess"]) == 0 for r in rows) / len(rows), 3))


def trust(rows):
    rounds = []
    senders = [r for r in rows if r["role"] == "1"]
    for rnd, rs in by_round(senders).items():
        sent = [int(r["sent"]) for r in rs]
        shares = [int(r["returned"]) / int(r["received"]) for r in rs if int(r["received"]) > 0]
        rounds.append(dict(round=rnd, pairs=len(rs), mean_sent=round(mean(sent), 2),
                           sent_zero=sum(x == 0 for x in sent), sent_all=sum(x == 10 for x in sent),
                           mean_return_share=round(mean(shares), 3) if shares else None,
                           sender_mean_payoff=round(mean(float(r["payoff"]) for r in rs), 2)))
    missing = sum(r["decision_missing"] == "1" for r in rows)
    return dict(rounds=rounds, missing=missing, first_sent=rounds[0]["mean_sent"],
                last_sent=rounds[-1]["mean_sent"],
                last_round_return_share=rounds[-1]["mean_return_share"])


def ultimatum(rows):
    rounds = []
    proposers = [r for r in rows if r["role"] == "1"]
    rejected_offers = []
    for rnd, rs in by_round(proposers).items():
        offers = [int(r["offer"]) for r in rs]
        acc = [r["accepted"] == "1" for r in rs]
        rejected_offers += [o for o, a in zip(offers, acc) if not a]
        rounds.append(dict(round=rnd, pairs=len(rs), mean_offer=round(mean(offers), 2),
                           min_offer=min(offers), max_offer=max(offers),
                           acceptance_rate=round(sum(acc) / len(acc), 3)))
    missing = sum(r["decision_missing"] == "1" for r in rows)
    return dict(rounds=rounds, missing=missing, rejected_offers=sorted(rejected_offers),
                mean_offer_share=round(mean(int(r["offer"]) for r in proposers) / 20, 3))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--game", required=True, choices=["beauty", "trust", "ultimatum"])
    p.add_argument("--otree-csv", required=True)
    p.add_argument("--sim-dir")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    rows = list(csv.DictReader(open(args.otree_csv, encoding="utf-8")))
    sessions = sorted({r["session_code"] for r in rows})
    if len(sessions) != 1:
        raise SystemExit(f"expected one session, found {sessions}")
    s = dict(session=sessions[0], **{"beauty": beauty, "trust": trust, "ultimatum": ultimatum}[args.game](rows))
    if args.sim_dir:
        game_dir = os.path.join(args.sim_dir, "game", sessions[0])
        comp = load_jsonl(os.path.join(game_dir, "comprehension.jsonl"))
        s["comprehension_correct"] = f"{sum(1 for c in comp if c.get('correct'))}/{len(comp)}" if comp else None
        s["failures"] = [e for e in load_jsonl(os.path.join(game_dir, "bridge_log.jsonl"))
                         if str(e.get("event", "")).endswith("failed")]
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    rounds = s.pop("rounds")
    print(f"{args.game} session {s.pop('session')}")
    keys = list(rounds[0])
    print("  ".join(f"{k:>12}" for k in keys))
    for r in rounds:
        print("  ".join(f"{str(r[k]):>12}" for k in keys))
    for k, v in s.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
