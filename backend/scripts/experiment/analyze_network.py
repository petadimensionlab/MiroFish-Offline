"""
Summarize one network-chat session (NOTES.md #54): does talk between
non-partners, on a graph with heterogeneous contact rates, move behaviour?

Reads the oTree custom export (pd_debate_custom.csv or pgg_custom.csv,
authoritative) and <sim_dir>/game/<session_code>/{network.json,
network_contacts.jsonl, network_chat.jsonl, bridge_log.jsonl}.

Sections: network stats; per-agent table (degree, lambda, contacts, messages,
cooperation); Spearman of degree / lambda / conversations against cooperation;
negative-binomial check of the drawn contact counts; behavioural exposure
(conversation partners who defected in t-1); talk exposure (what received
messages named, PD); edge concordance of the last round against a
permutation baseline; talk quality (on topic, partner reports, possible confusion). With
channel dyads (#55, network.json has edge_attrs): a dyads section (compatibility
A against how often pairs talked and answered, reply rate by compatibility
tercile, unanswered conversations). With memory_shown.jsonl (net_memory_mode
'decay'): a memory section (block size, tier shares, remembered exposure).
A betrayal section (NOTES.md #57, analyze_betrayal.py): what agents said they
would choose against what they chose, what they did to their partner, and what
that did to the people who heard of it; recomputed from the logs, so every
network run has it (with the reveal off its 'told' facts are a placebo).
Conversations nobody answered are no exposure: the responder never saw them.

Usage:
    python analyze_network.py --otree-csv export/pd_debate_custom.csv --sim-dir <sim_dir> \
        [--session CODE] [--json]
"""

import argparse
import csv
import json
import os
import random
import re
import sys
from collections import defaultdict

import networkx as nx
import numpy as np
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import analyze_betrayal
except Exception:  # noqa: BLE001 -- keep this script usable on its own
    analyze_betrayal = None
try:
    from analyze_session import GAME_TERMS
except Exception:  # noqa: BLE001 -- keep this script usable on its own
    GAME_TERMS = re.compile(r"\btrust|look out for (your|my)self|same person|decision task|\bpoints\b"
                            r"|the other person|cooperat|betray|selfish", re.I)

# Partner talk (NOTES.md #54). Agents correctly tell third parties what happened with their own game
# partner ("My partner and I both chose X in round 1"); that is counted as PARTNER_REPORT only.
# Confusion is the speaker treating the *listener* as the game partner: second-person joint-action
# proposals ("let's both", "you and I should", "if we both", "shall we both", "we can both get 30")
# or claims about "our" round result with the listener. Third-person "my partner and I" is not counted.
PARTNER_REPORT = re.compile(r"\bmy (game )?partner\b", re.I)
POSSIBLE_CONFUSION = re.compile(
    r"\blet'?s both\b|\byou and I (should|could|can|will|both|each)\b|\bif we both\b|\bshall we both\b"
    r"|\bwe can both\b|\bour (round|result|choices?|payoffs?|scores?)\b(?! (with|against))",
    re.I)
PERMUTATIONS = 1000


def load_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def var(xs):
    xs = list(xs)
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return round(sum((x - m) ** 2 for x in xs) / (len(xs) - 1), 4)


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3 or len({x for x, _ in pairs}) < 2 or len({y for _, y in pairs}) < 2:
        return None
    rho, p = sps.spearmanr([x for x, _ in pairs], [y for _, y in pairs])
    return dict(rho=round(float(rho), 3), p=round(float(p), 4), n=len(pairs))


def load_rows(otree_csv, session=None):
    rows = list(csv.DictReader(open(otree_csv, encoding="utf-8")))
    sessions = sorted({r["session_code"] for r in rows})
    if session is None:
        if len(sessions) != 1:
            raise SystemExit(f"expected one session, found {sessions}; pass --session")
        session = sessions[0]
    rows = [r for r in rows if r["session_code"] == session]
    if not rows:
        raise SystemExit(f"no rows for session {session}")
    game = "pd" if "cooperated" in rows[0] else "pgg" if "contribution" in rows[0] else None
    if game is None:
        raise SystemExit("neither a 'cooperated' nor a 'contribution' column: not a PD / pgg export")
    return rows, session, game


