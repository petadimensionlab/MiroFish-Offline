"""
Dyad hooks 'betrayal' and 'reputation' (NOTES.md #57).

Registered into dyads.DYAD_HOOKS when this module is imported (the bridge does
it); a DyadLedger built with net_dyad_hooks names them in order. Both run in
the bridge's round_complete under its lock, once per round, after the
decisions of the round are known. The rules of what counts as an event are in
betrayal.py; this module is the bookkeeping.

betrayal
    Appends the round's word / game / exploit events to ledger.events (written
    to dyads.jsonl as 'events'), keeps compact cumulative counts on the dyads
    they concern (ext['game'] on (victim, by), ext['word'] on (speaker,
    listener), each keyed by the person it is about) and per-agent figures in
    ledger.agent_ext (analysis only, never shown to anyone). With a reveal
    (context 'reveal' 'talked' | 'pair') and a ledger that keeps memory, it also
    tells every agent, for each conversation it had in the round, what the other
    chose: a MemoryItem of kind 'note' (memory.py renders it). A fact that shows
    a broken word, or under 'pair' an exploit, gets the context's salience, on the
    note and on the owner's items of that conversation, so it is remembered longer.
reputation
    What agent i could know about each neighbour j from those facts: ext['rep'][i]
    on the dyad (i, j) = {W, B, ...}, W the Beta score of j keeping the words it
    said to i, B the Beta score of j's revealed choices being cooperative. With no
    reveal nothing is revealed, and the same numbers are kept as ext['latent_rep']
    (analysis only: the placebo of a run that tells nobody anything). The bridge
    reads 'rep' to weight who contacts whom (ρ_w, ρ_c); scores never appear in a
    prompt.

Context keys: game, partner (pd: agent -> partner), groups (pgg: agent -> members),
outcomes_by_round, convs (this round's conversations, copies), game_ctx
({options} pd / {endowment} pgg), shown (pd: {'A': symbol, 'B': symbol}),
endowment, reveal ('none' | 'talked' | 'pair'), salience.
"""

from typing import Any, Dict, List, Optional

from . import betrayal as bt
from . import dyads as dy


def _ctx_for_rules(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ctx.get('game_ctx') or {})
    if ctx.get('game') == 'pd':
        out['shown'] = ctx.get('shown') or {}
    else:
        out.setdefault('endowment', ctx.get('endowment'))
    return out


def _coop_so_far(ctx: Dict[str, Any], t: int, a: int) -> Optional[float]:
    """Share of cooperative rounds of agent a up to round t (pgg: mean share of the endowment)."""
    vals = []
    for r in range(1, t + 1):
        o = ctx['outcomes_by_round'].get(r, {}).get(a)
        if not bt.usable(o):
            continue
        if ctx['game'] == 'pd':
            vals.append(1.0 if o['choice'] == 'A' else 0.0)
        else:
            vals.append(int(float(o['choice'])) / ctx['endowment'])
    return round(sum(vals) / len(vals), 4) if vals else None


def _bump(table: Dict[str, Any], key: str, **inc) -> Dict[str, Any]:
    row = table.setdefault(key, {})
    for k, v in inc.items():
        row[k] = row.get(k, 0) + v
    return row


def _choice_shown(ctx: Dict[str, Any], outcome: Dict[str, Any]):
    """What the others are told of one outcome: PD the shown symbol, pgg the amount."""
    if ctx['game'] == 'pd':
        return (ctx.get('shown') or {}).get(outcome['choice'], outcome['choice'])
    return int(float(outcome['choice']))


def hook_betrayal(ledger: dy.DyadLedger, t: int, outcomes: Dict[int, Dict[str, Any]],
                  ctx: Dict[str, Any]) -> None:
    game = ctx.get('game')
    if game not in ('pd', 'pgg'):
        return
    convs = ctx.get('convs') or []
    rules_ctx = _ctx_for_rules(ctx)
    words, _ = bt.word_events(t, convs, outcomes, game, rules_ctx)
    if game == 'pd':
        games, _ = bt.game_events_pd(t, ctx['outcomes_by_round'], ctx.get('partner') or {})
    else:
        games, _ = bt.game_events_pgg(t, ctx['outcomes_by_round'], ctx.get('groups') or {}, ctx['endowment'])
    ledger.events.extend(words + games)

    # compact cumulative state on the dyads the events concern
    for e in games:
        if e['type'] != 'game':
            continue
        for victim in ([e['victim']] if game == 'pd' else e['victims']):
            d = ledger.get(victim, e['by'])
            row = _bump(d.ext.setdefault('game', {}), str(victim), count=1)
            row['last_round'], row['last_kind'] = t, e['kind']
            ledger.touch(victim, e['by'])
    for e in words:
        d = ledger.get(e['speaker'], e['listener'])
        row = _bump(d.ext.setdefault('word', {}), str(e['listener']),
                    kept=int(e['kept']), broken=int(not e['kept']))
        row['last_round'] = t
        ledger.touch(e['speaker'], e['listener'])

    # per-agent figures (analysis only)
    for a in sorted(outcomes):
        row = ledger.agent_ext.setdefault(a, dict(stated=0, kept=0, consistency=bt.beta_score(0, 0),
                                                  coop=None, exploits=0))
        row['stated'] += sum(1 for e in words if e['speaker'] == a)
        row['kept'] += sum(1 for e in words if e['speaker'] == a and e['kept'])
        row['exploits'] += sum(1 for e in games if e['type'] == 'exploit' and e['by'] == a)
        row['consistency'] = round(bt.beta_score(row['kept'], row['stated']), 4)
        row['coop'] = _coop_so_far(ctx, t, a)

    reveal = ctx.get('reveal') or 'none'
    if reveal != 'none' and ledger.keep_memory:
        _reveal(ledger, t, outcomes, ctx, convs, words, games, reveal)


