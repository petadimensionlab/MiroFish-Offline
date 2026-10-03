"""
Collect archived experiment runs into one results.json (+ results_rounds.csv).

Reads results_manifest.json, calls the existing analysis code as library
functions (analyze_session / analyze_pgg / analyze_games / analyze_network)
and adds per-run metadata, per-round series, headline numbers and a few
verbatim illustrative messages. No LLM calls, no servers.

Usage:
    backend/.venv/bin/python backend/scripts/experiment/collect_results.py \
        [--manifest results_manifest.json] [--out-dir docs/otree-integration/results]
"""

import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analyze_games  # noqa: E402
import analyze_network  # noqa: E402
import analyze_pgg  # noqa: E402
import analyze_session  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_OUT = os.path.join(REPO, "docs", "otree-integration", "results")


def r3(x, nd=3):
    return None if x is None else round(float(x), nd)


def load_jsonl(path):
    return analyze_session.load_jsonl(path)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def clean(o):
    """Make numpy / tuple values JSON friendly and round floats."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        o = o.item()
    if isinstance(o, float):
        return round(o, 4)
    return o


def headline(series):
    xs = [v for v in series if v is not None]
    if not xs:
        return None
    return dict(first=r3(series[0]), last=r3(series[-1]), mean=r3(sum(xs) / len(xs)))


def first_listed(log, labels):
    """Share of PD decisions that chose the first-listed option.
    Internal 'A' = cooperate. Last decide event per (round, agent), missing excluded."""
    last = {}
    for e in log:
        if e.get("event") == "decide":
            last[(e["round_number"], e["agent_id"])] = e
    n = first = 0
    for (rnd, agent), e in last.items():
        if e.get("missing") or e.get("choice") not in ("A", "B"):
            continue
        lab = labels.get(str(agent), labels) if "cooperate" not in labels else labels
        chose = lab["cooperate"] if e["choice"] == "A" else lab["defect"]
        n += 1
        first += chose == lab["order"][0]
    return dict(n=n, first_listed=first, share=r3(first / n) if n else None)


def find_session_dir(sim_dir):
    dirs = sorted(glob.glob(os.path.join(sim_dir, "game", "*")))
    dirs = [d for d in dirs if os.path.isdir(d)]
    if len(dirs) != 1:
        raise SystemExit(f"expected one session under {sim_dir}/game, found {dirs}")
    return dirs[0]


def comprehension(game_dir):
    comp = load_jsonl(os.path.join(game_dir, "comprehension.jsonl"))
    return dict(correct=sum(1 for c in comp if c.get("correct")), n=len(comp))


def condition_fields(settings, n_agents, labels):
    s = settings
    keys = ["chat_turns", "net_topology", "net_channel_beta", "net_reply_model", "net_memory_mode",
            "net_channels", "net_channel_topic_weight", "net_pair_chat_model", "net_memory_half_life",
            "net_memory_budget_chars", "net_mean_degree", "net_contact_mean", "net_turns", "no_think",
            "label_scheme", "label_unit", "label_randomize", "label_order_per_agent", "game_params"]
    cond = {k: s.get(k) for k in keys if k in s}
    cond["beta"] = cond.pop("net_channel_beta", None)
    cond["reply_model"] = cond.pop("net_reply_model", None)
    cond["memory_mode"] = cond.pop("net_memory_mode", "off" if s.get("net_topology") else None)
    cond["seed"] = s.get("seed")
    cond["net_seed"] = s.get("net_seed")
    cond["policy"] = s.get("policy")
    return cond


def run_model(log):
    for e in log:
        if e.get("event") == "decide" and e.get("source", "").startswith("llm:"):
            return e["source"][4:]
    return None


def csv_rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def missing_total(rows):
    return sum(r["decision_missing"] == "1" for r in rows)


def series_for(game, summ):
    """{metric: [value per round]} from an analyzer summary."""
    rs = summ["rounds"]
    if game == "pd":
        return {"cooperation_rate": [r["cooperation_rate"] for r in rs]}
    if game == "pgg":
        return {"mean_contribution_share": [r["share_of_endowment"] for r in rs],
                "mean_contribution": [r["mean"] for r in rs]}
    if game == "beauty":
        return {"mean_guess": [r["mean"] for r in rs]}
    if game == "trust":
        return {"mean_sent": [r["mean_sent"] for r in rs],
                "mean_return_share": [r["mean_return_share"] for r in rs]}
    if game == "ultimatum":
        return {"mean_offer": [r["mean_offer"] for r in rs],
                "acceptance_rate": [r["acceptance_rate"] for r in rs]}
    raise ValueError(game)


def collect_run(root, m):
    sim_dir = os.path.join(root, m["sim"])
    csv_path = glob.glob(os.path.join(root, m["export"], "*_custom.csv"))
    csv_path = [p for p in csv_path if not p.endswith("all_apps_wide.csv")]
    if len(csv_path) != 1:
        raise SystemExit(f"{m['id']}: expected one *_custom.csv, got {csv_path}")
    csv_path = csv_path[0]
    game_dir = find_session_dir(sim_dir)
    log = load_jsonl(os.path.join(game_dir, "bridge_log.jsonl"))
    configure = next(e for e in log if e.get("event") == "configure")
    settings = configure.get("settings", {})
    labels = configure.get("labels")
    rows = csv_rows(csv_path)
    game = m["game"]
    is_network = os.path.exists(os.path.join(game_dir, "network.json"))
    meta = json.load(open(os.path.join(sim_dir, "personas_meta.json")))

    out = dict(id=m["id"], phase=m["phase"], game=game, label=m["label"],
               model=run_model(log), thinking=m.get("thinking"),
               persona_set=m["persona_set"],
               persona_meta=dict(seed=meta.get("seed"), pool_n=meta.get("n"), generator_model=meta.get("model"),
                                 group=meta.get("group"), channels_file=meta.get("channels_file")),
               n_agents=configure.get("n_agents"),
               rounds=len({int(r["round_number"]) for r in rows}),
               condition=condition_fields(settings, configure.get("n_agents"), labels),
               session=rows[0]["session_code"], export=m["export"], sim=m["sim"],
               export_csv_sha256_16=sha(csv_path))

    if game == "pd":
        summ = analyze_session.summarize(csv_path, sim_dir)
    elif game == "pgg":
        summ = analyze_pgg.summarize(csv_path, sim_dir)
    else:
        fn = getattr(analyze_games, game)
        summ = fn(rows)
    out["comprehension"] = comprehension(game_dir)
    out["missing_decisions"] = missing_total(rows)
    out["series"] = series_for(game, summ)
    out["headline"] = {k: headline(v) for k, v in out["series"].items()}

    if game == "pd":
        out["conditional_cooperation"] = summ["conditional_cooperation"]
        out["round_detail"] = [dict(round=r["round"], mutual_cooperation=r["mutual_cooperation"],
                                    mutual_defection=r["mutual_defection"], split=r["split"],
                                    mean_payoff=r["mean_payoff"]) for r in summ["rounds"]]
        out["first_listed_choice"] = first_listed(log, labels)
        out["beliefs"] = summ["beliefs"]
        out["decision_sources"] = summ["decision_sources"]
        if summ["chat"]:
            out["pair_chat"] = summ["chat"]
            out["messages"] = dict(total=summ["chat"]["messages"], failed=summ["chat"]["failed"])
    elif game == "pgg":
        for k in ("first_round_mean", "last_round_mean", "decline", "end_game_drop", "trend_per_round",
                  "conditional_slope", "nash_share"):
            out[k] = summ[k]
        out["beliefs"] = summ.get("beliefs")
    elif game == "beauty":
        out["equilibrium_share"] = summ["equilibrium_share"]
        out["round_detail"] = [dict(round=r["round"], median=r["median"], zero=r["zero"],
                                    above_67=r["above_67"]) for r in summ["rounds"]]
    elif game == "trust":
        out["round_detail"] = [dict(round=i + 1, sent_zero=r["sent_zero"], sent_all=r["sent_all"],
                                    sender_mean_payoff=r["sender_mean_payoff"]) for i, r in enumerate(summ["rounds"])]
    elif game == "ultimatum":
        out["rejected_offers"] = summ["rejected_offers"]
        out["mean_offer_share"] = summ["mean_offer_share"]
        out["round_detail"] = [dict(round=r["round"], min_offer=r["min_offer"], max_offer=r["max_offer"])
                               for r in summ["rounds"]]

    if is_network:
        net = analyze_network.summarize(csv_path, sim_dir)
        lam = [a["lam"] for a in net["agents"] if a.get("lam") is not None]
        lam_s = sorted(lam)
        k = [a["mean_k_drawn"] for a in net["agents"] if a.get("mean_k_drawn") is not None]
        out["network"] = dict(
            topology=net["topology"], stats=net["network"], contact=net["contact"],
            lambda_summary=dict(n=len(lam), mean=r3(sum(lam) / len(lam), 4), median=r3(lam_s[len(lam_s) // 2], 4),
                                min=r3(lam_s[0], 4), max=r3(lam_s[-1], 4),
                                n_below_0_1=sum(x < 0.1 for x in lam),
                                mean_k_drawn_per_agent=r3(sum(k) / len(k), 4) if k else None),
            spearman=net["correlations"], contact_count_check=net["contact_count_check"],
            behavioural_exposure=net["behavioural_exposure"], talk_exposure=net["talk_exposure"],
            edge_concordance=net["edge_concordance"], talk_quality=net["talk_quality"])
        if net.get("dyads"):
            out["network"]["dyads"] = net["dyads"]
        if net.get("memory"):
            out["network"]["memory"] = net["memory"]
        out["series"]["conversations"] = [r["conversations"] for r in net["rounds"]]
        out["series"]["messages"] = [r["messages"] for r in net["rounds"]]
        q = net["talk_quality"]
        out["messages"] = dict(total=q["messages"], attempts=q["attempts"], failed=q["failed_attempts"],
                               conversations=sum(out["series"]["conversations"]))
    elif "messages" not in out:
        out["messages"] = dict(total=0, failed=0)
    return clean(out)


# ---------------------------------------------------------------- messages

INTENT = re.compile(r"\b(I('| wi)ll (choose|pick|go with|select|play|stick)|I'?m (planning|going|choosing|picking)|"
                    r"I plan to|I intend to|I am (planning|going|choosing)|my plan is)\b", re.I)
OUTCOME = re.compile(r"\b(my (game )?partner\b.*\b(chose|picked|defected|cooperated|betrayed|got)|"
                     r"(last|previous) round\b.*\b(chose|picked|defected|cooperated|got|points)|"
                     r"(we|they|both of us) both (chose|picked|got)|you (chose|picked|defected|cooperated))", re.I)
HOSTILE = re.compile(r"\b(exploit\w*|punish\w*|retaliat\w*|take advantage|taken advantage|sucker\w*|"
                     r"backstab\w*|revenge|never again|screw\w*|cheat\w*|betray\w*)\b", re.I)
MAX_CHARS = 320


def success_messages(game_dir, kind):
    if kind == "pair":
        final = {}
        for rec in load_jsonl(os.path.join(game_dir, "chat.jsonl")):
            final[(rec["round_number"], rec["turn"], rec["agent_id"])] = rec
        recs = [r for r in final.values() if r.get("message")]
        return [dict(round=r["round_number"], agent=r["agent_id"], to=r.get("partner_agent_id"),
                     conv=None, turn=r["turn"], text=r["message"]) for r in recs]
    recs = [r for r in load_jsonl(os.path.join(game_dir, "network_chat.jsonl")) if r.get("message")]
    return [dict(round=r["round_number"], agent=r["agent_id"], to=r["other_agent_id"], conv=r["conv_id"],
                 turn=r["turn"], text=r["message"], initiator=r["initiator"]) for r in recs]


def pick(msgs, rx, used):
    cand = [m for m in msgs if len(m["text"]) <= MAX_CHARS and rx.search(m["text"])
            and (m["round"], m["agent"], m["turn"], m["conv"]) not in used]
    cand.sort(key=lambda m: (m["round"], m["agent"], m["turn"], str(m["conv"])))
    return cand[0] if cand else None


def representative(root, m):
    sim_dir = os.path.join(root, m["sim"])
    game_dir = find_session_dir(sim_dir)
    kind = "pair" if os.path.exists(os.path.join(game_dir, "chat.jsonl")) else "network"
    msgs = success_messages(game_dir, kind)
    used, out = set(), []
    for cat, rx in (("intention", INTENT), ("own_pair_outcome", OUTCOME), ("exploitation_or_retaliation", HOSTILE)):
        p = pick(msgs, rx, used)
        if p:
            used.add((p["round"], p["agent"], p["turn"], p["conv"]))
            out.append(dict(category=cat, round=p["round"], agent_id=p["agent"], to_agent_id=p["to"],
                            conv_id=p["conv"], text=p["text"]))
    if m["id"] == "ch_ba":
        contacts = load_jsonl(os.path.join(game_dir, "network_contacts.jsonl"))
        by = {(x["round"], x["conv"], x["agent"]): x for x in msgs}
        found = []
        for rec in contacts:
            for c in rec["conversations"]:
                if c.get("replied") is False:
                    x = by.get((rec["round_number"], c["conv_id"], c["initiator"]))
                    if x and len(x["text"]) <= MAX_CHARS and x["turn"] == 0:
                        found.append(x)
        found.sort(key=lambda x: (x["round"], x["agent"], str(x["conv"])))
        if found:
            x = found[0]
            out.append(dict(category="unanswered_attempt", round=x["round"], agent_id=x["agent"],
                            to_agent_id=x["to"], conv_id=x["conv"], text=x["text"],
                            note=f"initiator message of a conversation with replied=false "
                                 f"({len(found)} such with a recorded turn-0 message)"))
    return out


def e4b_note(root, spec):
    game_dir = find_session_dir(os.path.join(root, spec["sim"]))
    log = load_jsonl(os.path.join(game_dir, "bridge_log.jsonl"))
    configure = next(e for e in log if e.get("event") == "configure")
    labels = configure["labels"]
    decides = [e for e in log if e.get("event") == "decide"]
    r1 = [e for e in log if e.get("event") == "decide" and e["round_number"] == 1]
    r1_log = [e for e in r1]
    ok = {e["agent_id"]: e for e in r1_log if not e.get("missing") and e.get("choice") in ("A", "B")}
    coop = sum(e["choice"] == "A" for e in ok.values())
    return clean(dict(sim=spec["sim"], label=spec["label"], partial=True, model=run_model(log),
                      configured_rounds=configure["settings"].get("num_rounds"),
                      max_round_with_decisions=max((e["round_number"] for e in decides), default=None),
                      round1=dict(n=len(ok), cooperation_rate=r3(coop / len(ok)) if ok else None,
                                  first_listed=first_listed(r1_log, labels)),
                      chat_turns=configure["settings"].get("chat_turns")))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--manifest", default=os.path.join(HERE, "results_manifest.json"))
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args()
    man = json.load(open(args.manifest))
    root = man["data_root"]
    runs = [collect_run(root, m) for m in man["runs"]]
    by_id = {m["id"]: m for m in man["runs"]}
    reps = {i: representative(root, by_id[i]) for i in man["representative_messages"]}
    result = dict(
        description="Archived oTree x MiroFish runs; produced by collect_results.py from results_manifest.json",
        data_root=root, n_runs=len(runs), runs=runs,
        e4b_note=e4b_note(root, man["e4b_note"]),
        representative_messages=reps,
        representative_message_rules=dict(
            max_chars=MAX_CHARS, intention=INTENT.pattern, own_pair_outcome=OUTCOME.pattern,
            exploitation_or_retaliation=HOSTILE.pattern,
            unanswered_attempt="ch_ba only: turn-0 message of the initiator of a conversation with replied=false; "
                               "first match in (round, agent) order for every category"),
        excluded=man.get("excluded"))
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out_dir, "results_rounds.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "round", "metric", "value"])
        for r in runs:
            for metric, vals in r["series"].items():
                for i, v in enumerate(vals, 1):
                    if v is not None:
                        w.writerow([r["id"], i, metric, v])
    print(f"wrote {args.out_dir}/results.json ({len(runs)} runs) and results_rounds.csv")


if __name__ == "__main__":
    main()
