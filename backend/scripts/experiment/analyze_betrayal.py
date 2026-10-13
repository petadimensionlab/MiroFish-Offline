"""
Betrayal and revealed-choice analysis of one network session (NOTES.md #57).

Ground truth only, no LLM: what agents SAID they would choose in a
conversation against what they chose (word events), and what they DID to their
game partner / group (game and exploit events). The rules live in
app/services/betrayal.py, which this script loads by file path (the services
package imports Flask). Everything is recomputed from the logs of a session,
<sim_dir>/game/<session_code>/: bridge_log.jsonl (round_complete outcomes,
configure labels), network_chat.jsonl (final messages), network_contacts.jsonl
(who started, who answered) and network.json / dyads.json (partner, groups).
So every existing run is a free arm with the reveal switched off. When
dyads.jsonl carries events (net_dyad_hooks 'betrayal') they are asserted equal
to the recomputed ones: online == offline.

Sections of summarize(): statements (coverage, status counts, kept rate by
round), game events, aftermath (P(coop t+1) after being betrayed against
cooperators not betrayed), spillover (P(coop t+1) after being told that a
conversation partner exploited / broke its word against being told it did
not), selection (P(i starts a conversation with j at t+1) by what i could have
been told about j at t), speakers (kept rate and cooperation of agents that
announced a choice), honesty (past-tense self-reports against the record),
gossip (messages naming a third participant, and those that relay a fact the
speaker was told earlier). In a run without reveal (net_reveal_choices 'none')
the 'told' facts are LATENT: nobody was told, so spillover and selection are
placebos and must be null; with reveal 'talked' / 'pair' they are the effect.

Usage:
    python analyze_betrayal.py GAME_DIR [GAME_DIR ...] [--json]
    python analyze_betrayal.py GAME_DIR --audit 60 --seed 0     # extractions to read by hand
"""

import argparse
import importlib.util
import json
import math
import os
import random
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BETRAYAL_PATH = os.path.join(HERE, '..', '..', 'app', 'services', 'betrayal.py')