def label_symbols(log):
    """(cooperate symbol, defect symbol) from the configure event, else None."""
    for e in log:
        if e.get("event") == "configure":
            labels = e.get("labels") or {}
            if "cooperate" in labels:
                return labels["cooperate"], labels["defect"]
            for v in labels.values():
                if isinstance(v, dict) and "cooperate" in v:
                    return v["cooperate"], v["defect"]
    return None


def summarize(otree_csv, sim_dir, session=None):
    rows, session, game = load_rows(otree_csv, session)
    sdir = os.path.join(sim_dir, "game", session)
    net_path = os.path.join(sdir, "network.json")
    if not os.path.exists(net_path):
        raise SystemExit(f"{net_path} not found: not a network run")
    net = json.load(open(net_path, encoding="utf-8"))
    contacts = load_jsonl(os.path.join(sdir, "network_contacts.jsonl"))
    chat = load_jsonl(os.path.join(sdir, "network_chat.jsonl"))
    log = load_jsonl(os.path.join(sdir, "bridge_log.jsonl"))
    settings = next((e["settings"] for e in log if e.get("event") == "configure"), {})
    endowment = (settings.get("game_params") or {}).get("endowment", 20)

    # coop[(agent, round)] in [0, 1]; binary = cooperated / at least half the endowment
    coop, binary = {}, {}
    for r in rows:
        a, t = int(r["agent_id"]), int(r["round_number"])
        if game == "pd":
            coop[(a, t)] = float(r["cooperated"])
            binary[(a, t)] = int(r["cooperated"])
        else:
            coop[(a, t)] = int(r["contribution"]) / endowment
            binary[(a, t)] = int(coop[(a, t)] >= 0.5)
    rounds = sorted({t for _, t in coop})
    agents = sorted({a for a, _ in coop})
    last = rounds[-1]

    messages = [m for m in chat if m.get("message")]
    # conversations nobody answered (#55): no exposure for either side
    unanswered = {(rec["round_number"], c["conv_id"]) for rec in contacts
                  for c in rec["conversations"] if c.get("replied") is False}
    # conversations with at least one message, per round: (initiator, responder)
    convs = defaultdict(set)
    for m in messages:
        if (m["round_number"], m["conv_id"]) in unanswered:
            continue
        convs[(m["round_number"], m["conv_id"])] = (m["initiator"], m["agent_id"] if m["agent_id"] != m["initiator"]
                                                    else m["other_agent_id"])
    partners = defaultdict(set)  # (agent, round) -> conversation partners
    for (t, _), (i, j) in convs.items():
        partners[(i, t)].add(j)
        partners[(j, t)].add(i)
    planned = defaultdict(int)
    started = defaultdict(int)
    received = defaultdict(int)
    k_drawn = defaultdict(list)
    for rec in contacts:
        for a, r in rec["agents"].items():
            a = int(a)
            k_drawn[a].append(r["k_drawn"])
            started[a] += len(r["initiated"])
            received[a] += len(r["received"])
            planned[a] += r["k_drawn"]
    sent = defaultdict(int)
    for m in messages:
        sent[m["agent_id"]] += 1

    nodes = {n["agent_id"]: n for n in net["nodes"]}
    table = []
    for a in agents:
        n = nodes.get(a, {})
        table.append(dict(
            agent_id=a, degree=n.get("degree"), lam=n.get("lambda"), mean_k_drawn=mean(k_drawn[a]),
            convs_started=started[a], convs_received=received[a], messages_sent=sent[a],
            coop=mean(coop[(a, t)] for t in rounds if (a, t) in coop)))
    correlations = {
        "degree": spearman([row["degree"] for row in table], [row["coop"] for row in table]),
        "lambda": spearman([row["lam"] for row in table], [row["coop"] for row in table]),
    }
    correlations["total_conversations"] = spearman(
        [row["convs_started"] + row["convs_received"] for row in table], [row["coop"] for row in table])

    # negative binomial check: drawn counts vs mu and mu + mu^2 / r
    contact = net.get("contact", {})
    mu, r_disp = contact.get("mean"), contact.get("dispersion")
    pooled = [k for ks in k_drawn.values() for k in ks]
    nb = dict(n=len(pooled), mean=mean(pooled), variance=var(pooled), mu=mu,
              expected_variance=(round(mu + mu * mu / r_disp, 4) if mu is not None and r_disp and r_disp > 0
                                 else mu))

    # behavioural exposure: conversation partners who defected in t-1
    def split(exposure_fn, levels):
        """P(coop_t | exposure level), by own t-1 choice."""
        cells = defaultdict(list)
        for a in agents:
            for t in rounds[1:]:
                if (a, t) not in binary or (a, t - 1) not in binary:
                    continue
                lvl = exposure_fn(a, t)
                if lvl is not None:
                    cells[(lvl, binary[(a, t - 1)])].append(binary[(a, t)])
        out = {}
        for lvl in levels:
            out[lvl] = {("own_prev_coop" if own else "own_prev_defect"):
                        dict(n=len(cells[(lvl, own)]), p_coop=mean(cells[(lvl, own)]))
                        for own in (1, 0)}
        return out

    def behav_exposure(a, t):
        ps = partners.get((a, t), set())
        if not ps:
            return None  # no conversation: neither exposed nor a fair control
        return "exposed" if any(binary.get((p, t - 1)) == 0 for p in ps) else "not_exposed"

    def any_exposure(a, t):
        ps = partners.get((a, t), set())
        if not ps:
            return "no_conversation"
        return behav_exposure(a, t)

    behavioural = split(any_exposure, ("exposed", "not_exposed", "no_conversation"))

    # talk exposure (PD): what the messages an agent received named
    talk = None
    symbols = label_symbols(log) if game == "pd" else None
    if symbols:
        c_sym, d_sym = symbols
        names_by = defaultdict(set)
        for m in messages:
            to = m["other_agent_id"]
            t = m["round_number"]
            if (t, m["conv_id"]) in unanswered:
                continue
            if c_sym in m["message"]:
                names_by[(to, t)].add("c")
            if d_sym in m["message"]:
                names_by[(to, t)].add("d")

        def talk_exposure(a, t):
            s = names_by.get((a, t), set())
            return {frozenset(): "neither", frozenset("c"): "cooperate_only",
                    frozenset("d"): "defect_only", frozenset("cd"): "both"}[frozenset(s)]
        talk = dict(symbols=dict(cooperate=c_sym, defect=d_sym),
                    **{"table": split(talk_exposure, ("defect_only", "cooperate_only", "both", "neither"))})

    # edge concordance of the last round against a permutation baseline
    G = nx.Graph()
    G.add_nodes_from(agents)
    final = {a for a, t in binary if t == last}
    G.add_edges_from((a, b) for a, b in net["edges"] if a in final and b in final)
    vals = {a: binary[(a, last)] for a in G.nodes if (a, last) in binary}
    concordance = None
    if G.number_of_edges() and len(set(vals.values())) > 1:
        def same(v):
            return sum(v[a] == v[b] for a, b in G.edges) / G.number_of_edges()
        observed = same(vals)
        rng = random.Random(0)
        keys, values = list(vals), list(vals.values())
        perm = []
        for _ in range(PERMUTATIONS):
            rng.shuffle(values)
            perm.append(same(dict(zip(keys, values))))
        nx.set_node_attributes(G, vals, "v")
        assort = nx.attribute_assortativity_coefficient(G, "v")
        concordance = dict(round=last, observed=round(observed, 4), permuted_mean=mean(perm),
                           p_ge=round((1 + sum(p >= observed for p in perm)) / (PERMUTATIONS + 1), 4),
                           assortativity=None if assort != assort else round(float(assort), 4))

    quality = dict(
        messages=len(messages), attempts=len(chat),
        failed_attempts=sum(1 for m in chat if not m.get("message")),
        on_topic=mean(1 if GAME_TERMS.search(m["message"]) else 0 for m in messages),
        partner_reports=mean(1 if PARTNER_REPORT.search(m["message"]) else 0 for m in messages),
        possible_confusion=mean(1 if POSSIBLE_CONFUSION.search(m["message"]) else 0 for m in messages),
        possible_confusion_examples=[m["message"][:300] for m in messages
                                     if POSSIBLE_CONFUSION.search(m["message"])][:5])

    by_round = [dict(round_number=t, coop=mean(coop[(a, t)] for a in agents if (a, t) in coop),
                     conversations=sum(1 for (r, _) in convs if r == t),
                     messages=sum(1 for m in messages if m["round_number"] == t)) for t in rounds]
    out = dict(session_code=session, game=game, topology=net.get("topology"), network=net.get("stats"),
               contact=contact, rounds=by_round, agents=table, correlations=correlations,
               contact_count_check=nb, behavioural_exposure=behavioural, talk_exposure=talk,
               edge_concordance=concordance, talk_quality=quality)
    if net.get("edge_attrs"):
        out["dyads"] = dyad_summary(net, contacts, load_json(os.path.join(sdir, "dyads.json")))
    memory_rows = load_jsonl(os.path.join(sdir, "memory_shown.jsonl"))
    if memory_rows:
        out["memory"] = memory_summary(memory_rows, game, symbols, binary, agents, rounds, split)
    if analyze_betrayal is not None:
        try:
            out["betrayal"] = analyze_betrayal.summarize(sdir)
        except (KeyError, ValueError, IndexError):  # logs without outcomes / labels: no betrayal section
            pass
    return out


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def quantiles(xs):
    if not len(xs):
        return None
    q = np.percentile(list(xs), [0, 25, 50, 75, 100])
    return dict(n=len(xs), min=round(float(q[0]), 3), q25=round(float(q[1]), 3), median=round(float(q[2]), 3),
                q75=round(float(q[3]), 3), max=round(float(q[4]), 3))


