"""
Summarize one public goods game session (oTree app pgg).

Reads the oTree custom export (pgg_custom.csv, authoritative) and, if given,
<sim_dir>/game/<session_code>/{bridge_log,comprehension,beliefs}.jsonl.

Benchmarks: with MPCR < 1 the dominant strategy and the subgame-perfect
equilibrium are zero contribution in every round. Human groups typically
start at 40-60 % of the endowment, decline over rounds, and drop further in
the last round; most people are conditional cooperators (contribute more
when the others contributed more).

Usage:
    python analyze_pgg.py --otree-csv export/pgg_custom.csv [--sim-dir <sim_dir>] [--json]
"""

import argparse
import csv
import json
import os
from collections import defaultdict


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx, 3)


def summarize(otree_csv, sim_dir=None, endowment=20):
    rows = list(csv.DictReader(open(otree_csv, encoding="utf-8")))
    sessions = sorted({r["session_code"] for r in rows})
    if len(sessions) != 1:
        raise SystemExit(f"expected one session, found {sessions}")
    session = sessions[0]
    for r in rows:
        r["c"] = int(r["contribution"])
        r["others"] = json.loads(r["others_contributions"])

    by_round = defaultdict(list)
    by_group_round = defaultdict(list)
    for r in rows:
        by_round[int(r["round_number"])].append(r)
        by_group_round[(r["group_id"], int(r["round_number"]))].append(r["c"])

    rounds = []
    for rnd in sorted(by_round):
        cs = [r["c"] for r in by_round[rnd]]
        rounds.append(dict(
            round=rnd, n=len(cs),
            mean=round(sum(cs) / len(cs), 2),
            share_of_endowment=round(sum(cs) / len(cs) / endowment, 3),
            zero=sum(c == 0 for c in cs), full=sum(c == endowment for c in cs),
            missing=sum(r["decision_missing"] == "1" for r in by_round[rnd]),
        ))
    groups = sorted({r["group_id"] for r in rows}, key=int)
    group_means = {g: [round(sum(by_group_round[(g, rr["round"])]) / len(by_group_round[(g, rr["round"])]), 1)
                       for rr in rounds] for g in groups}

    # Conditional cooperation: own contribution at t vs others' mean at t-1,
    # demeaned per agent (within-agent), so stable types do not confound it
    hist = {(r["agent_id"], int(r["round_number"])): r for r in rows}
    per_agent = defaultdict(list)
    for (agent, rnd), r in hist.items():
        prev = hist.get((agent, rnd - 1))
        if prev is not None:
            per_agent[agent].append((sum(prev["others"]) / len(prev["others"]), r["c"]))
    xs, ys = [], []
    for pts in per_agent.values():
        mx = sum(x for x, _ in pts) / len(pts)
        my = sum(y for _, y in pts) / len(pts)
        xs += [x - mx for x, _ in pts]
        ys += [y - my for _, y in pts]
    first, last = rounds[0]["mean"], rounds[-1]["mean"]
    before_last = rounds[-2]["mean"] if len(rounds) > 1 else None

    out = dict(session=session, rounds=rounds, group_means=group_means,
               first_round_mean=first, last_round_mean=last,
               decline=round(first - last, 2),
               end_game_drop=round(before_last - last, 2) if before_last is not None else None,
               trend_per_round=slope([r["round"] for r in rounds], [r["mean"] for r in rounds]),
               conditional_slope=slope(xs, ys),
               nash_share=round(sum(r["c"] == 0 for r in rows) / len(rows), 3))
    if sim_dir:
        game_dir = os.path.join(sim_dir, "game", session)
        comp = load_jsonl(os.path.join(game_dir, "comprehension.jsonl"))
        beliefs = load_jsonl(os.path.join(game_dir, "beliefs.jsonl"))
        out["comprehension_correct"] = f"{sum(1 for c in comp if c.get('correct'))}/{len(comp)}" if comp else None
        out["beliefs"] = {}
        for phase in ("pre", "post"):
            s = [b["score"] for b in beliefs if b.get("phase") == phase and b.get("score") is not None]
            out["beliefs"][phase] = round(sum(s) / len(s), 3) if s else None
        log = load_jsonl(os.path.join(game_dir, "bridge_log.jsonl"))
        out["failures"] = [e for e in log if str(e.get("event", "")).endswith("failed")]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--otree-csv", required=True)
    p.add_argument("--sim-dir")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    s = summarize(args.otree_csv, args.sim_dir)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"session {s['session']}  comprehension {s.get('comprehension_correct')}  beliefs {s.get('beliefs')}")
    print("round   n   mean  share  zero  full  missing")
    for r in s["rounds"]:
        print(f"{r['round']:>5} {r['n']:>3} {r['mean']:>6} {r['share_of_endowment']:>6} {r['zero']:>5} "
              f"{r['full']:>5} {r['missing']:>8}")
    print("group means by round:", s["group_means"])
    for k in ("first_round_mean", "last_round_mean", "decline", "end_game_drop", "trend_per_round",
              "conditional_slope", "nash_share"):
        print(f"{k}: {s[k]}")
    if s.get("failures"):
        print("FAILURES:", s["failures"])


if __name__ == "__main__":
    main()