def _reveal(ledger: dy.DyadLedger, t: int, outcomes: Dict[int, Dict[str, Any]], ctx: Dict[str, Any],
            convs: List[Dict[str, Any]], words: List[Dict[str, Any]], games: List[Dict[str, Any]],
            reveal: str) -> None:
    """Tell each side of a conversation what the other chose, as a note in its memory."""
    s = float(ctx.get('salience') or 1.0)
    game = ctx['game']
    broken = {(e['listener'], e['speaker']) for e in words if not e['kept']}
    exploiters = {e['by'] for e in games if e['type'] == 'exploit'}
    for c in convs:
        if not bt.talked(c):
            continue
        for i, j in ((c['initiator'], c['responder']), (c['responder'], c['initiator'])):
            oj = outcomes.get(j)
            if not bt.usable(oj):
                continue
            ext: Dict[str, Any] = {'reveal': reveal}
            if game == 'pd':
                ext['other_choice_shown'] = _choice_shown(ctx, oj)
                if reveal == 'pair':
                    op = outcomes.get((ctx.get('partner') or {}).get(j))
                    if bt.usable(op):
                        ext['other_partner_choice_shown'] = _choice_shown(ctx, op)
            else:
                ext['amount'] = _choice_shown(ctx, oj)
                if reveal == 'pair':
                    others = sorted((int(float(outcomes[m]['choice'])) for m in (ctx.get('groups') or {}).get(j, [])
                                     if m != j and bt.usable(outcomes.get(m))), reverse=True)
                    if others:
                        ext['group_amounts'] = others
            mismatch = (i, j) in broken or (reveal == 'pair' and j in exploiters)
            sal = s if mismatch else 1.0
            d = ledger.get(i, j)
            d.memory.setdefault(i, []).append(dy.MemoryItem(
                kind='note', round_number=t, conv_id=f"r{t}n{i}-{j}", started_by=None, replied=True,
                messages=[], salience=sal, ext=ext))
            if mismatch and sal != 1.0:
                for item in d.memory.get(i, []):
                    if item.kind in ('conv', 'pair_chat') and item.conv_id == c['conv_id']:
                        item.salience = sal


def hook_reputation(ledger: dy.DyadLedger, t: int, outcomes: Dict[int, Dict[str, Any]],
                    ctx: Dict[str, Any]) -> None:
    game = ctx.get('game')
    if game not in ('pd', 'pgg'):
        return
    key = 'rep' if (ctx.get('reveal') or 'none') != 'none' else 'latent_rep'
    pair_mode = (ctx.get('reveal') or 'none') == 'pair'
    exploiters = {e['by'] for e in ledger.events if e.get('type') == 'exploit' and e.get('round') == t}
    words = [e for e in ledger.events if e.get('type') == 'word' and e.get('round') == t]
    for c in ctx.get('convs') or []:
        if not bt.talked(c):
            continue
        for i, j in ((c['initiator'], c['responder']), (c['responder'], c['initiator'])):
            d = ledger.get(i, j)
            oj = outcomes.get(j)
            if not d.edge or not bt.usable(oj):
                continue
            row = d.ext.setdefault(key, {}).setdefault(str(i), dict(
                W=0.5, B=0.5, n_word=0, n_choice=0, exploits_seen=0, kept=0, succ=0.0))
            stated = [e for e in words if e['speaker'] == j and e['listener'] == i and e['conv_id'] == c['conv_id']]
            row['n_word'] += len(stated)
            row['kept'] += sum(1 for e in stated if e['kept'])
            row['n_choice'] += 1
            row['succ'] += (1.0 if oj['choice'] == 'A' else 0.0) if game == 'pd' \
                else int(float(oj['choice'])) / ctx['endowment']
            if pair_mode and j in exploiters:
                row['exploits_seen'] += 1
            row['W'] = round(bt.beta_score(row['kept'], row['n_word']), 4)
            row['B'] = round(bt.beta_score(row['succ'], row['n_choice']), 4)
            row['succ'] = round(row['succ'], 4)
            ledger.touch(i, j)


dy.DYAD_HOOKS['betrayal'] = hook_betrayal
dy.DYAD_HOOKS['reputation'] = hook_reputation
