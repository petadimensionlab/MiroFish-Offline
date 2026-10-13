#!/usr/bin/env python3
"""Static comparison page for several runs (conditions) of the same game (PD or pgg).

Usage:
    dashboard_compare.py --run LABEL=CSV[:SIMDIR][@SESSION] --run ... [--out PATH] [--title T]

Charts: cooperation rate per round (legend click highlights a condition), final / overall
rate per condition, pooled per-agent scatter (degree / lambda vs cooperation, network runs
only), per-condition network stats and message counts. The page embeds all data and loads
vega / vega-lite / vega-embed from jsdelivr. Prompts and raw LLM responses are never read.
"""

import argparse
import html
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
from altair.vegalite import v6 as _v6  # noqa: E402

import dashboard as D  # noqa: E402

PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7a7a7a", "#8c564b"]
W = 820


def parse_run(spec):
    if "=" not in spec:
        raise SystemExit(f"error: --run expects LABEL=CSV[:SIMDIR][@SESSION], got {spec!r}")
    label, rest = spec.split("=", 1)
    session = None
    if "@" in rest:
        rest, session = rest.rsplit("@", 1)
    csv, sim = (rest.split(":", 1) + [None])[:2] if ":" in rest else (rest, None)
    return label.strip(), csv, sim or None, session


def load_condition(label, csv, sim, session):
    args = argparse.Namespace(otree_csv=csv, sim_dir=sim, session=session, baseline_csv=None, out=None, max_chars=600, title=None)
    run = D.load_run(args)
    df = run["df"]
    names = D.load_names(sim, df["agent_id"].unique())
    dec, agents, label_of, row_of, endow = D.build_decisions(run, names)
    msgs = D.build_messages(run, names, 600)
    net = D.load_network(run, names, dec, msgs)
    rounds, _, _ = D.build_rounds(run, dec, msgs, None)
    bet = D.load_betrayal(run, net)
    s = run["cfg"].get("settings", {}) or {}
    model = next((x.replace("llm:", "") for x in dec["source"] if x), "")
    info = dict(label=label, game=run["game"], session=run["session"], persona=D.persona_set(sim), model=model or "n/a",
                n_agents=len(agents), rounds=int(dec["round_number"].nunique()), messages=len(msgs),
                topology=(net["raw"].get("topology") if net else None) or s.get("net_topology") or "none",
                chat_turns=s.get("chat_turns", 0))
    info['betrayal'] = betrayal_numbers(bet, dec)
    return run, dec, msgs, net, rounds, info


def betrayal_numbers(bet, dec):
    """Per condition: kept rate of announced choices, exploit-avoidance ratio (share of conversations
    started with a neighbour something bad was revealed about, observed / expected from the base
    contact weights; below 1 = avoided) and cooperation in rounds 2+. None without betrayal data."""
    if bet is None:
        return None
    st = bet["statements"]
    kept, broken = int(st.loc[st["kind"] == "kept", "n"].sum()), int(st.loc[st["kind"] == "broken", "n"].sum())
    sh = bet["shares"]
    ratio = None
    if len(sh) and (sh["expected_base"] * sh["initiations"]).sum() > 0:
        ratio = float((sh["observed"] * sh["initiations"]).sum() / (sh["expected_base"] * sh["initiations"]).sum())
    late = dec[dec["round_number"] >= 2]
    return dict(kept_rate=kept / (kept + broken) if kept + broken else None, avoidance=ratio,
                coop_2plus=float(late["coop"].mean()) if len(late) else None, reveal=bet["reveal"],
                statements=kept + broken)


def net_stats(net):
    if net is None:
        return dict(mean_degree=None, max_degree=None, clustering=None, n_edges=None)
    import networkx as nx
    G = nx.Graph()
    G.add_nodes_from(int(n["agent_id"]) for n in net["raw"]["nodes"])
    G.add_edges_from(tuple(e) for e in net["raw"].get("edges", []))
    n = G.number_of_nodes()
    degs = [d for _, d in G.degree()]
    return dict(mean_degree=round(2 * G.number_of_edges() / n, 2) if n else None, max_degree=max(degs) if degs else None,
                clustering=round(nx.average_clustering(G), 3), n_edges=G.number_of_edges())


