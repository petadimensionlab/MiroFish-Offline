"""
Summarize one oTree x MiroFish experiment session.

Reads
  - the oTree custom export (pd_debate_custom.csv) -- authoritative behavior
  - <sim_dir>/game/<session_code>/llm_answers.jsonl, bridge_log.jsonl
  - <sim_dir>/game/<session_code>/chat.jsonl -- private pair chat, if any
  - <sim_dir>/twitter/actions.jsonl -- discourse during debate rounds

Usage:
    python analyze_session.py --otree-csv export/pd_debate_custom.csv \
        --sim-dir <sim_dir> [--session <code>] [--json]
"""

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict


# crude topic detector; check samples by hand before relying on it
GAME_TERMS = re.compile(r"\btrust|look out for (your|my)self|same person|decision task|\bpoints\b"
                        r"|the other person|cooperat|betray|selfish", re.I)


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
    configure = next((e for e in bridge_log if e.get("event") == "configure"), {})
    debate = [e for e in bridge_log if e.get("event") == "debate_phase"]
    failures = [e for e in bridge_log if e.get("event") in ("debate_failed", "prefetch_failed")]

    actions = load_jsonl(os.path.join(sim_dir, "twitter", "actions.jsonl"))
    game_agents = {int(r["agent_id"]) for r in rows}
    # round 0 holds the config's initial posts, not debate
    real = [a for a in actions if "action_type" in a and a.get("round", 0) > 0
            and not a.get("action_args", {}).get("injected")]
    injected = [a for a in actions if "action_type" in a and a.get("action_args", {}).get("injected")]
    def text(a):
        args = a.get("action_args", {})
        return " ".join(filter(None, [args.get("content"), args.get("quote_content")]))

    # posts the bridge wrote: result posts and the opening/topic post
    seeded = {a["action_args"].get("content") for a in injected}
    engaged = [a for a in real if a.get("action_args", {}).get("original_content") in seeded]
    on_topic = [a for a in real if GAME_TERMS.search(text(a))]
    discourse = dict(
        spontaneous_actions=len(real),
        with_text=sum(1 for a in real if text(a).strip()),
        engaging_seeded_posts=len(engaged),
        mentioning_game_terms=len(on_topic),
        by_game_agents=sum(1 for a in real if a.get("agent_id") in game_agents),
        action_types=dict(Counter(a["action_type"] for a in real)),
        injected_posts=len(injected),
        debate_phases=len(debate),
        debate_seconds=round(sum(e.get("elapsed_sec", 0) for e in debate), 1),
    )

    comprehension = load_jsonl(os.path.join(game_dir, "comprehension.jsonl"))
    beliefs = load_jsonl(os.path.join(game_dir, "beliefs.jsonl"))
    belief_means = {}
    for phase in ("pre", "post"):
        scores = [b["score"] for b in beliefs if b.get("phase") == phase and b.get("score") is not None]
        belief_means[phase] = round(sum(scores) / len(scores), 3) if scores else None

    chat = summarize_chat(load_jsonl(os.path.join(game_dir, "chat.jsonl")), configure.get("labels"), history)

    return dict(session=session, labels=configure.get("labels"),
                model=configure.get("settings", {}).get("policy"),
                comprehension_correct=(f"{sum(1 for c in comprehension if c.get('correct'))}/{len(comprehension)}"
                                       if comprehension else None),
                beliefs=belief_means, rounds=rounds, conditional_cooperation=conditional,
                decision_sources=dict(sources), llm=llm, mismatches=mismatches,
                failures=failures, discourse=discourse, chat=chat)


def summarize_chat(records, labels, history):
    """Pair chat: how much was said, whether it was about the game, and
    whether what an agent said it would choose matched what it chose.

    The stated intention is crude: an agent's last message of the round that
    names exactly one of its two option labels. Check samples by hand.
    """
    if not records:
        return None
    final = {}
    for rec in records:  # the retry of a failed message replaces it
        final[(rec["round_number"], rec["turn"], rec["agent_id"])] = rec
    msgs = [r for r in final.values() if r.get("message")]

    def labels_for(agent_id):
        if not labels:
            return None
        return labels.get(str(agent_id), labels) if "cooperate" not in labels else labels

    def named(text, lab):
        return [k for k in ("cooperate", "defect") if lab[k] in text]

    on_topic = 0
    last_statement = {}
    for m in sorted(msgs, key=lambda r: (r["round_number"], r["turn"])):
        lab = labels_for(m["agent_id"])
        names = named(m["message"], lab) if lab else []
        if names or GAME_TERMS.search(m["message"]):
            on_topic += 1
        if len(names) == 1:
            last_statement[(str(m["agent_id"]), m["round_number"])] = names[0]
    kept = broken = 0
    for (agent, rnd), stated in last_statement.items():
        row = history.get((agent, rnd))
        if row is None:
            continue
        chose = "cooperate" if row["cooperated"] == "1" else "defect"
        kept += chose == stated
        broken += chose != stated
    stated_coop = [v for v in last_statement.values()]
    return dict(
        messages=len(msgs),
        failed=len(final) - len(msgs),
        mean_chars=round(sum(len(m["message"]) for m in msgs) / len(msgs), 1) if msgs else None,
        on_topic_share=round(on_topic / len(msgs), 3) if msgs else None,
        stated_intentions=len(last_statement),
        stated_cooperate_share=(round(stated_coop.count("cooperate") / len(stated_coop), 3)
                                if stated_coop else None),
        intention_kept=kept, intention_broken=broken,
    )


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
    print(f"session {s['session']}  labels {s['labels']}  comprehension {s['comprehension_correct']}  beliefs {s['beliefs']}")
    print("round  n  coop  AA  BB  AB  missing  mean_payoff")
    for r in s["rounds"]:
        print(f"{r['round']:>5} {r['n']:>2} {r['cooperation_rate']:>5.2f} {r['mutual_cooperation']:>3} "
              f"{r['mutual_defection']:>3} {r['split']:>3} {r['missing']:>8} {r['mean_payoff']:>12}")
    print("conditional cooperation:", s["conditional_cooperation"])
    print("decision sources:", s["decision_sources"])
    print("llm:", s["llm"], "mismatches:", s["mismatches"])
    print("discourse:", s["discourse"])
    if s["chat"]:
        print("pair chat:", s["chat"])
    if s["failures"]:
        print("FAILURES:", s["failures"])


if __name__ == "__main__":
    main()