def load_betrayal():
    spec = importlib.util.spec_from_file_location('betrayal_rules', os.path.abspath(BETRAYAL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('betrayal_rules', mod)
    spec.loader.exec_module(mod)
    return mod


B = load_betrayal()


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def rate(xs):
    xs = list(xs)
    return dict(n=len(xs), p=mean(xs))


# -- inputs from the logs ---------------------------------------------------------------

def read_session(gdir):
    """Everything events_from_logs needs, from the session's log directory."""
    log = load_jsonl(os.path.join(gdir, 'bridge_log.jsonl'))
    cfg = next((e for e in log if e.get('event') == 'configure'), {})
    settings = cfg.get('settings') or {}
    game = settings.get('game', 'pd')
    outcomes = {}
    for e in log:
        if e.get('event') == 'round_complete' and e['round_number'] not in outcomes:
            outcomes[e['round_number']] = {int(o['agent_id']): o for o in e.get('outcomes', []) if 'agent_id' in o}
    labels = cfg.get('labels') or {}
    if 'cooperate' not in labels:
        labels = next((v for v in labels.values() if isinstance(v, dict) and 'cooperate' in v), {})
    shown = {'A': labels['cooperate'], 'B': labels['defect']} if labels else {}
    contacts = load_jsonl(os.path.join(gdir, 'network_contacts.jsonl'))
    chat = load_jsonl(os.path.join(gdir, 'network_chat.jsonl'))
    net = load_json(os.path.join(gdir, 'network.json')) or {}
    dyads_doc = load_json(os.path.join(gdir, 'dyads.json')) or {}
    partner, groups = {}, {}
    for rnd in sorted(outcomes):
        for a, o in outcomes[rnd].items():
            if game == 'pd' and o.get('partner_agent_id') is not None:
                partner.setdefault(a, int(o['partner_agent_id']))
            if game == 'pgg' and o.get('group_agent_ids'):
                groups.setdefault(a, [int(x) for x in o['group_agent_ids']])
    if game == 'pd' and not partner:
        excl = {}
        for d in dyads_doc.get('dyads', []):
            if d.get('excluded'):
                excl.setdefault(d['a'], []).append(d['b'])
                excl.setdefault(d['b'], []).append(d['a'])
        partner = {a: v[0] for a, v in excl.items() if len(v) == 1}
    return dict(log=log, settings=settings, game=game, outcomes=outcomes, shown=shown, contacts=contacts,
                chat=chat, net=net, dyads_doc=dyads_doc, partner=partner, groups=groups,
                endowment=(settings.get('game_params') or {}).get('endowment', 20))


def conversations(sess):
    """{round: [{conv_id, initiator, responder, replied, messages}]} as the bridge held them."""
    final = defaultdict(list)
    for r in sess['chat']:
        if r.get('message') and not r.get('parse_error'):
            final[(r['round_number'], r['conv_id'])].append(dict(agent_id=r['agent_id'], text=r['message']))
    out = {}
    for rec in sess['contacts']:
        t = rec['round_number']
        out[t] = [dict(conv_id=c['conv_id'], initiator=c['initiator'], responder=c['responder'],
                       replied=c.get('replied', True), messages=final.get((t, c['conv_id']), []))
                  for c in rec['conversations']]
    return out


def game_ctx(sess):
    if sess['game'] == 'pd':
        shown = sess['shown']
        return dict(options=[shown['A'], shown['B']], shown=shown)
    return dict(endowment=sess['endowment'])


def events_from_logs(gdir, sess=None):
    """Recompute word / game / exploit events of every round from the logs.

    Returns {game, rounds: [t], events: {t: [event]}, counts: {t: {...}}}.
    The events are what DyadLedger hook 'betrayal' writes online.
    """
    sess = sess or read_session(gdir)
    convs = conversations(sess)
    ctx = game_ctx(sess)
    events, counts = {}, {}
    for t in sorted(sess['outcomes']):
        w, c = B.word_events(t, convs.get(t, []), sess['outcomes'][t], sess['game'], ctx)
        if sess['game'] == 'pd':
            g, skipped = B.game_events_pd(t, sess['outcomes'], sess['partner'])
        else:
            g, skipped = B.game_events_pgg(t, sess['outcomes'], sess['groups'], sess['endowment'])
        events[t] = w + g
        counts[t] = dict(c, skipped_game=skipped)
    return dict(game=sess['game'], rounds=sorted(events), events=events, counts=counts)


def check_online(gdir, offline):
    """Assert dyads.jsonl events == recomputed ones. None when the run kept none."""
    rows = load_jsonl(os.path.join(gdir, 'dyads.jsonl'))
    if not any('events' in r for r in rows):
        return None
    bad = [r['round_number'] for r in rows
           if json.dumps(r.get('events', []), sort_keys=True) !=
           json.dumps(offline['events'].get(r['round_number'], []), sort_keys=True)]
    return dict(rounds=len(rows), equal=not bad, mismatched_rounds=bad)


# -- helpers on outcomes ------------------------------------------------------------------

def coop_value(sess, t, a):
    """Cooperation of a in round t in [0, 1]; None when missing / not a real decision."""
    o = sess['outcomes'].get(t, {}).get(a)
    if not B.usable(o):
        return None
    if sess['game'] == 'pd':
        return 1.0 if o['choice'] == 'A' else 0.0
    return int(float(o['choice'])) / sess['endowment']


talked = B.talked


def facts(sess, ev_by_round, convs):
    """Per (round, listener i, speaker j) with a conversation both took part in: what i could
    have been told about j: {choice, exploit, mismatch, bad}."""
    out = {}
    for t, cs in convs.items():
        exploiters = {e['by'] for e in ev_by_round.get(t, []) if e['type'] == 'exploit'}
        mism = {(e['listener'], e['speaker']) for e in ev_by_round.get(t, [])
                if e['type'] == 'word' and not e['kept']}
        for c in cs:
            if not talked(c):
                continue
            for i, j in ((c['initiator'], c['responder']), (c['responder'], c['initiator'])):
                cv = coop_value(sess, t, j)
                if cv is None:
                    continue
                out[(t, i, j)] = dict(coop=cv, exploit=j in exploiters, mismatch=(i, j) in mism,
                                      bad=(j in exploiters) or ((i, j) in mism))
    return out


# -- sections -----------------------------------------------------------------------------

def statements(sess, off, convs):
    ctx = game_ctx(sess)
    status, by_round = defaultdict(int), defaultdict(lambda: dict(statements=0, kept=0, broken=0, ambiguous=0,
                                                                  hedged=0, none=0, skipped_missing=0))
    for t, cs in convs.items():
        for c in cs:
            if c['replied'] is False:
                continue
            for speaker in (c['initiator'], c['responder']):
                texts = [m['text'] for m in c['messages'] if m['agent_id'] == speaker]
                if not texts:
                    continue
                if not B.usable(sess['outcomes'].get(t, {}).get(speaker)):
                    by_round[t]['skipped_missing'] += 1
                    continue
                _, st = B.stated_intention(texts, sess['game'], ctx, t)
                status[st] += 1
                if st != 'single':
                    by_round[t][st] += 1
    words = [e for t in off['rounds'] for e in off['events'][t] if e['type'] == 'word']
    for e in words:
        by_round[e['round']]['statements'] += 1
        by_round[e['round']]['kept' if e['kept'] else 'broken'] += 1
    total = sum(status.values())
    return dict(
        speaker_conversations=total, coverage=round(status['single'] / total, 4) if total else None,
        status={k: status[k] for k in B.STATUSES}, statements=len(words),
        kept=sum(e['kept'] for e in words), broken=sum(not e['kept'] for e in words),
        kept_rate=mean(1 if e['kept'] else 0 for e in words),
        mean_deficit=(mean(e['deficit'] for e in words) if sess['game'] == 'pgg' else None),
        skipped_missing=sum(r['skipped_missing'] for r in by_round.values()),
        by_round=[dict(round=t, **by_round[t], kept_rate=(round(by_round[t]['kept'] / by_round[t]['statements'], 4)
                                                         if by_round[t]['statements'] else None))
                  for t in sorted(by_round)])


def game_summary(sess, off):
    kinds, rounds = defaultdict(int), defaultdict(lambda: defaultdict(int))
    exploits = 0
    for t in off['rounds']:
        for e in off['events'][t]:
            if e['type'] == 'game':
                kinds[e['kind']] += 1
                rounds[t][e['kind']] += 1
            elif e['type'] == 'exploit':
                exploits += 1
                rounds[t]['exploit'] += 1
    return dict(by_kind=dict(kinds), exploits=exploits,
                by_round=[dict(round=t, **dict(rounds[t])) for t in sorted(rounds)],
                skipped_missing=sum(c['skipped_game'] for c in off['counts'].values()))


def aftermath(sess, off):
    """P(coop t+1 | betrayed at t) against cooperators not betrayed (PD), or the mean
    share against members of the group who were not the victim of a drop (pgg)."""
    cells = defaultdict(list)
    for t in off['rounds']:
        nxt = {a: coop_value(sess, t + 1, a) for a in sess['outcomes'].get(t + 1, {})}
        evs = off['events'][t]
        if sess['game'] == 'pd':
            victims = {e['victim']: e['kind'] for e in evs if e['type'] == 'game'}
            for a in sess['outcomes'].get(t, {}):
                if coop_value(sess, t, a) != 1.0 or nxt.get(a) is None:
                    continue
                if a in victims:
                    cells['betrayed'].append(nxt[a])
                    cells[f"betrayed_{victims[a]}"].append(nxt[a])
                else:
                    cells['cooperator_not_betrayed'].append(nxt[a])
        else:
            victims = {v for e in evs if e['type'] == 'game' for v in e['victims']}
            for a in sess['outcomes'].get(t, {}):
                if coop_value(sess, t, a) is None or nxt.get(a) is None:
                    continue
                cells['victim_of_drop' if a in victims else 'not_victim'].append(nxt[a])
    return {k: rate(v) for k, v in sorted(cells.items())}


def spillover(sess, off, convs):
    """P(coop t+1) of i by what it could have been told at t: 'told_bad' (a conversation partner
    exploited its own partner / group or broke its word to i), 'told_good' (it talked, nothing bad),
    'no_conversation'. Split by own coop at t."""
    f = facts(sess, off['events'], convs)
    told = defaultdict(list)
    for (t, i, j), v in f.items():
        told[(t, i)].append(v)
    cells = defaultdict(list)
    for t in off['rounds']:
        for i in sess['outcomes'].get(t, {}):
            nxt, own = coop_value(sess, t + 1, i), coop_value(sess, t, i)
            if nxt is None or own is None:
                continue
            vs = told.get((t, i))
            lvl = 'no_conversation' if not vs else ('told_bad' if any(v['bad'] for v in vs) else 'told_good')
            cells[(lvl, 'own_coop' if own >= 0.5 else 'own_defect')].append(nxt)
    return {f"{lvl}/{own}": rate(v) for (lvl, own), v in sorted(cells.items())}


def selection(sess, off, convs):
    """P(i starts a conversation with neighbour j at t+1) among agent-rounds in which i started at
    least one, by what i could have been told about j at t: bad / good / not_told (no conversation
    at t)."""
    f = facts(sess, off['events'], convs)
    neighbors = defaultdict(set)
    for a, b in (sess['net'].get('edges') or []):
        neighbors[a].add(b)
        neighbors[b].add(a)
    cells = defaultdict(list)
    for rec in sess['contacts']:
        t1 = rec['round_number']
        for a, r in rec['agents'].items():
            i = int(a)
            if not r['initiated']:
                continue
            for j in neighbors[i]:
                v = f.get((t1 - 1, i, j))
                lvl = 'not_told' if v is None else ('bad' if v['bad'] else 'good')
                cells[lvl].append(int(j in r['initiated']))
    return {k: rate(v) for k, v in sorted(cells.items())}


def initiation_shares(sess, off, convs):
    """Per round t >= 2: the share of the conversations agents started whose target was a neighbour
    that i could have been told something bad about at t - 1 (a revealed exploit, a broken word, or
    in PD a B), against what that share would be if targets were drawn by the base weights
    (exp(beta z) with the channel compatibility, else uniform) and with the logged reputation weights."""
    f = facts(sess, off['events'], convs)
    neighbors = defaultdict(list)
    for a, b in (sess['net'].get('edges') or []):
        neighbors[a].append(b)
        neighbors[b].append(a)
    beta = (sess['net'].get('channels') or {}).get('beta') or 0.0
    z = {(e['a'], e['b']): e['z'] for e in sess['net'].get('edge_attrs') or []}

    def base_w(i, j):
        return math.exp(beta * z[(min(i, j), max(i, j))]) if beta and z else 1.0

    def bad(t, i, j):
        v = f.get((t, i, j))
        return bool(v) and (v['bad'] or (sess['game'] == 'pd' and v['coop'] == 0.0))

    rows = []
    for rec in sess['contacts']:
        t1 = rec['round_number']
        if t1 < 2:
            continue
        rw = rec.get('reputation_weights') or {}
        n = hit = 0
        e_base = e_rep = 0.0
        for a, r in rec['agents'].items():
            i = int(a)
            for j in r['initiated']:
                nb = neighbors[i]
                if not nb:
                    continue
                n += 1
                hit += bad(t1 - 1, i, j)
                wb = {k: base_w(i, k) for k in nb}
                wr = {k: wb[k] * (rw.get(str(i), {}).get(str(k), 1.0)) for k in nb}
                e_base += sum(wb[k] for k in nb if bad(t1 - 1, i, k)) / sum(wb.values())
                e_rep += sum(wr[k] for k in nb if bad(t1 - 1, i, k)) / sum(wr.values())
        if n:
            rows.append(dict(round=t1, initiations=n, observed=round(hit / n, 4),
                             expected_base=round(e_base / n, 4), expected_with_reputation=round(e_rep / n, 4)))
    return rows


def consistency_by_round(sess, off):
    """Per agent and round, cumulative: consistency (Beta score of kept words), statements, share of
    cooperative choices so far. Offline twin of the 'agents' key of dyads.jsonl."""
    stated, kept = defaultdict(int), defaultdict(int)
    vals = defaultdict(list)
    rows = []
    for t in off['rounds']:
        for e in off['events'][t]:
            if e['type'] == 'word':
                stated[e['speaker']] += 1
                kept[e['speaker']] += int(e['kept'])
        for a in sorted(sess['outcomes'].get(t, {})):
            v = coop_value(sess, t, a)
            if v is not None:
                vals[a].append(v)
            rows.append(dict(agent_id=a, round=t, stated=stated[a],
                             consistency=round(B.beta_score(kept[a], stated[a]), 4),
                             coop=round(sum(vals[a]) / len(vals[a]), 4) if vals[a] else None))
    return rows


def speakers(sess, off):
    """Kept rate and cooperation of agents that announced a choice, against those that did not."""
    said = defaultdict(set)
    for t in off['rounds']:
        for e in off['events'][t]:
            if e['type'] == 'word':
                said[t].add(e['speaker'])
    stated, silent = [], []
    for t in off['rounds']:
        for a in sess['outcomes'].get(t, {}):
            v = coop_value(sess, t, a)
            if v is not None:
                (stated if a in said[t] else silent).append(v)
    words = [e for t in off['rounds'] for e in off['events'][t] if e['type'] == 'word']
    return dict(kept_rate=rate(1 if e['kept'] else 0 for e in words),
                coop_when_stated=rate(stated), coop_when_not_stated=rate(silent))


def honesty(sess, convs):
    """Past-tense self-reports ('I chose X in round 2') against the record."""
    ctx = game_ctx(sess)
    shown = sess['shown']
    n = n_round = true = 0
    examples = []
    for t, cs in convs.items():
        for c in cs:
            if c['replied'] is False:
                continue
            for m in c['messages']:
                for s in B.SENTENCE_SPLIT.split(B._norm(m['text'])):
                    mm = B.PAST_REPORT.search(s)
                    if not mm:
                        continue
                    opts = B._options_in(s[mm.start():], sess['game'], ctx)
                    if len(opts) != 1:
                        continue
                    n += 1
                    ref = B.ROUND_REF.findall(s)
                    if len(ref) != 1 or int(ref[0]) >= t or int(ref[0]) < 1:
                        continue
                    o = sess['outcomes'].get(int(ref[0]), {}).get(m['agent_id'])
                    if not B.usable(o):
                        continue
                    n_round += 1
                    truth = shown.get(o['choice']) if sess['game'] == 'pd' else str(int(float(o['choice'])))
                    ok = opts[0] == truth
                    true += ok
                    if not ok and len(examples) < 5:
                        examples.append(dict(round=t, speaker=m['agent_id'], text=s[:200], truth=truth))
    return dict(past_reports=n, verifiable=n_round, true=true,
                true_rate=round(true / n_round, 4) if n_round else None, false_examples=examples)


def gossip(sess, convs):
    """Messages naming a third participant, and the subset where the speaker had been told about
    that participant in an earlier round (a conversation with it)."""
    nodes = sess['net'].get('nodes') or []
    full = {n['agent_id']: re.sub(r'\s*\(.*$', '', n.get('name') or '').strip() for n in nodes}
    first_count = defaultdict(int)
    for nm in full.values():
        if nm:
            first_count[nm.split()[0]] += 1
    pats = {}
    for a, nm in full.items():
        if not nm:
            continue
        forms = [re.escape(nm)]
        if first_count[nm.split()[0]] == 1 and len(nm.split()[0]) > 2:
            forms.append(re.escape(nm.split()[0]))
        pats[a] = re.compile(r"\b(" + '|'.join(forms) + r")\b")
    met = defaultdict(set)  # agent -> {others talked with in rounds so far}
    n_msgs = n_third = n_relay = n_relay_choice = 0
    ctx = game_ctx(sess)
    examples = []
    for t in sorted(convs):
        for c in convs[t]:
            if c['replied'] is False:
                continue
            for m in c['messages']:
                n_msgs += 1
                listener = c['responder'] if m['agent_id'] == c['initiator'] else c['initiator']
                named = [a for a, p in pats.items() if a not in (m['agent_id'], listener) and p.search(m['text'])]
                if not named:
                    continue
                n_third += 1
                told = [a for a in named if a in met[m['agent_id']]]
                if told:
                    n_relay += 1
                    if B._options_in(m['text'], sess['game'], ctx):
                        n_relay_choice += 1
                    if len(examples) < 5:
                        examples.append(dict(round=t, speaker=m['agent_id'], text=m['text'][:200]))
        for c in convs[t]:
            if talked(c):
                met[c['initiator']].add(c['responder'])
                met[c['responder']].add(c['initiator'])
    return dict(messages=n_msgs, naming_third_party=n_third, relaying_known_fact=n_relay,
                relaying_and_naming_option=n_relay_choice, examples=examples)


def summarize(gdir):
    sess = read_session(gdir)
    off = events_from_logs(gdir, sess)
    convs = conversations(sess)
    reveal = sess['settings'].get('net_reveal_choices', 'none')
    out = dict(game=sess['game'], session_dir=gdir, reveal=reveal,
               rho_w=sess['settings'].get('net_reputation_word_weight', 0.0),
               rho_c=sess['settings'].get('net_reputation_choice_weight', 0.0),
               intent_rule_version=B.INTENT_RULE_VERSION,
               facts_are=('revealed' if reveal != 'none' else 'latent (placebo)'),
               statements=statements(sess, off, convs), game_events=game_summary(sess, off),
               aftermath=aftermath(sess, off), spillover=spillover(sess, off, convs),
               selection=selection(sess, off, convs),
               initiation_shares=initiation_shares(sess, off, convs), speakers=speakers(sess, off),
               honesty=honesty(sess, convs), gossip=gossip(sess, convs),
               online_equals_offline=check_online(gdir, off))
    return out


# -- audit sample ----------------------------------------------------------------------------

def extractions(gdir):
    """Every extracted statement with its source text: [(round, speaker, value, text, kept)]."""
    sess = read_session(gdir)
    off = events_from_logs(gdir, sess)
    convs = conversations(sess)
    rows = []
    for t, cs in convs.items():
        for c in cs:
            if c['replied'] is False:
                continue
            for e in off['events'][t]:
                if e['type'] == 'word' and e['conv_id'] == c['conv_id']:
                    texts = [m['text'] for m in c['messages'] if m['agent_id'] == e['speaker']]
                    rows.append(dict(session=gdir, round=t, conv_id=c['conv_id'], speaker=e['speaker'],
                                     value=e['stated_shown'], kept=e['kept'], texts=texts))
    return rows


def show(s):
    print(f"{s['session_dir']}  game {s['game']}  reveal {s['reveal']}  rho_w {s['rho_w']}  rho_c {s['rho_c']}")
    st = s['statements']
    print(f"  statements {st['statements']} of {st['speaker_conversations']} speaker-conversations "
          f"(coverage {st['coverage']}), kept {st['kept']} broken {st['broken']}, status {st['status']}")
    print("  game events:", s['game_events']['by_kind'], 'exploits', s['game_events']['exploits'])
    for key in ('aftermath', 'spillover', 'selection', 'speakers', 'honesty', 'gossip'):
        print(f"  {key}:", s[key])
    if s['online_equals_offline'] is not None:
        print("  online == offline:", s['online_equals_offline'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('gdir', nargs='+', help='<sim_dir>/game/<session_code>')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--audit', type=int, default=0, help='print N random extractions to check by hand')
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args(argv)
    if args.audit:
        rows = [r for g in args.gdir for r in extractions(g)]
        random.Random(args.seed).shuffle(rows)
        print(json.dumps(rows[:args.audit], ensure_ascii=False, indent=1))
        return
    outs = [summarize(g) for g in args.gdir]
    if args.json:
        print(json.dumps(outs if len(outs) > 1 else outs[0], ensure_ascii=False, indent=2))
    else:
        for s in outs:
            show(s)


if __name__ == '__main__':
    main()
