"""
Preview channel-compatible contacts without any LLM call (NOTES.md #55).

For the agents of a channel simulation (add_channels.py, #53) this builds the
same graph, contact rates and compatibility A_ij the bridge would, and runs
the contact sampling of several seeds and rounds: how strongly does A
predict how often two people talk (Spearman over the graph's edges, pooled
over seeds), how many conversations per round, and what share goes
unanswered under reply model 'reach'. Partners are taken as consecutive
agent ids in --agent-ids (pairs for PD, groups of 4 for pgg); the real
pairing is oTree's, so this is a preview, not a prediction.

Usage:
    python scripts/experiment/preview_channel_contacts.py --sim-dir SIM_DIR \
        [--topology ba] [--beta 1.0] [--reply reach] [--seeds 20] [--rounds 10] \
        [--agent-ids 3,4,6,...] [--game pd|pgg] [--topic-weight 0.5]
Without --agent-ids the first 16 of agent_order in personas_meta.json are used.
"""

import argparse
import json
import os
import sys

import numpy as np
from scipy import stats as sps

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from app.services import network_chat as nc  # noqa: E402


def agent_ids_from(sim_dir, given, n=16):
    if given:
        return [int(x) for x in given.split(",") if x.strip()]
    with open(os.path.join(sim_dir, "personas_meta.json"), encoding="utf-8") as f:
        order = json.load(f).get("agent_order") or []
    if len(order) < n:
        raise SystemExit(f"agent_order has {len(order)} agents, need {n}; pass --agent-ids")
    return [int(a) for a in order[:n]]


def exclusions_for(ids, game):
    size = 2 if game == "pd" else 4
    out = {}
    for k in range(0, len(ids), size):
        grp = ids[k:k + size]
        for a in grp:
            out[a] = set(grp) - {a}
    return out


def preview(sim_dir, ids, topology="ba", beta=1.0, reply="reach", seeds=20, rounds=10, game="pd",
            topic_weight=0.5, mean_degree=4.0, contact_mean=1.0, dispersion=0.5, max_initiate=3, max_load=4):
    profiles = nc.channel_profiles(sim_dir)
    if profiles is None:
        raise SystemExit(f"{sim_dir} has no persona channels (run add_channels.py first)")
    missing = [a for a in ids if a not in profiles["people"]]
    if missing:
        raise SystemExit(f"agents without channels: {missing}")
    compat = nc.compatibility(profiles, topic_weight)
    sub = [compat.compat[(min(a, b), max(a, b))] for i, a in enumerate(sorted(ids)) for b in sorted(ids)[i + 1:]]
    A_all = np.array(list(compat.compat.values()))
    excl = exclusions_for(ids, game)
    xs, ys = [], []
    convs_per_round, unanswered, total, reach_used = [], 0, 0, []
    per_seed_rho = []
    for seed in range(seeds):
        net = nc.build_network(ids, excl, topology, mean_degree, 0.1, seed)
        lam = nc.draw_lambdas(ids, contact_mean, dispersion, seed)
        dyad = nc.dyad_sampling(compat, net["neighbors"], beta, reply)
        count = {tuple(e): 0 for e in net["edges"]}
        for r in range(1, rounds + 1):
            convs, _ = nc.sample_contacts(net["neighbors"], lam, r, seed, max_initiate, max_load, dyad=dyad)
            convs_per_round.append(len(convs))
            for c in convs:
                count[(min(c["initiator"], c["responder"]), max(c["initiator"], c["responder"]))] += 1
                total += 1
                unanswered += int(not c["replied"])
                reach_used.append(compat.reach[(c["initiator"], c["responder"])])
        a = [compat.compat[e] for e in count]
        n = [count[e] for e in count]
        xs += a
        ys += n
        if len(set(n)) > 1:
            per_seed_rho.append(float(sps.spearmanr(a, n)[0]))
    rho = float(sps.spearmanr(xs, ys)[0]) if len(set(ys)) > 1 else float("nan")
    return dict(
        n_agents=len(ids), topology=topology, beta=beta, reply=reply, seeds=seeds, rounds=rounds,
        A_all_population=dict(mean=float(A_all.mean()), sd=float(A_all.std()), min=float(A_all.min()), max=float(A_all.max())),
        A_selected_agents=dict(mean=float(np.mean(sub)), sd=float(np.std(sub)), min=float(np.min(sub)), max=float(np.max(sub))),
        spearman_A_vs_conversations_pooled=rho,
        spearman_per_seed_mean=float(np.mean(per_seed_rho)) if per_seed_rho else None,
        conversations_per_round=float(np.mean(convs_per_round)),
        conversations_total=total,
        unanswered_share=unanswered / total if total else None,
        mean_reach_of_started=float(np.mean(reach_used)) if reach_used else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim-dir", required=True)
    ap.add_argument("--topology", default="ba", choices=nc.NET_TOPOLOGIES[1:])
    ap.add_argument("--beta", type=float, default=1.0)
    ap.add_argument("--reply", default="reach", choices=nc.REPLY_MODELS)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--agent-ids", default=None, help="comma list; default: first 16 of agent_order")
    ap.add_argument("--game", default="pd", choices=("pd", "pgg"))
    ap.add_argument("--topic-weight", type=float, default=0.5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    ids = agent_ids_from(args.sim_dir, args.agent_ids)
    res = preview(args.sim_dir, ids, args.topology, args.beta, args.reply, args.seeds, args.rounds,
                  args.game, args.topic_weight)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"agents {ids}")
    for k, v in res.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
