"""
Static interactive dashboard (Altair / Vega-Lite) for one oTree x MiroFish session.

Reads
  - the oTree custom export (pd_debate_custom.csv or pgg_custom.csv)
  - <sim_dir>/personas_meta.json, reddit_profiles.json (names, persona set)
  - <sim_dir>/game/<session>/bridge_log.jsonl (configure event: settings, labels)
  - .../chat.jsonl (pair chat), network_chat.jsonl, network.json,
    network_contacts.jsonl (network runs; all optional)
  - dyads.json, memory_shown.jsonl (channel dyads / decaying memory, NOTES.md #55; optional)
  - the logs analyze_betrayal.py recomputes betrayal events from (NOTES.md #57; a section
    "Revealed choices and consistency" appears when the run has statements or game events)
and writes ONE self-contained HTML file (vega / vega-lite / vega-embed are loaded
from jsdelivr, so viewing needs internet). Prompts and raw LLM responses are never
embedded; only parsed messages (truncated), short decision reasons and numbers.

Usage:
    python scripts/experiment/dashboard.py --otree-csv export/pd_debate_custom.csv \
        [--sim-dir SIM_DIR] [--session CODE] [--baseline-csv CONTROL.csv] \
        [--out PATH] [--max-chars 600] [--title TITLE]

Click a heatmap cell / storyline dot / network node to select an agent, click a
round (rate chart point, heatmap cell) to select a round; double-click clears.
The search box filters messages (plain case-insensitive substring).
"""

import argparse
import warnings
import csv
import html
import json
import math
import os
import re
import sys
from collections import defaultdict

import altair as alt
import numpy as np
import pandas as pd
from altair.vegalite import v6 as _v6

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # analyze_betrayal (NOTES.md #57)

# colour-blind-safe pair (Okabe-Ito blue / vermillion), neutral grey for missing
C_COOP = "#0072B2"
C_DEFECT = "#D55E00"
C_BOTH = "#CC79A7"
C_GREY = "#9a9a9a"
C_INK = "#222222"
STANCE_DOMAIN = ["only cooperate", "only defect", "both", "neither"]
STANCE_RANGE = [C_COOP, C_DEFECT, C_BOTH, C_GREY]
STATUS_DOMAIN = ["cooperate", "defect", "missing"]
STATUS_RANGE = [C_COOP, C_DEFECT, C_GREY]
SEQ_SCHEME = "blues"

BASE_W = 340
ROW_H = 16
TABLE_ROWS = 40
TABLE_W = 800


# ----------------------------------------------------------------------------- loading
def _jsonl(path):
    out = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