def make_compare(conds):
    game = conds[0]["run"]["game"]
    order = [c["info"]["label"] for c in conds]
    colors = alt.Scale(domain=order, range=[PALETTE[i % len(PALETTE)] for i in range(len(order))])
    what = "cooperation rate" if game == "pd" else "mean contribution share"
    ink = D.C_INK
    cond = alt.Color("condition:N", scale=colors, legend=alt.Legend(title="condition (click to highlight)", orient="top",
                                                                    direction="horizontal", labelColor=ink, titleColor=ink))
    hl = alt.selection_point(fields=["condition"], bind="legend", name="cond")

    rr = pd.concat([c["rounds"].assign(condition=c["info"]["label"]) for c in conds], ignore_index=True)
    rr = rr[["condition", "round_number", "rate", "n", "missing", "messages"]]
    rl = sorted(rr["round_number"].unique().tolist())
    tip_r = [alt.Tooltip("condition:N"), alt.Tooltip("round_number:Q", title="round"), alt.Tooltip("rate:Q", title=what, format=".0%"),
             alt.Tooltip("messages:Q"), alt.Tooltip("missing:Q")]
    x = alt.X("round_number:Q", scale=alt.Scale(domain=[min(rl) - 0.5, max(rl) + 0.5], nice=False, zero=False),
              axis=alt.Axis(values=rl, format="d", title="round", grid=False, labelColor=ink, titleColor=ink))
    y = alt.Y("rate:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", title=None, labelColor=ink))
    op = alt.condition(hl, alt.value(1), alt.value(0.2))
    line = alt.Chart(rr).mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(filled=True, size=50)).encode(
        x=x, y=y, color=cond, opacity=op, tooltip=tip_r).add_params(hl)
    c_rate = line.properties(width=W, height=220, title=D._title(f"{what.capitalize()} by round",
                                                               "one line per condition; click a legend entry to highlight it"))

    # per-condition summary
    summ = []
    for c in conds:
        d, i = c["dec"], c["info"]
        last = c["rounds"].sort_values("round_number").iloc[-1]
        summ.append(dict(condition=i["label"], overall=float(d["coop"].mean()), final=float(last["rate"]),
                         final_round=int(last["round_number"])))
    sm = pd.DataFrame(summ).melt(id_vars=["condition", "final_round"], value_vars=["overall", "final"],
                                 var_name="measure", value_name="rate")
    bars = alt.Chart(sm).mark_bar().encode(
        y=alt.Y("condition:N", sort=order, title=None, axis=alt.Axis(labelColor=ink, labelLimit=200)),
        yOffset=alt.YOffset("measure:N", sort=["overall", "final"]),
        x=alt.X("rate:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", title=what, labelColor=ink, titleColor=ink)),
        color=alt.Color("condition:N", scale=colors, legend=None),
        opacity=alt.Opacity("measure:N", scale=alt.Scale(domain=["overall", "final"], range=[0.55, 1]),
                            legend=alt.Legend(title="light = overall, dark = final round", orient="top", direction="horizontal",
                                              labelColor=ink, titleColor=ink)),
        tooltip=[alt.Tooltip("condition:N"), alt.Tooltip("measure:N"), alt.Tooltip("rate:Q", format=".1%"),
                 alt.Tooltip("final_round:Q", title="final round")])
    txt = alt.Chart(sm).mark_text(align="left", dx=4, fontSize=10, color=ink).encode(
        y=alt.Y("condition:N", sort=order), yOffset=alt.YOffset("measure:N", sort=["overall", "final"]),
        x="rate:Q", text=alt.Text("rate:Q", format=".0%"))
    c_sum = alt.layer(bars, txt).properties(width=W - 200, height=alt.Step(18),
                                            title=D._title("Overall and final-round rate", "per condition"))
    charts = [c_rate, c_sum]

    # pooled agent scatter (network runs)
    pts = []
    for c in conds:
        if c["net"] is not None:
            nd = c["net"]["nodes"]
            pts.append(nd[["name", "degree", "lambda", "convs", "coop"]].assign(condition=c["info"]["label"]))
    if pts:
        pa = pd.concat(pts, ignore_index=True).dropna(subset=["coop"])
        tip = [alt.Tooltip("name:N", title="agent"), alt.Tooltip("condition:N"), alt.Tooltip("degree:Q"),
               alt.Tooltip("lambda:Q", title="λ", format=".2f"), alt.Tooltip("convs:Q", title="conversations"),
               alt.Tooltip("coop:Q", title="cooperation", format=".0%")]

        def sc(field, title, w):
            return alt.Chart(pa).mark_point(filled=True, size=70, stroke="white").encode(
                x=alt.X(field, title=title, axis=alt.Axis(tickMinStep=1 if field.startswith("degree") else 0, tickCount=6, format="d" if field.startswith("degree") else ".1f", labelColor=ink, titleColor=ink)),
                y=alt.Y("coop:Q", title=None, scale=alt.Scale(domain=[-0.05, 1.05]), axis=alt.Axis(format="%", labelColor=ink)),
                color=alt.Color("condition:N", scale=colors, legend=None), opacity=op, tooltip=tip).add_params(hl).properties(
                width=w, height=200, title=D._title(f"{title} vs agent {what}", "one dot per agent, pooled over network conditions"))
        charts.append(alt.hconcat(sc("degree:Q", "degree", W // 2 - 40), sc("lambda:Q", "contact rate λ", W // 2 - 40), spacing=30))

    # betrayal and reputation (#57): kept rate, exploit-avoidance ratio, cooperation from round 2
    brows = []
    for c in conds:
        b = c["info"].get("betrayal")
        if b:
            for measure, v in (("kept rate", b["kept_rate"]), ("avoidance ratio", b["avoidance"]),
                               ("cooperation, rounds 2+", b["coop_2plus"])):
                if v is not None:
                    brows.append(dict(condition=c["info"]["label"], measure=measure, value=float(v)))
    if brows:
        bd = pd.DataFrame(brows)
        bars = alt.Chart(bd).mark_bar(stroke="white", strokeWidth=2).encode(
            y=alt.Y("condition:N", sort=order, title=None, axis=alt.Axis(labelColor=ink, labelLimit=200)),
            x=alt.X("value:Q", title=None, axis=alt.Axis(labelColor=ink), scale=alt.Scale(domainMin=0)),
            color=alt.Color("condition:N", scale=colors, legend=None),
            tooltip=[alt.Tooltip("condition:N"), alt.Tooltip("measure:N"), alt.Tooltip("value:Q", format=".2f")])
        charts.append(bars.properties(width=W // 3 - 60, height=alt.Step(16)).facet(
            column=alt.Column("measure:N", title=None, sort=["kept rate", "avoidance ratio", "cooperation, rounds 2+"],
                              header=alt.Header(labelColor=ink, labelFontSize=11))).properties(
            title=D._title("Words, deeds and who gets contacted",
                           "kept rate of announced choices; avoidance ratio < 1 = neighbours with something bad revealed are contacted less than the base weights predict")))

    # stats "table"
    rows = []
    for c in conds:
        i, st = c["info"], c["stats"]
        rows.append(dict(condition=i["label"], topology=i["topology"], **{"mean degree": st["mean_degree"], "max degree": st["max_degree"],
                                                                        "clustering": st["clustering"], "messages": i["messages"]}))
    st_df = pd.DataFrame(rows)
    cols = ["topology", "mean degree", "max degree", "clustering", "messages"]
    long = st_df.melt(id_vars="condition", value_vars=cols, var_name="stat", value_name="v")
    long["text"] = [("–" if pd.isna(v) else (f"{v:.2f}" if isinstance(v, float) and s in ("mean degree", "clustering") else str(v)))
                    for v, s in zip(long["v"], long["stat"])]
    tb = alt.Chart(long).mark_text(fontSize=12, color=ink).encode(
        x=alt.X("stat:N", sort=cols, title=None, axis=alt.Axis(orient="top", labelAngle=0, labelColor=ink, ticks=False, domain=False, labelFontSize=11)),
        y=alt.Y("condition:N", sort=order, title=None, axis=alt.Axis(labelColor=ink, ticks=False, domain=False, labelLimit=200)),
        text="text:N", tooltip=[alt.Tooltip("condition:N"), alt.Tooltip("stat:N"), alt.Tooltip("text:N", title="value")])
    charts.append(tb.properties(width=W - 200, height=alt.Step(22), title=D._title("Network and messages", "per condition; – = no network")))

    final = alt.vconcat(*charts, spacing=28).resolve_scale(color="independent", opacity="independent")
    return final.configure_view(stroke=None).configure(background="#ffffff", font="system-ui, sans-serif").configure_axis(
        labelFontSize=10, titleFontSize=11)


def page(title, conds, spec):
    game = conds[0]["run"]["game"]
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in (
            i["label"], i["session"], i["persona"], i["model"], i["n_agents"], i["rounds"], i["topology"], i["chat_turns"], i["messages"])) + "</tr>"
        for i in (c["info"] for c in conds))
    head = "".join(f"<th>{h}</th>" for h in ("condition", "session", "persona set", "model", "agents", "rounds", "topology", "chat turns", "messages"))
    css = D.CSS + "\ntable.h{border-collapse:collapse;font-size:12px;margin:8px 0}table.h td,table.h th{border:1px solid #ddd;padding:3px 8px;text-align:left}"
    js = """
const spec = JSON.parse(document.getElementById('spec').textContent);
const statusEl = document.getElementById('status');
vegaEmbed('#vis', spec, {renderer: 'svg', actions: false}).then(res => {
  window.__view = res.view; statusEl.textContent = 'ok';
}).catch(err => { statusEl.textContent = 'error: ' + err; });
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/vega@{_v6.VEGA_VERSION}"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@{_v6.VEGALITE_VERSION}"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@{_v6.VEGAEMBED_VERSION}"></script>
</head><body>
<h1>{html.escape(title)}</h1>
<div class="meta">game: {game.upper()} &middot; {len(conds)} conditions</div>
<div style="overflow-x:auto"><table class="h"><tr>{head}</tr>{rows}</table></div>
<div id="vis" style="overflow-x:auto"></div>
<div id="status">loading</div>
<script type="application/json" id="spec">{D.spec_json(spec)}</script>
<script>{js}</script>
</body></html>
"""


def build(runs, out, title=None):
    conds = []
    for label, csv, sim, session in runs:
        run, dec, msgs, net, rounds, info = load_condition(label, csv, sim, session)
        conds.append(dict(run=run, dec=dec, msgs=msgs, net=net, rounds=rounds, info=info, stats=net_stats(net)))
    if len({c["run"]["game"] for c in conds}) != 1:
        raise SystemExit("error: all runs must be the same game")
    if len({c["info"]["label"] for c in conds}) != len(conds):
        raise SystemExit("error: duplicate condition labels")
    alt.data_transformers.disable_max_rows()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Automatically deduplicated")
        chart = make_compare(conds)
    spec = chart.to_dict(validate=True)
    html_text = page(title or f"{conds[0]['run']['game'].upper()} comparison", conds, spec)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_text)
    return dict(out=out, spec=spec, html=html_text, conds=conds)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", required=True, metavar="LABEL=CSV[:SIMDIR][@SESSION]",
                    help="one condition; repeat for each (same game)")
    ap.add_argument("--out", default="compare.html", help="output HTML")
    ap.add_argument("--title", help="page title")
    a = ap.parse_args(argv)
    res = build([parse_run(r) for r in a.run], a.out, a.title)
    print(f"wrote {res['out']} ({os.path.getsize(res['out']) / 1024:.0f} KB; {len(res['conds'])} conditions)")


if __name__ == "__main__":
    main()