def dyad_summary(net, contacts, dyads_doc):
    """Compatibility A against talk on the graph's edges (NOTES.md #55)."""
    attrs = {(e["a"], e["b"]): e for e in net["edge_attrs"]}
    attempts, answered = defaultdict(int), defaultdict(int)
    reach, replied = [], []
    for rec in contacts:
        for c in rec["conversations"]:
            key = (min(c["initiator"], c["responder"]), max(c["initiator"], c["responder"]))
            if key not in attrs:
                continue
            attempts[key] += 1
            ok = c.get("replied") is not False
            answered[key] += int(ok)
            e = attrs[key]
            reach.append(e["reach_ab"] if c["initiator"] == key[0] else e["reach_ba"])
            replied.append(int(ok))
    edges = sorted(attrs)
    compat = [attrs[k]["compat"] for k in edges]
    n_unanswered = sum(attempts.values()) - sum(answered.values())
    tercile = []
    order = sorted(edges, key=lambda k: attrs[k]["compat"])
    for name, part in zip(("low", "middle", "high"), np.array_split(np.array(order, dtype=object), 3)):
        keys = [tuple(k) for k in part]
        n = sum(attempts[k] for k in keys)
        tercile.append(dict(tercile=name, edges=len(keys),
                            compat=[round(attrs[keys[0]]["compat"], 3), round(attrs[keys[-1]]["compat"], 3)] if keys else None,
                            conversations=n, reply_rate=round(sum(answered[k] for k in keys) / n, 4) if n else None))
    all_a = [d["compat"] for d in dyads_doc["dyads"] if d.get("compat") is not None] if dyads_doc else []
    return dict(
        compat_all_dyads=quantiles(all_a), compat_edges=quantiles(compat),
        spearman_compat_attempts=spearman(compat, [attempts[k] for k in edges]),
        spearman_compat_answered=spearman(compat, [answered[k] for k in edges]),
        reply_rate_by_compat_tercile=tercile, unanswered=n_unanswered,
        conversations=sum(attempts.values()),
        realised_reply_rate=mean(replied), mean_reach=mean(reach),
        beta=(net.get("channels") or {}).get("beta"), reply_model=(net.get("channels") or {}).get("reply_model"))