def _json(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return None
    return None


def _clean(text, max_chars):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars and len(t) > max_chars:
        t = t[: max(1, max_chars - 1)].rstrip() + "\u2026"
    return t


def _num(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def detect_game(df):
    if "cooperated" in df.columns:
        return "pd"
    if "contribution" in df.columns:
        return "pgg"
    raise SystemExit("error: CSV has neither a 'cooperated' (pd) nor a 'contribution' (pgg) column")


def load_names(sim_dir, ids):
    names = {}
    if sim_dir:
        meta = _json(os.path.join(sim_dir, "personas_meta.json")) or {}
        for p in meta.get("people", []) or []:
            if "agent_id" in p and p.get("name"):
                names[int(p["agent_id"])] = str(p["name"])
        prof = _json(os.path.join(sim_dir, "reddit_profiles.json"))
        if isinstance(prof, list):
            for i, p in enumerate(prof):
                uid = p.get("user_id", i)
                if int(uid) not in names and p.get("name"):
                    names[int(uid)] = str(p["name"])
    for i in ids:
        names.setdefault(int(i), f"Agent {i}")
    return names


def persona_set(sim_dir):
    meta = _json(os.path.join(sim_dir, "personas_meta.json")) if sim_dir else None
    return (meta or {}).get("group") or "general"


def labels_for(labels, aid):
    """labels dict is either {cooperate, defect, order} or {agent_id: {...}}."""
    if not labels:
        return None
    if "cooperate" in labels:
        return labels
    return labels.get(str(aid))


def load_run(args):
    df = pd.read_csv(args.otree_csv, dtype={"session_code": str})
    game = detect_game(df)
    sessions = sorted(df["session_code"].dropna().unique())
    if args.session:
        df = df[df["session_code"] == args.session]
        if df.empty:
            raise SystemExit(f"error: session {args.session} not in CSV (have {sessions})")
        session = args.session
    else:
        if len(sessions) != 1:
            raise SystemExit(f"error: CSV has sessions {sessions}; pass --session")
        session = sessions[0]
    df = df.reset_index(drop=True)
    sim_dir = args.sim_dir
    gdir = os.path.join(sim_dir, "game", session) if sim_dir else None
    if gdir and not os.path.isdir(gdir):
        gdir = None

    cfg = {}
    for ev in _jsonl(os.path.join(gdir, "bridge_log.jsonl")) if gdir else []:
        if ev.get("event") == "configure":
            cfg = ev
    return dict(df=df, game=game, session=session, sim_dir=sim_dir, gdir=gdir, cfg=cfg)


# ----------------------------------------------------------------------------- tables
def build_decisions(run, names, max_reason=240):
    df, game, cfg = run["df"], run["game"], run["cfg"]
    settings = cfg.get("settings", {}) or {}
    labels = cfg.get("labels") or {}
    gp = settings.get("game_params") or {}
    endow = float(gp.get("endowment", 20) or 20)

    if game == "pd":
        gcol = "pair_id" if "pair_id" in df.columns else None
    else:
        gcol = "group_id" if "group_id" in df.columns else None
    first = df.drop_duplicates("agent_id")
    order_key = {}
    for _, r in first.iterrows():
        order_key[int(r["agent_id"])] = (int(r[gcol]) if gcol and not pd.isna(r[gcol]) else 0, int(r["agent_id"]))
    agents = sorted(order_key, key=lambda a: order_key[a])
    row_of = {a: i for i, a in enumerate(agents)}
    label_of = {a: f"{names[a]} #{a}" for a in agents}

    groups = defaultdict(set)
    if game == "pgg" and gcol:
        for _, r in df.drop_duplicates(["agent_id", gcol]).iterrows():
            groups[int(r[gcol])].add(int(r["agent_id"]))

    recs = []
    for _, r in df.iterrows():
        a = int(r["agent_id"])
        rn = int(r["round_number"])
        missing = bool(_num(r.get("decision_missing"), 0))
        rec = dict(agent_id=a, label=label_of[a], row=row_of[a], round_number=rn,
                   x0=rn - 0.5, x1=rn + 0.5, missing=missing,
                   group_id=int(r[gcol]) if gcol and not pd.isna(r[gcol]) else 0,
                   source=str(r.get("decision_source", "") or ""),
                   reason=_clean(r.get("decision_reason", ""), max_reason))
        if game == "pd":
            lab = labels_for(labels, a) or {}
            coop = None if missing else int(_num(r["cooperated"], 0))
            pc = str(r.get("partner_choice", "") or "")
            rec.update(
                coop=None if coop is None else float(coop),
                status="missing" if missing else ("cooperate" if coop else "defect"),
                shown=(lab.get("cooperate", "?") if coop else lab.get("defect", "?")) if coop is not None else "",
                partner=label_of.get(int(r["partner_agent_id"]), "") if not pd.isna(r.get("partner_agent_id")) else "",
                partner_choice="cooperate" if pc == "A" else ("defect" if pc == "B" else ""),
                payoff=_num(r.get("payoff")),
                dark=(coop is not None),
                value="cooperate" if coop else ("defect" if coop is not None else "missing"))
            rec["pair_id"] = rec["group_id"]
        else:
            c = _num(r.get("contribution"))
            share = None if (missing or c is None) else c / endow
            rec.update(coop=share, status="missing" if missing else "share",
                       shown=f"{int(c)}" if c is not None else "",
                       partner=", ".join(label_of.get(m, str(m)) for m in sorted(groups[rec["group_id"]] - {a})),
                       partner_choice=str(r.get("others_contributions", "") or ""),
                       payoff=_num(r.get("earnings")),
                       dark=bool(share is not None and share > 0.55),
                       value=f"{int(c)} of {int(endow)}" if c is not None else "missing")
        recs.append(rec)
    dec = pd.DataFrame(recs)
    return dec, agents, label_of, row_of, endow


def build_messages(run, names, max_chars):
    """Return DataFrame of messages (one row per message)."""
    df, game, gdir, cfg = run["df"], run["game"], run["gdir"], run["cfg"]
    labels = cfg.get("labels") or {}
    msgs = []

    def add(kind, rn, conv, turn, wave, sp, li, text):
        text = _clean(text, max_chars)
        if not text:
            return
        msgs.append(dict(kind=kind, round_number=int(rn), conv_id=str(conv), turn=int(turn),
                         wave=None if wave is None else int(wave), speaker_id=int(sp), listener_id=int(li), text=text))

    # pair chat: chat.jsonl (last attempt with a message per (round, turn, agent)), else CSV
    pair_rows = {}
    for r in _jsonl(os.path.join(gdir, "chat.jsonl")) if gdir else []:
        if r.get("message"):
            pair_rows[(r["round_number"], r["turn"], r["agent_id"])] = r
    if pair_rows:
        for (rn, turn, aid), r in sorted(pair_rows.items()):
            sp, li = int(aid), int(r["partner_agent_id"])
            add("pair", rn, f"r{rn}p{min(sp, li)}-{max(sp, li)}", turn, None, sp, li, r["message"])
    elif "chat_transcript" in df.columns:
        seen = set()
        for _, r in df.iterrows():
            raw = r.get("chat_transcript")
            if not isinstance(raw, str) or not raw.strip():
                continue
            a, p, rn = int(r["agent_id"]), r.get("partner_agent_id"), int(r["round_number"])
            if pd.isna(p):
                continue
            p = int(p)
            key = (rn, min(a, p), max(a, p))
            if key in seen:
                continue
            seen.add(key)
            try:
                arr = json.loads(raw)
            except ValueError:
                continue
            for t, m in enumerate(arr):
                sp = int(m.get("agent_id", a))
                add("pair", rn, f"r{rn}p{key[1]}-{key[2]}", t, None, sp, p if sp == a else a, m.get("text"))

    # network chat
    net_rows = {}
    for r in _jsonl(os.path.join(gdir, "network_chat.jsonl")) if gdir else []:
        if r.get("message"):
            net_rows[(r["conv_id"], r["turn"])] = r
    if net_rows:
        for (cid, turn), r in sorted(net_rows.items(), key=lambda kv: (kv[1]["round_number"], kv[1].get("wave") or 0, kv[0][1], kv[0][0])):
            add("network", r["round_number"], cid, turn, r.get("wave"), r["agent_id"], r["other_agent_id"], r["message"])
    elif "network_transcript" in df.columns:
        seen = set()
        for _, r in df.iterrows():
            raw = r.get("network_transcript")
            if not isinstance(raw, str) or not raw.strip():
                continue
            a, rn = int(r["agent_id"]), int(r["round_number"])
            try:
                arr = json.loads(raw)
            except ValueError:
                continue
            for conv in arr:
                cid = conv.get("conv_id")
                if cid in seen:
                    continue
                seen.add(cid)
                o = int(conv["other_agent_id"])
                for t, m in enumerate(conv.get("messages", [])):
                    sp = int(m["agent_id"])
                    add("network", rn, cid, t, None, sp, o if sp == a else a, m.get("text"))

    if not msgs:
        return pd.DataFrame(columns=["msg_id", "kind", "round_number", "conv_id", "turn", "wave", "speaker_id",
                                     "listener_id", "speaker", "listener", "text", "stance", "x", "agent_id"])
    m = pd.DataFrame(msgs)
    m["slot"] = [(w if (k == "network" and w is not None and not pd.isna(w)) else t)
                 for k, w, t in zip(m["kind"], m["wave"], m["turn"])]
    m["slot"] = m["slot"].astype(int)
    nslots = m.groupby("round_number")["slot"].transform(lambda s: s.max() + 1)
    m["x"] = m["round_number"] - 0.4 + 0.8 * (m["slot"] + 0.5) / nslots
    m = m.sort_values(["round_number", "x", "turn"]).reset_index(drop=True)
    m["msg_id"] = range(len(m))
    m["speaker"] = m["speaker_id"].map(lambda a: names.get(int(a), f"Agent {a}"))
    m["listener"] = m["listener_id"].map(lambda a: names.get(int(a), f"Agent {a}"))
    m["agent_id"] = m["speaker_id"]

    def stance(a, text):
        if game == "pgg":
            return "message"
        lab = labels_for(labels, a)
        if not lab:
            return "neither"
        c, d = lab.get("cooperate") in text, lab.get("defect") in text
        return "both" if (c and d) else "only cooperate" if c else "only defect" if d else "neither"

    m["stance"] = [stance(a, t) for a, t in zip(m["speaker_id"], m["text"])]
    m["line"] = [f"R{r} {s} \u2192 {l}: {t}" for r, s, l, t in zip(m["round_number"], m["speaker"], m["listener"], m["text"])]
    return m.drop(columns=["slot"])


def load_network(run, names, dec, msgs):
    gdir = run["gdir"]
    net = _json(os.path.join(gdir, "network.json")) if gdir else None
    if not net or not net.get("nodes"):
        return None
    contacts = _jsonl(os.path.join(gdir, "network_contacts.jsonl"))
    ids = sorted({int(n["agent_id"]) for n in net["nodes"]})
    layout = net.get("layout") or {}
    if not all(str(i) in layout for i in ids):
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from(ids)
        G.add_edges_from([tuple(e) for e in net.get("edges", [])])
        layout = {str(k): list(v) for k, v in nx.spring_layout(G, seed=0).items()}
    pos = {i: layout[str(i)] for i in ids}

    # conversations (per round) from contacts, else from messages
    convs = []
    for c in contacts:
        for cv in c.get("conversations", []):
            convs.append((int(c["round_number"]), int(cv["initiator"]), int(cv["responder"]),
                          cv.get("replied") is not False))
    if not convs and len(msgs):
        nm = msgs[msgs["kind"] == "network"].drop_duplicates("conv_id")
        for _, r in nm.iterrows():
            convs.append((int(r["round_number"]), int(r["speaker_id"]), int(r["listener_id"]), True))
    per_agent = defaultdict(int)
    per_edge = defaultdict(int)
    per_edge_unanswered = defaultdict(int)
    edge_round = set()
    for rn, a, b, replied in convs:
        per_agent[a] += 1
        per_agent[b] += 1
        per_edge[(min(a, b), max(a, b))] += 1
        per_edge_unanswered[(min(a, b), max(a, b))] += int(not replied)
        edge_round.add((rn, min(a, b), max(a, b)))
    attrs = {(int(e["a"]), int(e["b"])): e for e in net.get("edge_attrs") or []}
    coop = dec.groupby("agent_id")["coop"].mean()
    nodes = []
    for n in net["nodes"]:
        a = int(n["agent_id"])
        cr = coop.get(a)
        nodes.append(dict(agent_id=a, label=f"{names[a]} #{a}", name=n.get("display") or names[a],
                          x=pos[a][0], y=pos[a][1], degree=int(n.get("degree", 0)),
                          **{"lambda": float(n.get("lambda", 0) or 0)},
                          convs=per_agent.get(a, 0), coop=None if cr is None or pd.isna(cr) else float(cr)))
    edges = []
    for e in net.get("edges", []):
        a, b = int(e[0]), int(e[1])
        key = (min(a, b), max(a, b))
        row = dict(a=a, b=b, x=pos[a][0], y=pos[a][1], x2=pos[b][0], y2=pos[b][1],
                   convs=per_edge.get(key, 0))
        if attrs:  # channel dyads (#55)
            n_c, n_u = per_edge.get(key, 0), per_edge_unanswered.get(key, 0)
            row.update(compat=float(attrs[key]["compat"]) if key in attrs else None,
                       unanswered=n_u, answered=n_c - n_u,
                       reply_share=(n_c - n_u) / n_c if n_c else None)
        edges.append(row)
    er = [dict(round_number=rn, x=pos[a][0], y=pos[a][1], x2=pos[b][0], y2=pos[b][1])
          for rn, a, b in sorted(edge_round) if a in pos and b in pos]
    plinks = []
    if "partner" in dec.columns and run["game"] == "pd":
        seen = set()
        for _, r in run["df"].drop_duplicates("agent_id").iterrows():
            p = r.get("partner_agent_id")
            if pd.isna(p):
                continue
            a, b = int(r["agent_id"]), int(p)
            k = (min(a, b), max(a, b))
            if k in seen or a not in pos or b not in pos:
                continue
            seen.add(k)
            plinks.append(dict(x=pos[a][0], y=pos[a][1], x2=pos[b][0], y2=pos[b][1]))
    elif run["game"] == "pgg":
        grp = run["df"].drop_duplicates("agent_id").groupby("group_id")["agent_id"].apply(list)
        for members in grp:
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if a in pos and b in pos:
                        plinks.append(dict(x=pos[a][0], y=pos[a][1], x2=pos[b][0], y2=pos[b][1]))

    kd = []
    for c in contacts:
        for aid, rec in (c.get("agents") or {}).items():
            kd.append(dict(agent_id=int(aid), round_number=int(c["round_number"]),
                           k_drawn=int(rec.get("k_drawn", 0)), k_realized=int(rec.get("k_realized", 0))))
    kd = pd.DataFrame(kd, columns=["agent_id", "round_number", "k_drawn", "k_realized"])
    mu = float((net.get("contact") or {}).get("mean", 1.0))
    r = float((net.get("contact") or {}).get("dispersion", 0.0))
    nb = pd.DataFrame(columns=["k", "count", "expected"])
    if len(kd):
        from scipy import stats as st
        counts = kd["k_drawn"].value_counts()
        kmax = int(max(counts.index.max(), 6))
        ks = np.arange(0, kmax + 1)
        pmf = st.nbinom(r, r / (r + mu)).pmf(ks) if r > 0 else st.poisson(mu).pmf(ks)
        nb = pd.DataFrame(dict(k=ks, count=[int(counts.get(int(k), 0)) for k in ks], expected=pmf * len(kd)))
    dyads_doc = _json(os.path.join(gdir, "dyads.json")) if attrs else None
    all_compat = [float(x["compat"]) for x in (dyads_doc or {}).get("dyads", []) if x.get("compat") is not None]
    return dict(raw=net, nodes=pd.DataFrame(nodes), edges=pd.DataFrame(edges), edge_round=pd.DataFrame(
        er, columns=["round_number", "x", "y", "x2", "y2"]), partner_links=pd.DataFrame(
        plinks, columns=["x", "y", "x2", "y2"]), contacts=kd, nb=nb, mu=mu, r=r,
        compat=bool(attrs), all_compat=all_compat)


def load_memory(run):
    """memory_shown.jsonl (net_memory_mode 'decay') -> one row per remembered item of a
    decision prompt, plus characters per decision; None when the run has no memory log."""
    rows = [r for r in (_jsonl(os.path.join(run["gdir"], "memory_shown.jsonl")) if run["gdir"] else [])
            if r.get("purpose") == "decision"]
    if not rows:
        return None
    items, chars = [], {}
    for r in rows:
        chars[(int(r["agent_id"]), int(r["round_number"]))] = int(r["chars"])
        for i in r["items"]:
            if i.get("kind") == "note":  # revealed choices (#57) are no conversation
                continue
            items.append(dict(agent_id=int(r["agent_id"]), decision_round=int(r["round_number"]),
                              conv_round=int(i["round"]), tier=i["tier"], weight=float(i["weight"]),
                              other=i["other"], delta=int(i["delta"])))
    return dict(items=pd.DataFrame(items, columns=["agent_id", "decision_round", "conv_round", "tier",
                                                   "weight", "other", "delta"]), chars=chars)


def load_betrayal(run, net):
    """Statements / game events per round, per-agent consistency and the share of initiations to
    neighbours with something bad revealed (analyze_betrayal.py); None when the run has none."""
    if not run["gdir"]:
        return None
    try:
        import analyze_betrayal as AB
        sess = AB.read_session(run["gdir"])
        off = AB.events_from_logs(run["gdir"], sess)
        convs = AB.conversations(sess)
        stat = AB.statements(sess, off, convs)
    except Exception:  # noqa: BLE001 -- no outcomes / labels / chat: nothing to show
        return None
    game_rows = []
    for t in off["rounds"]:
        for e in off["events"][t]:
            if e["type"] == "game":
                game_rows.append(dict(round_number=t, kind=e["kind"]))
    if not stat["statements"] and not game_rows:
        return None
    srows = []
    for r in stat["by_round"]:
        for k, v in (("kept", r["kept"]), ("broken", r["broken"]), ("ambiguous", r["ambiguous"] + r["hedged"])):
            srows.append(dict(round_number=r["round"], kind=k, n=v))
    grows = pd.DataFrame(game_rows, columns=["round_number", "kind"])
    grows = grows.groupby(["round_number", "kind"]).size().reset_index(name="n") if len(grows) else \
        pd.DataFrame(columns=["round_number", "kind", "n"])
    deg = {int(n["agent_id"]): int(n.get("degree", 0)) for n in ((net or {}).get("raw") or {}).get("nodes", [])}
    ag = pd.DataFrame(AB.consistency_by_round(sess, off), columns=["agent_id", "round", "stated", "consistency", "coop"])
    ag = ag.rename(columns={"round": "round_number"})
    if len(ag) and deg:
        order = sorted(deg, key=lambda a: (deg[a], a))
        third = {a: ["low degree", "middle degree", "high degree"][min(2, 3 * i // len(order))] for i, a in enumerate(order)}
        ag["degree_tercile"] = ag["agent_id"].map(third).fillna("n/a")
    else:
        ag["degree_tercile"] = "n/a"
    ish = pd.DataFrame(AB.initiation_shares(sess, off, convs),
                       columns=["round", "initiations", "observed", "expected_base", "expected_with_reputation"])
    ish = ish.rename(columns={"round": "round_number"})
    return dict(statements=pd.DataFrame(srows, columns=["round_number", "kind", "n"]), game=grows, agents=ag,
                shares=ish, reveal=sess["settings"].get("net_reveal_choices", "none"),
                rho=(sess["settings"].get("net_reputation_word_weight", 0.0) or 0.0)
                + (sess["settings"].get("net_reputation_choice_weight", 0.0) or 0.0))


def build_rounds(run, dec, msgs, baseline_csv):
    g = dec.groupby("round_number")
    rounds = pd.DataFrame(dict(rate=g["coop"].mean(), n=g["agent_id"].count(), missing=g["missing"].sum())).reset_index()
    rounds["messages"] = rounds["round_number"].map(msgs.groupby("round_number").size() if len(msgs) else {}).fillna(0).astype(int)
    rounds["series"] = "this run"
    base = None
    if baseline_csv:
        b = pd.read_csv(baseline_csv, dtype={"session_code": str})
        if "cooperated" in b.columns and run["game"] == "pd":
            s = b.groupby("round_number")["cooperated"].mean()
        elif "contribution" in b.columns and run["game"] == "pgg":
            ce = b.groupby("round_number")["contribution"].mean()
            s = ce / float((run["cfg"].get("settings", {}).get("game_params") or {}).get("endowment", 20) or 20)
        else:
            raise SystemExit("error: --baseline-csv game differs from --otree-csv")
        base = pd.DataFrame(dict(round_number=s.index.astype(int), rate=s.values, series="baseline"))
    grp = None
    if run["game"] == "pgg":
        grp = dec.groupby(["group_id", "round_number"])["coop"].mean().reset_index()
        grp["group"] = "group " + grp["group_id"].astype(str)
    return rounds, base, grp


# ----------------------------------------------------------------------------- charts
STORE_AGENT = "length(data('agent_store'))>0 && vlSelectionTest('agent_store', datum)"
STORE_RND = "length(data('rnd_store'))>0 && vlSelectionTest('rnd_store', datum)"
Q_FILTER = "indexof(lower(datum.text), lower(q))>=0"


def _title(text, sub):
    return alt.TitleParams(text=text, subtitle=[sub], anchor="start", fontSize=14, subtitleFontSize=11,
                           subtitleColor="#555555", color=C_INK, subtitlePadding=2)


def _y_scale(n):
    return alt.Scale(domain=[-0.5, n - 0.5], reverse=True, nice=False, zero=False)


def _y_axis(labels_list):
    n = len(labels_list)
    expr = json.dumps(labels_list, ensure_ascii=False) + "[datum.value]"
    return alt.Axis(values=list(range(n)), labelExpr=expr, labelLimit=90, title=None, grid=False,
                    ticks=False, domain=False, labelFontSize=10, labelColor=C_INK, labelOverlap=False)


def _x_scale(rounds_list):
    return alt.Scale(domain=[min(rounds_list) - 0.5, max(rounds_list) + 0.5], nice=False, zero=False)


def _x_axis(rounds_list, title="round"):
    return alt.Axis(values=list(range(min(rounds_list), max(rounds_list) + 1)), title=title, grid=False,
                    format="d", labelColor=C_INK, titleColor=C_INK, tickMinStep=1)


def _decision_color(game):
    if game == "pd":
        return alt.Color("status:N", scale=alt.Scale(domain=STATUS_DOMAIN, range=STATUS_RANGE),
                         legend=alt.Legend(title="decision", orient="top", direction="horizontal",
                                           labelColor=C_INK, titleColor=C_INK))
    return alt.condition("datum.missing", alt.value(C_GREY),
                         alt.Color("coop:Q", scale=alt.Scale(scheme=SEQ_SCHEME, domain=[0, 1]),
                                   legend=alt.Legend(title="share of endowment", orient="top", direction="horizontal",
                                                     gradientLength=120, labelColor=C_INK, titleColor=C_INK)))


def make_charts(run, dec, msgs, rounds, base, grp, net, agents, label_of, endow, memory=None, betrayal=None):
    game = run["game"]
    rlist = sorted(dec["round_number"].unique().tolist())
    nr = len(rlist)
    wide = max(BASE_W, 30 * nr)
    n = len(agents)
    ylabels = [label_of[a] for a in agents]
    cols_dec = [c for c in dec.columns]

    agent = alt.selection_point(fields=["agent_id"], on="click", clear="dblclick", name="agent")
    rnd = alt.selection_point(fields=["round_number"], on="click", clear="dblclick", name="rnd")
    q = alt.param(name="q", value="", bind=alt.binding(input="search", name="search messages "))
    has_msgs = len(msgs) > 0

    rate_title = "Cooperation rate" if game == "pd" else "Mean contribution (share of endowment)"
    y_rate = alt.Y("rate:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", title=None, labelColor=C_INK))
    x_rate = alt.X("round_number:Q", scale=alt.Scale(domain=[min(rlist) - 0.5, max(rlist) + 0.5], nice=False, zero=False),
                   axis=alt.Axis(values=rlist, format="d", title="round", grid=False, labelColor=C_INK, titleColor=C_INK))
    layers = []
    if grp is not None:
        layers.append(alt.Chart(grp).mark_line(strokeWidth=1, opacity=0.4, color=C_COOP).encode(
            x=x_rate, y=alt.Y("coop:Q", scale=alt.Scale(domain=[0, 1])), detail="group:N",
            tooltip=[alt.Tooltip("group:N"), alt.Tooltip("round_number:Q", title="round"),
                     alt.Tooltip("coop:Q", title="group mean share", format=".0%")]))
    if base is not None:
        layers.append(alt.Chart(base).mark_line(strokeDash=[5, 4], strokeWidth=2, color="#555555").encode(
            x=alt.X("round_number:Q"), y="rate:Q",
            tooltip=[alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("rate:Q", title="baseline", format=".0%")]))
    layers.append(alt.Chart(rounds).mark_line(strokeWidth=2.5, color=C_COOP).encode(x=x_rate, y=y_rate))
    layers.append(alt.Chart(rounds).mark_point(size=70, filled=True, color=C_COOP, stroke="white", strokeWidth=1.5).encode(
        x=x_rate, y=y_rate,
        opacity=alt.condition(rnd, alt.value(1), alt.value(0.85), empty=False),
        tooltip=[alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("rate:Q", title="rate", format=".0%"),
                 alt.Tooltip("n:Q", title="agents"), alt.Tooltip("missing:Q", title="missing"),
                 alt.Tooltip("messages:Q", title="messages")]).add_params(rnd))
    layers.append(alt.Chart(dec).transform_filter(STORE_AGENT).mark_line(
        strokeWidth=1.5, color=C_DEFECT, point=alt.OverlayMarkDef(filled=True, size=40, color=C_DEFECT)).encode(
        x=x_rate, y=alt.Y("coop:Q", scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("label:N", title="agent"), alt.Tooltip("round_number:Q", title="round"),
                 alt.Tooltip("value:N", title="decision")]))
    sub = ("dashed = baseline; orange = selected agent; click a point to select a round" if base is not None
           else "orange line = selected agent (click a heatmap cell); click a point to select a round")
    c_rate = alt.layer(*layers).properties(width=BASE_W, height=150, title=_title(rate_title, sub))

    # heatmap
    tip = [alt.Tooltip("label:N", title="agent"), alt.Tooltip("round_number:Q", title="round"),
           alt.Tooltip("shown:N", title="shown choice" if game == "pd" else "contribution"),
           alt.Tooltip("value:N", title="decision"),
           alt.Tooltip("partner_choice:N", title="partner choice" if game == "pd" else "others contributed"),
           alt.Tooltip("payoff:Q", title="payoff"), alt.Tooltip("reason:N", title="reason")]
    x0 = alt.X("x0:Q", scale=_x_scale(rlist), axis=_x_axis(rlist))
    dec = dec.assign(row2=dec["row"] + 0.5, row0=dec["row"] - 0.5)
    y_heat = alt.Y("row0:Q", scale=_y_scale(n), axis=_y_axis(ylabels))
    base_heat = alt.Chart(dec)
    rect = base_heat.mark_rect(stroke="white", strokeWidth=1).encode(
        x=x0, x2="x1:Q", y=y_heat, y2="row2:Q", color=_decision_color(game),
        opacity=alt.condition("datum.missing", alt.value(0.35), alt.value(1)), tooltip=tip)
    talk = base_heat.transform_filter("datum.talked").mark_text(fontSize=26, fontWeight="bold", dy=-2).encode(
        x=alt.X("xc:Q", scale=_x_scale(rlist), axis=_x_axis(rlist)), y=alt.Y("row:Q", scale=_y_scale(n), axis=_y_axis(ylabels)),
        text=alt.value("\u00b7"),
        color=alt.condition("datum.dark", alt.value("white"), alt.value(C_INK)), tooltip=tip)
    ring = base_heat.transform_filter(STORE_AGENT).mark_rect(fill=None, stroke=C_INK, strokeWidth=1.5).encode(
        x=x0, x2="x1:Q", y=y_heat, y2="row2:Q")
    heat_layers = [rect, talk, ring]
    heat_sub = ("one row per agent, one column per round; " + ("\u00b7 = agent talked that round; " if has_msgs else "")
                + "click a cell to select agent + round")
    c_heat = alt.layer(*heat_layers).properties(width=wide, height=ROW_H * n,
                                                title=_title("Decisions by agent and round", heat_sub))
    c_heat = c_heat.add_params(agent, rnd)

    charts = [c_rate, c_heat]

    # storyline + message table
    if has_msgs:
        row_of = {a: i for i, a in enumerate(agents)}
        ms = msgs[msgs["speaker_id"].isin(row_of) & msgs["listener_id"].isin(row_of)].copy()
        ms["srow"] = ms["speaker_id"].map(row_of)
        ms["lrow"] = ms["listener_id"].map(row_of)
        def X(f):
            return alt.X(f, scale=_x_scale(rlist), axis=_x_axis(rlist))

        def Y(f, axis=True):
            return alt.Y(f, scale=_y_scale(n), axis=_y_axis(ylabels))

        bg_dec = dec.assign(xa=dec["round_number"] - 0.45, xb=dec["round_number"] + 0.45)
        seg = alt.Chart(bg_dec).mark_rule(strokeWidth=6, opacity=0.35).encode(
            x=X("xa:Q"), x2="xb:Q", y=Y("row:Q", True),
            color=alt.Color("status:N", scale=alt.Scale(domain=STATUS_DOMAIN, range=STATUS_RANGE), legend=None) if game == "pd"
            else alt.condition("datum.missing", alt.value(C_GREY),
                               alt.Color("coop:Q", scale=alt.Scale(scheme=SEQ_SCHEME, domain=[0, 1]), legend=None)),
            tooltip=[alt.Tooltip("label:N", title="agent"), alt.Tooltip("round_number:Q", title="round"),
                     alt.Tooltip("value:N", title="decision")]
            + ([alt.Tooltip("memory_chars:Q", title="memory block (chars)")] if "memory_chars" in bg_dec.columns else []))
        conn = alt.Chart(ms).transform_filter(Q_FILTER).mark_rule(strokeWidth=1.2).encode(
            x=X("x:Q"), y=Y("srow:Q"), y2="lrow:Q",
            color=alt.condition('datum.kind=="pair"', alt.value("#333333"), alt.value("#8a8a8a")),
            strokeDash=alt.condition('datum.kind=="pair"', alt.value([5, 3]), alt.value([1, 0])),
            opacity=alt.value(0.8))
        dots = alt.Chart(ms).transform_filter(Q_FILTER).mark_point(filled=True, size=70, stroke="white", strokeWidth=1).encode(
            x=X("x:Q"), y=Y("srow:Q"),
            color=alt.Color("stance:N", scale=(alt.Scale(domain=STANCE_DOMAIN, range=STANCE_RANGE) if game == "pd" else alt.Scale(domain=STANCE_DOMAIN + ["message"], range=STANCE_RANGE + [C_COOP])),
                            legend=alt.Legend(title="speaker mentions", orient="top", direction="horizontal",
                                              labelColor=C_INK, titleColor=C_INK) if game == "pd" else None),
            shape=alt.Shape("kind:N", scale=alt.Scale(domain=["pair", "network"], range=["circle", "square"]),
                            legend=alt.Legend(title="chat", orient="top", direction="horizontal",
                                              labelColor=C_INK, titleColor=C_INK)),
            tooltip=[alt.Tooltip("speaker:N"), alt.Tooltip("listener:N"), alt.Tooltip("round_number:Q", title="round"),
                     alt.Tooltip("kind:N", title="chat"), alt.Tooltip("text:N")]).add_params(agent)
        c_story = alt.layer(seg, conn, dots).resolve_scale(color="independent").properties(
            width=wide, height=ROW_H * n,
            title=_title("Conversation storyline",
                         "dot = message on the speaker's row, linked to the listener's; dashed = pair chat, solid grey = network; "
                         "click a dot to select the speaker; search box is below the last chart"))
        charts.append(c_story)

        mr = pd.concat([ms.assign(role="speaker", agent_id=ms["speaker_id"]),
                        ms.assign(role="listener", agent_id=ms["listener_id"])], ignore_index=True)
        mr = mr[["msg_id", "agent_id", "role", "round_number", "x", "turn", "line", "text", "speaker", "listener", "kind"]]
        tbl = (alt.Chart(mr)
               .transform_filter("length(data('agent_store'))==0 ? datum.role=='speaker' : vlSelectionTest('agent_store', datum)")
               .transform_filter("length(data('rnd_store'))==0 || vlSelectionTest('rnd_store', datum)")
               .transform_filter(Q_FILTER)
               .transform_window(row="row_number()", sort=[alt.SortField("round_number"), alt.SortField("x"), alt.SortField("turn")])
               .transform_filter(f"datum.row<={TABLE_ROWS}")
               .mark_text(align="left", limit=TABLE_W - 10, fontSize=11, color=C_INK, baseline="middle")
               .encode(x=alt.value(2), y=alt.Y("row:O", axis=None), text="line:N",
                       tooltip=[alt.Tooltip("speaker:N"), alt.Tooltip("listener:N"),
                                alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("text:N")])
               .properties(width=TABLE_W, height=alt.Step(15),
                           title=_title("Messages", f"first {TABLE_ROWS} matches for the agent / round selection; use the search box below the last chart; hover a row for the full text")))
        charts.append(tbl)

    # network
    if net is not None:
        nodes, edges = net["nodes"], net["edges"]
        lim = 1.15
        xs_ = alt.Scale(domain=[-lim, lim], nice=False, zero=False)
        ys_ = alt.Scale(domain=[-lim, lim], nice=False, zero=False)
        def pos(field, sc, extra=None):
            return alt.X(field, scale=xs_, axis=None) if sc == "x" else alt.Y(field, scale=ys_, axis=None)
        e_tip = [alt.Tooltip("a:Q", title="agent"), alt.Tooltip("b:Q", title="agent"),
                 alt.Tooltip("convs:Q", title="conversations")]
        e_enc = {}
        if net.get("compat"):  # channel dyads (#55): edges coloured by compatibility A
            e_tip += [alt.Tooltip("compat:Q", title="compatibility", format=".2f"),
                      alt.Tooltip("unanswered:Q", title="unanswered")]
            e_enc["color"] = alt.Color("compat:Q", scale=alt.Scale(scheme="greens", domain=[0.2, 1.0]),
                                       legend=alt.Legend(title="compatibility A", orient="right", labelColor=C_INK, titleColor=C_INK))
        e_base = alt.Chart(edges).mark_rule(opacity=0.8 if e_enc else 0.7, **({} if e_enc else {"color": "#b5b5b5"})).encode(
            x=pos("x:Q", "x"), y=pos("y:Q", "y"), x2="x2:Q", y2="y2:Q",
            strokeWidth=alt.StrokeWidth("convs:Q", scale=alt.Scale(range=[0.8, 4]), legend=None),
            tooltip=e_tip, **e_enc)
        e_round = alt.Chart(net["edge_round"]).transform_filter(STORE_RND).mark_rule(color="#222222", strokeWidth=2.5).encode(
            x=pos("x:Q", "x"), y=pos("y:Q", "y"), x2="x2:Q", y2="y2:Q")
        layers = [e_base, e_round]
        if len(net["partner_links"]):
            layers.append(alt.Chart(net["partner_links"]).mark_rule(strokeDash=[3, 3], color=C_DEFECT, opacity=0.6).encode(
                x=pos("x:Q", "x"), y=pos("y:Q", "y"), x2="x2:Q", y2="y2:Q"))
        nnodes = alt.Chart(nodes).mark_point(filled=True, stroke="white", strokeWidth=1.2).encode(
            x=pos("x:Q", "x"), y=pos("y:Q", "y"),
            size=alt.Size("lambda:Q", scale=alt.Scale(range=[60, 500]), legend=alt.Legend(title="contact rate \u03bb", orient="right", labelColor=C_INK, titleColor=C_INK)),
            color=alt.Color("coop:Q", scale=alt.Scale(scheme=SEQ_SCHEME, domain=[0, 1]),
                            legend=alt.Legend(title="cooperation rate" if game == "pd" else "mean share", orient="right", format="%", labelColor=C_INK, titleColor=C_INK)),
            tooltip=[alt.Tooltip("name:N", title="agent"), alt.Tooltip("degree:Q"), alt.Tooltip("lambda:Q", title="\u03bb", format=".2f"),
                     alt.Tooltip("convs:Q", title="conversations"), alt.Tooltip("coop:Q", title="cooperation", format=".0%")]).add_params(agent)
        sel_ring = alt.Chart(nodes).transform_filter(STORE_AGENT).mark_point(filled=False, stroke=C_INK, strokeWidth=3, size=700).encode(
            x=pos("x:Q", "x"), y=pos("y:Q", "y"))
        layers += [nnodes, sel_ring]
        st = net["raw"].get("stats", {})
        topo = net["raw"].get("topology", "")
        c_net = alt.layer(*layers).resolve_scale(color="independent").properties(
            width=BASE_W, height=BASE_W,
            title=_title(f"Network ({topo}, n={st.get('n', len(nodes))}, m={st.get('m', len(edges))})",
                         ("edge colour = compatibility, " if net.get("compat") else "")
                         + "edge width = conversations; black = edges used in the selected round; dashed orange = "
                         + ("PD partner" if game == "pd" else "same group")))
        charts.append(c_net)
        if net.get("compat") and len(edges):
            ed = edges.dropna(subset=["compat"])
            parts = [pd.DataFrame(dict(compat=net["all_compat"], group="all dyads", w=1.0 / max(len(net["all_compat"]), 1))),
                     pd.DataFrame(dict(compat=ed["compat"], group="graph edges", w=1.0 / max(len(ed), 1)))]
            hd = pd.concat([p for p in parts if len(p)], ignore_index=True)
            h_chart = alt.Chart(hd).mark_bar(opacity=0.6).encode(
                x=alt.X("compat:Q", bin=alt.Bin(maxbins=14), title="compatibility A", axis=alt.Axis(labelColor=C_INK, titleColor=C_INK)),
                y=alt.Y("sum(w):Q", stack=None, title="share of group", axis=alt.Axis(format="%", labelColor=C_INK, titleColor=C_INK)),
                color=alt.Color("group:N", scale=alt.Scale(domain=["all dyads", "graph edges"], range=["#9a9a9a", C_COOP]),
                                legend=alt.Legend(title=None, orient="top", labelColor=C_INK))).properties(
                width=BASE_W // 2 - 10, height=150,
                title=_title("Compatibility of dyads", "grey all pairs, blue pairs linked in the graph"))
            s_chart = alt.Chart(ed).mark_point(filled=True, size=60, stroke="white").encode(
                x=alt.X("compat:Q", title="compatibility A", scale=alt.Scale(zero=False), axis=alt.Axis(labelColor=C_INK, titleColor=C_INK)),
                y=alt.Y("convs:Q", title="conversations", axis=alt.Axis(tickMinStep=1, labelColor=C_INK, titleColor=C_INK)),
                color=alt.Color("reply_share:Q", scale=alt.Scale(scheme=SEQ_SCHEME, domain=[0, 1]),
                                legend=alt.Legend(title="answered share", format="%", orient="right", labelColor=C_INK, titleColor=C_INK)),
                tooltip=[alt.Tooltip("a:Q", title="agent"), alt.Tooltip("b:Q", title="agent"),
                         alt.Tooltip("compat:Q", format=".2f"), alt.Tooltip("convs:Q", title="conversations"),
                         alt.Tooltip("unanswered:Q")]).properties(
                width=BASE_W // 2 - 20, height=150,
                title=_title("Compatibility vs conversations", "one dot per edge; colour = share answered"))
            charts.append(alt.hconcat(h_chart, s_chart, spacing=30).resolve_scale(color="independent"))
        if len(net["contacts"]):
            nbp = net["nb"]
            hist = alt.Chart(nbp).mark_bar(color="#c9c9c9", stroke="#888888").encode(
                x=alt.X("k:O", title="conversations drawn per agent-round (k)", axis=alt.Axis(labelAngle=0, labelColor=C_INK, titleColor=C_INK)),
                y=alt.Y("count:Q", title="agent-rounds", axis=alt.Axis(labelColor=C_INK, titleColor=C_INK)),
                tooltip=[alt.Tooltip("k:O"), alt.Tooltip("count:Q", title="observed"), alt.Tooltip("expected:Q", format=".1f")])
            pts = alt.Chart(nbp).mark_point(color=C_DEFECT, filled=True, size=50).encode(
                x="k:O", y="expected:Q", tooltip=[alt.Tooltip("k:O"), alt.Tooltip("expected:Q", title="expected", format=".1f")])
            c_hist = alt.layer(hist, pts).properties(
                width=BASE_W // 2 - 10, height=150,
                title=_title("Contacts drawn", f"bars observed; orange = NB(\u03bc={net['mu']:g}, r={net['r']:g}) expected"))
            sc = alt.Chart(nodes).mark_point(filled=True, opacity=0.85, stroke="white").encode(
                x=alt.X("degree:Q", title="degree", axis=alt.Axis(tickMinStep=1, labelColor=C_INK, titleColor=C_INK)),
                y=alt.Y("coop:Q", title=None, scale=alt.Scale(domain=[-0.05, 1.05]), axis=alt.Axis(format="%", labelColor=C_INK)),
                size=alt.Size("lambda:Q", scale=alt.Scale(range=[40, 400]), legend=None),
                color=alt.value(C_COOP),
                tooltip=[alt.Tooltip("name:N", title="agent"), alt.Tooltip("degree:Q"), alt.Tooltip("lambda:Q", title="\u03bb", format=".2f"),
                         alt.Tooltip("coop:Q", title="cooperation", format=".0%")]).add_params(agent)
            c_sc = sc.properties(width=BASE_W // 2 - 20, height=150,
                                 title=_title("Degree vs cooperation", "size = \u03bb; click a dot to select the agent"))
            charts.append(alt.hconcat(c_hist, c_sc, spacing=30))

    if memory is not None and len(memory["items"]):
        tiers = ["verbatim", "excerpt", "gist", "aggregate"]
        mchart = alt.Chart(memory["items"]).transform_filter(STORE_AGENT).mark_point(filled=True, opacity=0.85, stroke="white").encode(
            x=alt.X("conv_round:Q", title="round of the remembered conversation", scale=_x_scale(rlist), axis=_x_axis(rlist, "remembered round")),
            y=alt.Y("decision_round:Q", title="decision round", scale=alt.Scale(domain=[min(rlist) - 0.5, max(rlist) + 0.5], nice=False, zero=False, reverse=True),
                    axis=alt.Axis(values=rlist, format="d", labelColor=C_INK, titleColor=C_INK)),
            color=alt.Color("tier:N", scale=alt.Scale(domain=tiers, range=[C_COOP, "#56B4E9", "#E69F00", C_GREY]),
                            legend=alt.Legend(title="as remembered", orient="right", labelColor=C_INK, titleColor=C_INK)),
            size=alt.Size("weight:Q", scale=alt.Scale(domain=[0, 1], range=[20, 260]), legend=alt.Legend(title="weight", orient="right")),
            tooltip=[alt.Tooltip("other:N", title="with"), alt.Tooltip("conv_round:Q", title="conversation round"),
                     alt.Tooltip("decision_round:Q", title="decision round"), alt.Tooltip("tier:N"),
                     alt.Tooltip("weight:Q", format=".2f")]).properties(
            width=wide, height=max(150, 14 * len(rlist)),
            title=_title("Memory in decision prompts", "click a heatmap cell to select an agent; one dot per remembered conversation"))
        charts.append(mchart)

    if betrayal is not None:
        charts.append(_betrayal_charts(betrayal, rlist, agent))

    final = alt.vconcat(*charts, spacing=28).resolve_scale(color="independent", size="independent", shape="independent").add_params(q)
    final = final.configure_view(stroke=None).configure(background="#ffffff", font="system-ui, sans-serif").configure_axis(
        labelFontSize=10, titleFontSize=11)
    return final


def _betrayal_charts(bt, rlist, agent):
    """Section "Revealed choices and consistency" (NOTES.md #57): words and deeds per round,
    per-agent consistency, and who the conversations went to."""
    ink = dict(labelColor=C_INK, titleColor=C_INK)
    half = BASE_W // 2 - 10
    kinds = ["kept", "broken", "ambiguous"]
    stat = alt.Chart(bt["statements"]).mark_bar(stroke="white", strokeWidth=2).encode(
        x=alt.X("round_number:O", title="round", axis=alt.Axis(labelAngle=0, **ink)),
        y=alt.Y("n:Q", title="statements", axis=alt.Axis(tickMinStep=1, **ink)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=kinds, range=[C_COOP, C_DEFECT, C_GREY]),
                        legend=alt.Legend(title=None, orient="top", labelColor=C_INK)),
        order=alt.Order("kind:N", sort="descending"),
        tooltip=[alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("kind:N"), alt.Tooltip("n:Q")]).properties(
        width=half, height=150, title=_title("Announced choice against actual choice",
                                             "statements per round; broken = chose something else"))
    gk = ["break", "repeat", "first", "drop"]
    game = alt.Chart(bt["game"]).mark_bar(stroke="white", strokeWidth=2).encode(
        x=alt.X("round_number:O", title="round", axis=alt.Axis(labelAngle=0, **ink)),
        y=alt.Y("n:Q", title="exploited (victims)", axis=alt.Axis(tickMinStep=1, **ink)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=gk, range=[C_DEFECT, "#E69F00", "#56B4E9", C_BOTH]),
                        legend=alt.Legend(title=None, orient="top", labelColor=C_INK)),
        tooltip=[alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("kind:N"), alt.Tooltip("n:Q")]).properties(
        width=half, height=150, title=_title("Partner or group exploited a cooperator",
                                             "break = after mutual cooperation; repeat = again; first = new"))
    out = [alt.hconcat(stat, game, spacing=30).resolve_scale(color="independent")]
    ag = bt["agents"].dropna(subset=["coop"])
    if len(ag):
        terc = ["low degree", "middle degree", "high degree", "n/a"]
        col = alt.Color("degree_tercile:N", scale=alt.Scale(domain=terc, range=["#9ecae1", "#4292c6", "#08519c", C_GREY]),
                        legend=alt.Legend(title=None, orient="top", labelColor=C_INK))
        base = alt.Chart(ag)

        def lines(field, title, sub):
            return base.mark_line(strokeWidth=1.5, opacity=0.7).encode(
                x=alt.X("round_number:Q", scale=_x_scale(rlist), axis=_x_axis(rlist)),
                y=alt.Y(f"{field}:Q", scale=alt.Scale(domain=[0, 1]), title=None, axis=alt.Axis(format="%", labelColor=C_INK)),
                color=col, detail="agent_id:N",
                tooltip=[alt.Tooltip("agent_id:Q", title="agent"), alt.Tooltip("round_number:Q", title="round"),
                         alt.Tooltip(f"{field}:Q", format=".0%"), alt.Tooltip("stated:Q", title="statements so far")]
            ).properties(width=half, height=150, title=_title(title, sub))
        out.append(alt.hconcat(
            lines("consistency", "Consistency of each agent", "Beta score of kept words (1 + kept) / (2 + stated); 50% = nothing said"),
            lines("coop", "Cooperative share so far", "per agent, coloured by degree tercile"), spacing=30
        ).resolve_scale(color="independent"))
    sh = bt["shares"]
    if len(sh):
        long = sh.melt(id_vars=["round_number", "initiations"], value_vars=["observed", "expected_base", "expected_with_reputation"],
                       var_name="series", value_name="share")
        if not bt["rho"]:
            long = long[long["series"] != "expected_with_reputation"]
        doms = ["observed", "expected_base", "expected_with_reputation"]
        sh_chart = alt.Chart(long).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=40)).encode(
            x=alt.X("round_number:Q", scale=_x_scale(rlist), axis=_x_axis(rlist)),
            y=alt.Y("share:Q", scale=alt.Scale(domain=[0, 1]), title=None, axis=alt.Axis(format="%", labelColor=C_INK)),
            color=alt.Color("series:N", scale=alt.Scale(domain=doms, range=[C_DEFECT, "#555555", C_COOP]),
                            legend=alt.Legend(title=None, orient="top", labelColor=C_INK)),
            strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=doms, range=[[1, 0], [5, 4], [2, 2]]), legend=None),
            tooltip=[alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("series:N"),
                     alt.Tooltip("share:Q", format=".0%"), alt.Tooltip("initiations:Q")]).properties(
            width=BASE_W, height=150, title=_title(
                "Conversations started with a neighbour something bad was revealed about",
                "observed share against the share expected from the base contact weights"
                + (" and from the reputation weights" if bt["rho"] else "")))
        out.append(sh_chart)
    return alt.vconcat(*out, spacing=24, title=_title("Revealed choices and consistency",
                                                      f"net_reveal_choices: {bt['reveal']}; with no reveal nobody was told, the same facts are a placebo"))


