"""Tests for scripts/experiment/dashboard.py (static Altair dashboard).

Real-run tests are skipped when the scratchpad runs are missing; the synthetic
network fixture follows the network.json / network_contacts.jsonl /
network_chat.jsonl contract and always runs.
"""

import json
import math
import os
import random
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts", "experiment"))

pytest.importorskip("altair")
import altair as alt  # noqa: E402
import dashboard  # noqa: E402

SP = "/private/tmp/claude-502/-Users-nakaoka-workspace-research/ea7149a8-9259-49d6-9bd7-9bfc4185f5b8/scratchpad"
NASTY = "</script><b>&\"'△"


def run_dash(csv_path, sim_dir=None, out=None, **kw):
    argv = ["--otree-csv", str(csv_path), "--out", str(out)]
    if sim_dir:
        argv += ["--sim-dir", str(sim_dir)]
    for k, v in kw.items():
        argv += ["--" + k.replace("_", "-"), str(v)]
    args = dashboard.argparse.Namespace(
        otree_csv=str(csv_path), sim_dir=str(sim_dir) if sim_dir else None, session=kw.get("session"),
        baseline_csv=kw.get("baseline_csv"), out=str(out), max_chars=kw.get("max_chars", 600), title=None)
    return dashboard.build(args)


def spec_from_html(page):
    m = re.search(r'<script type="application/json" id="spec">(.*?)</script>', page, re.S)
    assert m, "spec script tag missing"
    return json.loads(m.group(1))


def store_params(spec):
    return {p["name"] for p in spec.get("params", []) if "select" in p}


def all_dataset_rows(spec):
    return {k: len(v) for k, v in spec.get("datasets", {}).items()}


# ------------------------------------------------------------------ real runs
REAL = [
    ("pd_chat", "export_chat_26b/pd_debate_custom.csv", "sim_chat26", 80, True),
    ("pd_nochat", "export_pdwork26/pd_debate_custom.csv", "sim_pdwork26", 160, False),
    ("pgg", "export_pggwork26/pgg_custom.csv", "sim_pggwork26", 160, False),
]


@pytest.mark.parametrize("name,csv_rel,sim_rel,nrows,has_chat", REAL)
def test_real_runs(tmp_path, name, csv_rel, sim_rel, nrows, has_chat):
    csv_path = os.path.join(SP, csv_rel)
    sim_dir = os.path.join(SP, sim_rel)
    if not (os.path.exists(csv_path) and os.path.isdir(sim_dir)):
        pytest.skip("scratchpad run missing")
    res = run_dash(csv_path, sim_dir, tmp_path / "d.html")
    page = res["html"]
    res["chart"].to_dict(validate=True)
    assert "vegaEmbed" in page
    spec = spec_from_html(page)
    body_spec = re.search(r'id="spec">(.*?)</script>', page, re.S).group(1)
    assert "</script" not in body_spec.lower()
    assert len(page.encode("utf-8")) < 2_000_000
    # one decision row per CSV row (dataset holding 'row0' is the decisions table)
    counts = all_dataset_rows(spec)
    assert nrows in counts.values()
    assert store_params(spec) >= {"agent", "rnd"}
    titles = json.dumps(spec["vconcat"])
    assert ("Conversation storyline" in titles) == has_chat
    assert ("Messages" in titles) == has_chat
    assert "Network (" not in titles
    assert "llm_answers" not in page and '"prompt"' not in page


def test_baseline_overlay(tmp_path):
    csv_path = os.path.join(SP, "export_chat_26b/pd_debate_custom.csv")
    base = os.path.join(SP, "export_pdwork26/pd_debate_custom.csv")
    if not (os.path.exists(csv_path) and os.path.exists(base)):
        pytest.skip("scratchpad run missing")
    res = run_dash(csv_path, os.path.join(SP, "sim_chat26"), tmp_path / "b.html", baseline_csv=base)
    assert "strokeDash" in json.dumps(res["spec"])