def memory_summary(rows, game, symbols, binary, agents, rounds, split):
    """Size and tier mix of the memory blocks, and remembered exposure (PD)."""
    out = {}
    for purpose in ("decision", "network_chat"):
        rs = [r for r in rows if r["purpose"] == purpose]
        if not rs:
            continue
        tiers = defaultdict(int)
        for r in rs:
            for i in r["items"]:
                if i.get("kind") != "note":  # revealed choices (#57) are no conversation
                    tiers[i["tier"]] += 1
        total = sum(tiers.values())
        out[purpose] = dict(
            prompts=len(rs), mean_chars=mean(r["chars"] for r in rs),
            at_budget=sum(1 for r in rs if r["over_budget_demotions"] > 0),
            tier_share={k: round(v / total, 3) for k, v in sorted(tiers.items())} if total else {})
    if game == "pd" and symbols:
        d_sym = symbols[1]
        seen = {}
        for r in rows:
            if r["purpose"] != "decision":
                continue
            dropped = {a["other"] for a in r["aggregates"] if a["dropped"] and a.get("kind") != "note"}
            hit = any(d_sym in i.get("mentions_other", []) and (
                i["tier"] in ("excerpt", "gist") or (i["tier"] == "aggregate" and i["other"] not in dropped))
                for i in r["items"])
            seen[(r["agent_id"], r["round_number"])] = "remembered_defect" if hit else "no_remembered_defect"
        out["remembered_exposure"] = dict(
            symbol=d_sym, table=split(lambda a, t: seen.get((a, t)), ("remembered_defect", "no_remembered_defect")))
    return out