# ----------------------------------------------------------------------------- HTML
def spec_json(spec):
    return (json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


CSS = """
body{max-width:980px;margin:auto;padding:12px 16px;font:14px system-ui,sans-serif;color:#222;background:#fff;color-scheme:light}
h1{font-size:20px;margin:6px 0}
.meta{color:#444;font-size:13px;margin:2px 0 8px}
.cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.card{border:1px solid #ddd;border-radius:6px;padding:6px 10px;min-width:90px}
.card b{display:block;font-size:18px}
.card span{font-size:12px;color:#555}
.legend span{display:inline-block;margin-right:12px;font-size:12px}
.sw{display:inline-block;width:10px;height:10px;margin-right:4px;vertical-align:baseline}
#vis{overflow-x:auto;padding:4px 0}
#status{font-size:11px;color:#777}
#vg-tooltip-element{max-width:340px}
#vg-tooltip-element td.value{white-space:normal;max-width:300px;word-break:break-word}
details{margin:4px 0;border-top:1px solid #eee}
summary{cursor:pointer;padding:4px 0;font-weight:600}
.conv{margin:6px 0 6px 12px;padding-left:8px;border-left:3px solid #ccc;font-size:13px}
.conv.network{border-left-color:#8a8a8a}.conv.pair{border-left-color:#0072B2}
.conv .h{color:#555;font-size:12px}
.note{color:#555;font-size:12px}
"""

JS = """
const spec = JSON.parse(document.getElementById('spec').textContent);
const statusEl = document.getElementById('status');
const sel = {agent: new Set(), rnd: new Set()};
function refresh() {
  document.querySelectorAll('.conv').forEach(el => {
    const ag = (el.dataset.agents || '').split(' ');
    const okA = sel.agent.size === 0 || ag.some(a => sel.agent.has(a));
    const okR = sel.rnd.size === 0 || sel.rnd.has(el.dataset.round);
    el.hidden = !(okA && okR);
  });
  document.querySelectorAll('details.round').forEach(d => {
    d.hidden = !d.querySelector('.conv:not([hidden])');
  });
}
function listen(view, store, key) {
  try {
    view.addDataListener(store, (name, rows) => {
      sel[key] = new Set((rows || []).map(r => String(r.values[0])));
      refresh();
    });
  } catch (e) { /* store missing: no such selection in this run */ }
}
vegaEmbed('#vis', spec, {renderer: 'svg', actions: false}).then(res => {
  listen(res.view, 'agent_store', 'agent');
  listen(res.view, 'rnd_store', 'rnd');
  window.__view = res.view;
  statusEl.textContent = 'ok';
}).catch(err => { statusEl.textContent = 'error: ' + err; });
"""


def transcript_html(msgs, names):
    if not len(msgs):
        return ""
    out = ['<h2 style="font-size:16px">Transcripts</h2><p class="note">Filtered by the agent / round selection above.</p>']
    for rn, g in msgs.groupby("round_number"):
        out.append(f'<details class="round" data-round="{rn}"><summary>Round {rn} ({g["conv_id"].nunique()} conversations, {len(g)} messages)</summary>')
        for cid, c in g.groupby("conv_id", sort=False):
            ag = sorted({int(a) for a in list(c["speaker_id"]) + list(c["listener_id"])})
            kind = c["kind"].iloc[0]
            title = " \u2194 ".join(html.escape(names.get(a, str(a))) for a in ag)
            out.append(f'<div class="conv {kind}" data-agents="{" ".join(map(str, ag))}" data-round="{rn}">'
                       f'<div class="h">{html.escape(kind)} chat, {html.escape(str(cid))}: {title}</div>')
            for _, m in c.sort_values(["turn"]).iterrows():
                out.append(f'<div><b>{html.escape(m["speaker"])}</b>: {html.escape(m["text"])}</div>')
            out.append("</div>")
        out.append("</details>")
    return "\n".join(out)


def build_html(title, run, dec, msgs, rounds, net, spec, names, agents):
    cfg = run["cfg"]
    s = cfg.get("settings", {}) or {}
    game = run["game"]
    final = rounds.sort_values("round_number").iloc[-1]
    overall = dec["coop"].mean()
    model = ""
    for src in dec["source"]:
        if src:
            model = src.replace("llm:", "")
            break
    labels = cfg.get("labels") or {}
    if "cooperate" in labels:
        lab = f"session-wide: cooperate={labels['cooperate']} defect={labels.get('defect', '')}"
    elif labels:
        lab = f"per {s.get('label_unit', 'pair')}"
    else:
        lab = "n/a"
    bits = [f"chat_turns={s.get('chat_turns', 0)}"]
    if net is not None:
        r = net["raw"]
        bits.append(f"net_topology={r.get('topology')} &mu;={net['mu']:g} r={net['r']:g}")
    bits.append(f"labels: {html.escape(lab)}")
    sim = os.path.basename(os.path.normpath(run["sim_dir"])) if run["sim_dir"] else "n/a"
    what = "cooperation rate" if game == "pd" else "mean share of endowment"
    cards = [
        (f"{overall:.0%}", f"overall {what}"), (f"{final['rate']:.0%}", f"final round ({int(final['round_number'])})"),
        (str(len(msgs)), "messages"), (str(int(dec['missing'].sum())), "missing decisions"),
        (str(len(agents)), "agents"), (str(dec['round_number'].nunique()), "rounds")]
    cards_html = "".join(f'<div class="card"><b>{html.escape(a)}</b><span>{html.escape(b)}</span></div>' for a, b in cards)
    if game == "pd":
        legend = (f'<span><i class="sw" style="background:{C_COOP}"></i>cooperate</span>'
                  f'<span><i class="sw" style="background:{C_DEFECT}"></i>defect</span>')
    else:
        legend = f'<span><i class="sw" style="background:{C_COOP}"></i>share contributed (light = low)</span>'
    legend += f'<span><i class="sw" style="background:{C_GREY}"></i>missing</span>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/vega@{_v6.VEGA_VERSION}"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@{_v6.VEGALITE_VERSION}"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@{_v6.VEGAEMBED_VERSION}"></script>
</head><body>
<h1>{html.escape(title)}</h1>
<div class="meta">game: {game.upper()} &middot; session {html.escape(run['session'])} &middot; sim {html.escape(sim)} &middot;
persona set: {html.escape(persona_set(run['sim_dir']))} &middot; model: {html.escape(model or 'n/a')}</div>
<div class="meta">{' &middot; '.join(bits)}</div>
<div class="cards">{cards_html}</div>
<div class="legend">{legend}</div>
<div id="vis" style="overflow-x:auto"></div>
<div id="status">loading</div>
{transcript_html(msgs, names)}
<script type="application/json" id="spec">{spec_json(spec)}</script>
<script>{JS}</script>
</body></html>
"""


# ----------------------------------------------------------------------------- main
def build(args):
    run = load_run(args)
    df = run["df"]
    names = load_names(run["sim_dir"], df["agent_id"].unique())
    dec, agents, label_of, row_of, endow = build_decisions(run, names)
    msgs = build_messages(run, names, args.max_chars)
    talked = set()
    for _, m in msgs.iterrows():
        talked.add((int(m["speaker_id"]), int(m["round_number"])))
        talked.add((int(m["listener_id"]), int(m["round_number"])))
    dec["talked"] = [(a, r) in talked for a, r in zip(dec["agent_id"], dec["round_number"])]
    dec["xc"] = dec["round_number"].astype(float)
    net = load_network(run, names, dec, msgs)
    memory = load_memory(run)
    if memory is not None:
        dec["memory_chars"] = [memory["chars"].get((int(a), int(r))) for a, r in zip(dec["agent_id"], dec["round_number"])]
    rounds, base, grp = build_rounds(run, dec, msgs, args.baseline_csv)
    alt.data_transformers.disable_max_rows()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Automatically deduplicated")
        betrayal = load_betrayal(run, net)
        chart = make_charts(run, dec, msgs, rounds, base, grp, net, agents, label_of, endow, memory, betrayal)
    spec = chart.to_dict(validate=True)
    title = args.title or f"{run['game'].upper()} session {run['session']}"
    page = build_html(title, run, dec, msgs, rounds, net, spec, names, agents)
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.otree_csv)), f"dashboard_{run['session']}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return dict(out=out, chart=chart, spec=spec, run=run, dec=dec, msgs=msgs, net=net, rounds=rounds, html=page)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--otree-csv", required=True, help="oTree custom export (pd_debate_custom.csv / pgg_custom.csv)")
    ap.add_argument("--sim-dir", help="simulation dir (names, bridge_log, chat / network files)")
    ap.add_argument("--session", help="session code (required when the CSV has several)")
    ap.add_argument("--baseline-csv", help="control export of the same game; overlays its cooperation line (dashed)")
    ap.add_argument("--out", help="output HTML (default: <csv dir>/dashboard_<session>.html)")
    ap.add_argument("--max-chars", type=int, default=600, help="truncate each message to this many characters")
    ap.add_argument("--title", help="page title")
    args = ap.parse_args(argv)
    res = build(args)
    size = os.path.getsize(res["out"])
    print(f"wrote {res['out']} ({size / 1024:.0f} KB; {len(res['msgs'])} messages)")


if __name__ == "__main__":
    main()