def test_game_detection_error(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("session_code,agent_id,round_number\ns,0,1\n")
    with pytest.raises(SystemExit):
        run_dash(p, None, tmp_path / "o.html")


# ------------------------------------------------------------------ synthetic network fixture
def make_fixture(root, n=16, rounds=10, game="pd", chat=True, network=True, seed=3):
    rnd = random.Random(seed)
    session = "synth001"
    sim = root / "sim_synth"
    gdir = sim / "game" / session
    gdir.mkdir(parents=True)
    ids = list(range(n))
    names = {i: f"Person {chr(65 + i % 26)}. {i}" for i in ids}
    (sim / "personas_meta.json").write_text(json.dumps({
        "group": "workplace", "people": [dict(agent_id=i, name=names[i], company="Kestrel", department="R&D",
                                               seniority="lead") for i in ids]}))
    syms = ["△", "□", "○", "◇"]
    labels = {"cooperate": syms[0], "defect": syms[1], "order": [syms[1], syms[0]]}
    settings = dict(policy="llm", game=game, game_params={"endowment": 20, "multiplier": 1.6, "group_size": 4}
                    if game == "pgg" else {}, num_rounds=rounds, label_unit="session", chat_turns=0,
                    net_topology="ba" if network else "none")
    (gdir / "bridge_log.jsonl").write_text(json.dumps(dict(event="configure", settings=settings, n_agents=n, labels=labels)) + "\n")

    # PD partners (0,1),(2,3),...; pgg groups of 4
    partner = {i: i ^ 1 for i in ids}
    group = {i: i // 4 + 1 for i in ids}
    edges = set()
    while network and len(edges) < int(n * 1.75):
        a, b = rnd.sample(ids, 2)
        if (partner[a] == b) if game == "pd" else (group[a] == group[b]):
            continue
        edges.add((min(a, b), max(a, b)))
    edges = sorted(edges)
    deg = {i: sum(i in e for e in edges) for i in ids}
    layout = {str(i): [math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)] for i in ids}
    if network:
        (gdir / "network.json").write_text(json.dumps(dict(
            version=1, session_code=session, game=game, topology="ba",
            params={"mean_degree_target": 4, "ba_m": 2, "graph_seed": 1, "seed": 0},
            contact={"mean": 1.0, "dispersion": 0.5, "max_initiate": 3, "max_load": 4, "turns": 2, "lambda_assign": "random"},
            exclude_partners=True,
            nodes=[dict(agent_id=i, name=names[i], display=f"{names[i]} (R&D, Kestrel)", degree=deg[i],
                        **{"lambda": round(rnd.gammavariate(0.5, 2.0), 2)}, excluded=[partner[i]]) for i in ids],
            edges=[list(e) for e in edges], excluded_edges_dropped=[], repair_edges=[],
            stats=dict(n=n, m=len(edges), mean_degree=2 * len(edges) / n, max_degree=max(deg.values())),
            layout=layout)))
    contacts, netchat = [], []
    for r in range(1, rounds + 1):
        convs, agents_rec = [], {}
        if network:
            for c, (a, b) in enumerate(rnd.sample(edges, 6)):
                convs.append(dict(conv_id=f"r{r}c{c}", initiator=a, responder=b))
            for i in ids:
                k = rnd.choice([0, 0, 1, 1, 2, 3])
                agents_rec[str(i)] = dict(**{"lambda": 1.0}, k_drawn=k, k_realized=min(k, 2), initiated=[], received=[], dropped=0)
            contacts.append(dict(ts=0, round_number=r, agents=agents_rec, conversations=convs))
            for cv in convs:
                for t in range(2):
                    sp, ot = (cv["initiator"], cv["responder"]) if t == 0 else (cv["responder"], cv["initiator"])
                    txt = NASTY + f" round {r} turn {t}" if (r == 1 and t == 0 and cv["conv_id"] == "r1c0") else \
                        f"I will pick {syms[0] if rnd.random() < .5 else syms[1]} this round, {r}."
                    netchat.append(dict(ts=0, round_number=r, conv_id=cv["conv_id"], turn=t, wave=t, attempt=1, agent_id=sp,
                                        other_agent_id=ot, initiator=cv["initiator"], message=txt, parse_error=None,
                                        seen_conv_ids=[], prompt="SECRET PROMPT", response="SECRET RESPONSE"))
    if network:
        (gdir / "network_contacts.jsonl").write_text("\n".join(json.dumps(c) for c in contacts) + "\n")
        (gdir / "network_chat.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in netchat) + "\n")
    import csv
    path = root / ("pd_debate_custom.csv" if game == "pd" else "pgg_custom.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        if game == "pd":
            cols = ["session_code", "participant_label", "agent_id", "round_number", "pair_id", "partner_agent_id", "choice",
                    "cooperated", "partner_choice", "payoff", "decision_source", "decision_missing", "decision_reason", "chat_transcript"]
        else:
            cols = ["session_code", "participant_label", "agent_id", "round_number", "group_id", "contribution",
                    "others_contributions", "group_total", "earnings", "decision_source", "decision_missing", "decision_reason"]
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in range(1, rounds + 1):
            for i in ids:
                miss = int(rnd.random() < 0.03)
                if game == "pd":
                    c = int(rnd.random() < 0.6)
                    w.writerow(dict(session_code=session, participant_label=f"agent_{i}", agent_id=i, round_number=r,
                                    pair_id=i // 2 + 1, partner_agent_id=partner[i], choice="A" if c else "B", cooperated=c,
                                    partner_choice="A", payoff=30.0, decision_source="llm:test", decision_missing=miss,
                                    decision_reason="because " * 10, chat_transcript=""))
                else:
                    c = rnd.choice([0, 5, 10, 20])
                    w.writerow(dict(session_code=session, participant_label=f"agent_{i}", agent_id=i, round_number=r,
                                    group_id=group[i], contribution=c, others_contributions="[10, 20, 0]", group_total=30,
                                    earnings=25.0, decision_source="llm:test", decision_missing=miss, decision_reason="ok"))
    return path, sim


def test_network_fixture_pd(tmp_path):
    csv_path, sim = make_fixture(tmp_path)
    res = run_dash(csv_path, sim, tmp_path / "n.html")
    res["chart"].to_dict(validate=True)
    page = res["html"]
    spec = spec_from_html(page)  # round trip through JSON in the HTML
    texts = [row.get("text", "") for rows in spec["datasets"].values() for row in rows]
    assert any(t.startswith(NASTY) for t in texts)
    assert "</script><b>" not in page  # escaped in spec and transcript
    assert "SECRET" not in page
    titles = json.dumps(spec["vconcat"])
    for t in ("Network (", "Contacts drawn", "Degree vs cooperation", "Conversation storyline", "Messages"):
        assert t in titles
    assert store_params(spec) >= {"agent", "rnd"}
    agent = next(p for p in spec["params"] if p["name"] == "agent")
    assert len(agent["views"]) >= 4  # heatmap, storyline dots, network nodes, scatter
    assert len(res["msgs"]) == 6 * 10 * 2
    assert set(res["msgs"]["kind"]) == {"network"}
    assert len(page.encode("utf-8")) < 2_000_000
    # transcript html: escaped text
    assert "&lt;/script&gt;&lt;b&gt;&amp;" in page
    # contacts dataset row count
    assert len(res["net"]["contacts"]) == 16 * 10


def test_network_fixture_pgg(tmp_path):
    csv_path, sim = make_fixture(tmp_path, n=8, rounds=4, game="pgg")
    res = run_dash(csv_path, sim, tmp_path / "g.html")
    res["chart"].to_dict(validate=True)
    assert res["net"] is not None and len(res["net"]["partner_links"]) > 0


def test_no_chat_no_message_charts(tmp_path):
    csv_path, sim = make_fixture(tmp_path, chat=False, network=False)
    res = run_dash(csv_path, sim, tmp_path / "c.html")
    titles = json.dumps(res["spec"]["vconcat"])
    assert "Conversation storyline" not in titles and "Messages" not in titles and "Network (" not in titles
    assert len(res["spec"]["vconcat"]) == 2
    assert "transcripts" not in res["html"].lower()


def test_csv_fallback_transcripts(tmp_path):
    # pair chat + network transcripts only in the CSV (no jsonl), incl. absent columns tolerated
    import pandas as pd
    csv_path, sim = make_fixture(tmp_path, n=4, rounds=2, network=False)
    df = pd.read_csv(csv_path)
    df["chat_transcript"] = [json.dumps([{"agent_id": a, "text": "hi △"}, {"agent_id": p, "text": "ok"}])
                             for a, p in zip(df["agent_id"], df["partner_agent_id"])]
    df.to_csv(csv_path, index=False)
    res = run_dash(csv_path, sim, tmp_path / "f.html")
    assert set(res["msgs"]["kind"]) == {"pair"}
    assert len(res["msgs"]) == 2 * 2 * 2  # 2 pairs x 2 rounds x 2 messages
    assert set(res["msgs"]["stance"]) <= {"only cooperate", "neither"}