def show(s):
    print(f"session {s['session_code']}  game {s['game']}  topology {s['topology']}")
    print("network:", s["network"])
    print("contact:", s["contact"])
    print("\nround  coop   convs  messages")
    for r in s["rounds"]:
        print(f"{r['round_number']:>5}  {r['coop']:<5}  {r['conversations']:>5}  {r['messages']:>8}")
    print("\nagent degree lambda  k_drawn  started received sent  coop")
    for a in s["agents"]:
        print(f"{a['agent_id']:>5} {a['degree']!s:>6} {a['lam']!s:>6} {a['mean_k_drawn']!s:>8} "
              f"{a['convs_started']:>7} {a['convs_received']:>8} {a['messages_sent']:>4}  {a['coop']}")
    print("\nSpearman vs cooperation:", s["correlations"])
    print("contact counts (mean / variance vs mu / mu + mu^2/r):", s["contact_count_check"])
    print("\nbehavioural exposure, P(coop_t) by whether a conversation partner defected at t-1:")
    for lvl, cells in s["behavioural_exposure"].items():
        print(f"  {lvl}: {cells}")
    if s["talk_exposure"]:
        print("\ntalk exposure (received messages name), symbols", s["talk_exposure"]["symbols"])
        for lvl, cells in s["talk_exposure"]["table"].items():
            print(f"  {lvl}: {cells}")
    print("\nedge concordance (last round):", s["edge_concordance"])
    print("talk quality:", s["talk_quality"])
    if s.get("dyads"):
        d = s["dyads"]
        print(f"\ndyads (beta {d['beta']}, reply model {d['reply_model']}):")
        print("  compatibility, all dyads:", d["compat_all_dyads"])
        print("  compatibility, edges:    ", d["compat_edges"])
        print("  Spearman(compat, conversations per edge):", d["spearman_compat_attempts"])
        print("  Spearman(compat, answered per edge):     ", d["spearman_compat_answered"])
        print("  reply rate by compatibility tercile:")
        for t in d["reply_rate_by_compat_tercile"]:
            print(f"    {t}")
        print(f"  unanswered {d['unanswered']} of {d['conversations']}; realised reply rate "
              f"{d['realised_reply_rate']} vs mean reach {d['mean_reach']}")
    if s.get("memory"):
        print("\nmemory:")
        for k, v in s["memory"].items():
            print(f"  {k}: {v}")
    if s.get("betrayal"):
        b = s["betrayal"]
        st = b["statements"]
        print(f"\nbetrayal (reveal {b['reveal']}, rho_w {b['rho_w']}, rho_c {b['rho_c']}; facts are {b['facts_are']}):")
        print(f"  statements {st['statements']} of {st['speaker_conversations']} speaker-conversations "
              f"(coverage {st['coverage']}), kept {st['kept']} broken {st['broken']}")
        print("  game events:", b["game_events"]["by_kind"], "exploits", b["game_events"]["exploits"])
        for k in ("aftermath", "spillover", "selection", "speakers"):
            print(f"  {k}: {b[k]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--otree-csv", required=True)
    ap.add_argument("--sim-dir", required=True)
    ap.add_argument("--session", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    s = summarize(args.otree_csv, args.sim_dir, args.session)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        show(s)


if __name__ == "__main__":
    main()
