"""Betrayal events, revealed choices and reputation (NOTES.md #57). Pure rules,
the dyad hooks, memory notes, reputation-weighted contacts, the bridge end to
end with a fake step-server client, the offline recomputation and the
byte-identity of everything with the new settings at their defaults. No LLM."""

import importlib.util
import json
import os
import re
import sys

import pytest

from app.services import betrayal as bt
from app.services import dyad_hooks  # noqa: F401  (registers the hooks)
from app.services import dyads as dy
from app.services import experiment_bridge as eb
from app.services import memory as mem
from app.services import network_chat as nc
from tests._golden import (render_golden, render_network_golden, contacts_golden, dyads_run_golden)
from tests.test_channel_dyads import (  # noqa: F401  (fixtures and helpers shared with #55 tests)
    ch_sim, write_channel_sim, _configure)
from tests.test_network_chat import (  # noqa: F401
    FAKE, FakeClient, fake, sim_dir, pd_agents, pgg_agents, _events)

HERE = os.path.dirname(__file__)
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts', 'experiment')
sys.path.insert(0, SCRIPTS)
import analyze_betrayal as ab  # noqa: E402

SYMS = ['△', '□', '○', '◇']
PD = dict(options=SYMS)
PGG = dict(endowment=20)


def _run(b, code, n, rounds):
    import time
    for r in range(1, rounds + 1):
        for a in range(n):
            b.decide(code, r, a)
        b.round_complete(code, r, {'outcomes': [
            dict(agent_id=a, choice=b._sessions[code].decisions[(r, a)].choice, payoff=10) for a in range(n)]})
    time.sleep(0.2)


# 1 ----------------------------------------------------------------------------------

@pytest.mark.parametrize('texts,expected', [
    (["I intend to select △ again for Round 2."], ('△', 'single')),
    (["I'm still deciding whether to try △ again for round 3, or stick with ◇."], (None, 'hedged')),
    (["In round 1 my partner chose ○. I'll choose □ again."], ('□', 'single')),
    (["My partner chose ○ last round, so I'll choose □ this time."], ('□', 'single')),
    (["My partner's switch to △ in round 4 has cost me. I am not sure what to do."], (None, 'none')),
    (["I'll choose △ in round 3 to avoid the volatility of ◇."], ('△', 'single')),
    (["I'll pick △ rather than ○, but I may change my mind."], ('△', 'single')),
    (["I will choose △ if you choose ○."], (None, 'none')),
    (["Hello! How is your partner doing?"], (None, 'none')),
    (["I will choose △ in round 3.", "As I said, I plan to choose □."], (None, 'ambiguous')),
    (["I will choose △ in round 3.", "Again: I plan to pick △."], ('△', 'single')),
    (["I’ll go with $\\diamond$ this round."], ('◇', 'single')),
    (["Selecting △ is a logical approach. By selecting ○ we both earn 30."], (None, 'none')),
    (["I also intend to choose □ in Round 5."], ('□', 'single')),
    (["I'm sticking with ○ for round 9."], ('○', 'single')),
    (["I will choose △ in round 5 and △ in round 6."], ('△', 'single')),
])
def test_stated_intention_pd(texts, expected):
    assert bt.stated_intention(texts, 'pd', PD, round_number=3 if '3' in ''.join(texts) else 10) == expected


def test_stated_intention_future_round_and_letters():
    assert bt.stated_intention(["I will choose △ in round 5."], 'pd', PD, round_number=3) == (None, 'none')
    letters = dict(options=['A', 'B'])
    assert bt.stated_intention(["As a rule, I will choose B."], 'pd', letters, 2) == ('B', 'single')
    assert bt.stated_intention(["I will go with it."], 'pd', letters, 2) == (None, 'none')


@pytest.mark.parametrize('text,expected', [
    ("I'll put in 10 in round 3, after 50 points.", ('10', 'single')),
    ("I will put in 15 points this round.", (None, 'none')),
    ("I plan to put in 20%.", (None, 'none')),
    ("I'll contribute 8 in round 2, since 3 rounds are left.", ('8', 'single')),
    ("I will put in 5, maybe 10.", (None, 'none')),
    ("I'm not sure whether I'll put in 5.", (None, 'hedged')),
    ("I will put in 25.", (None, 'none')),
    ("Others put in 4 last round, so I intend to put in 12.", ('12', 'single')),
])
def test_stated_intention_pgg(text, expected):
    assert bt.stated_intention([text], 'pgg', PGG, round_number=3) == expected


# 2 ----------------------------------------------------------------------------------

def _outs(rows):
    """{round: {agent: choice}} -> outcomes_by_round with optional (choice, missing)."""
    out = {}
    for t, row in rows.items():
        out[t] = {a: (dict(choice=c[0], payoff=0, missing=c[1]) if isinstance(c, tuple)
                      else dict(choice=c, payoff=0)) for a, c in row.items()}
    return out


def test_game_events_pd():
    partner = {0: 1, 1: 0, 2: 3, 3: 2, 4: 5, 5: 4}
    ob = _outs({
        1: {0: 'A', 1: 'A', 2: 'A', 3: 'B', 4: 'A', 5: 'A'},
        2: {0: 'A', 1: 'A', 2: 'A', 3: 'B', 4: 'A', 5: 'A'},
        3: {0: 'A', 1: 'B', 2: 'A', 3: 'B', 4: 'A', 5: 'A'},
        4: {0: 'A', 1: 'B', 2: 'B', 3: 'B', 4: 'A', 5: 'B'},
        5: {0: 'A', 1: 'A', 2: 'A', 3: 'A', 4: ('A', True), 5: 'B'},
    })
    ev1, _ = bt.game_events_pd(1, ob, partner)
    assert ev1 == [dict(type='game', round=1, victim=2, by=3, kind='first', streak=0),
                   dict(type='exploit', round=1, by=3, partner=2)]
    ev2, _ = bt.game_events_pd(2, ob, partner)
    assert [(e['victim'], e['kind'], e['streak']) for e in ev2 if e['type'] == 'game'] == [(2, 'repeat', 0)]
    ev3, _ = bt.game_events_pd(3, ob, partner)
    assert [(e['victim'], e['by'], e['kind'], e['streak']) for e in ev3 if e['type'] == 'game'] == \
        [(0, 1, 'break', 2), (2, 3, 'repeat', 0)]
    ev4, _ = bt.game_events_pd(4, ob, partner)
    assert [(e['victim'], e['kind']) for e in ev4 if e['type'] == 'game'] == [(0, 'repeat'), (4, 'break')]
    assert [e['by'] for e in ev4 if e['type'] == 'exploit'] == [1, 5]
    ev5, skipped = bt.game_events_pd(5, ob, partner)
    assert ev5 == [] and skipped == 1  # (4, 5): agent 4 missing; the pair is skipped from both sides


def test_game_events_pgg_drop():
    groups = {a: [0, 1, 2, 3] for a in range(4)}
    ob = _outs({1: {0: '10', 1: '10', 2: '10', 3: '10'}, 2: {0: '10', 1: '10', 2: '10', 3: '2'}})
    # round 2, agent 3: prev 10 >= mean others 10; now 2 < mean others 10 and < 10: drops; victims 0, 1, 2
    ev, _ = bt.game_events_pgg(2, ob, groups, 20)
    drop = [e for e in ev if e['type'] == 'game']
    assert drop == [dict(type='game', round=2, by=3, kind='drop', prev=10, now=2, mean_others_prev=10.0,
                         mean_others_now=10.0, victims=[0, 1, 2])]
    assert [e['by'] for e in ev if e['type'] == 'exploit'] == [3]
    # a member that was already below the mean exploits but does not drop
    ob2 = _outs({1: {0: '10', 1: '10', 2: '10', 3: '0'}, 2: {0: '10', 1: '10', 2: '10', 3: '0'}})
    ev2, _ = bt.game_events_pgg(2, ob2, groups, 20)
    assert [e['type'] for e in ev2] == ['exploit']
    # missing outcome: skipped
    ob3 = _outs({1: {0: '10', 1: '10', 2: '10', 3: '10'}, 2: {0: '10', 1: '10', 2: '10', 3: ('2', True)}})
    ev3, skipped = bt.game_events_pgg(2, ob3, groups, 20)
    assert not [e for e in ev3 if e['by'] == 3] and skipped == 1


# 3 ----------------------------------------------------------------------------------

def _conv(cid, i, j, msgs, replied=True):
    return dict(conv_id=cid, initiator=i, responder=j, replied=replied,
                messages=[dict(agent_id=a, text=t) for a, t in msgs])


def test_word_events_pd():
    ctx = dict(options=['△', '□'], shown={'A': '△', 'B': '□'})
    convs = [_conv('r2c0', 0, 1, [(0, "I will choose △ in round 2."), (1, "I plan to choose □.")]),
             _conv('r2c1', 2, 3, [(2, "I will choose △.")], replied=False),
             _conv('r2c2', 4, 5, [(4, "I will choose △."), (5, "I will choose □.")]),
             _conv('r2c3', 6, 7, [(6, "I will choose □."), (7, "Hi there.")])]
    outcomes = {0: dict(choice='A'), 1: dict(choice='A'), 2: dict(choice='B'), 3: dict(choice='B'),
                4: dict(choice='B', missing=True), 5: dict(choice='B'), 6: dict(choice='A'), 7: dict(choice='A')}
    ev, counts = bt.word_events(2, convs, outcomes, 'pd', ctx)
    got = {(e['speaker'], e['listener']): e for e in ev}
    assert set(got) == {(0, 1), (1, 0), (5, 4), (6, 7)}      # unanswered 2 -> 3 ignored; agent 4 skipped
    assert got[(0, 1)]['kept'] and got[(0, 1)]['stated'] == 'A' and got[(0, 1)]['stated_shown'] == '△'
    assert not got[(1, 0)]['kept'] and got[(1, 0)]['chose'] == 'A' and got[(1, 0)]['stated'] == 'B'
    assert got[(5, 4)]['kept'] and not got[(6, 7)]['kept']
    assert counts['skipped_missing'] == 1 and counts['statements'] == 4
    assert all(e['round'] == 2 and e['type'] == 'word' and e['status'] == 'single' for e in ev)


def test_word_events_pgg_deficit():
    convs = [_conv('r3c0', 0, 1, [(0, "I'll put in 10."), (1, "I will put in 5.")])]
    ev, _ = bt.word_events(3, convs, {0: dict(choice='4'), 1: dict(choice='12')}, 'pgg', PGG)
    got = {e['speaker']: e for e in ev}
    assert (got[0]['kept'], got[0]['deficit'], got[0]['stated'], got[0]['chose']) == (False, 6, 10, 4)
    assert (got[1]['kept'], got[1]['deficit']) == (True, 0)


def test_default_source_is_skipped():
    convs = [_conv('r1c0', 0, 1, [(0, "I will choose △."), (1, "I will choose △.")])]
    outcomes = {0: dict(choice='A', source='llm_default'), 1: dict(choice='A', source='llm:x')}
    ev, counts = bt.word_events(1, convs, outcomes, 'pd', dict(options=['△'], shown={'A': '△', 'B': '□'}))
    assert [e['speaker'] for e in ev] == [1] and counts['skipped_missing'] == 1


def test_reputation_arithmetic():
    assert bt.beta_score(0, 0) == 0.5 and bt.beta_score(3, 3) == 0.8 and bt.beta_score(0, 2) == 0.25
    assert bt.contact_multiplier(0.5, 0.5, 2.0, 2.0) == 1.0
    assert bt.contact_multiplier(0.25, 0.5, 0.0, 3.0) == 1.0
    assert abs(bt.contact_multiplier(0.5, 0.25, 0.0, 4.0) - pow(2.718281828459045, -2.0)) < 1e-12


# 4 ----------------------------------------------------------------------------------

def _ledger(hooks, n=8, keep_memory=False, edges=None):
    excl = {i: {i ^ 1} for i in range(n)}
    edges = edges if edges is not None else [(i, j) for i in range(n) for j in range(i + 1, n) if j != i ^ 1]
    return dy.DyadLedger(range(n), edges, excl, keep_memory=keep_memory, hooks=hooks)


def _ctx(ob, convs, reveal='none', salience=1.0, game='pd'):
    return dict(game=game, session_code='s', partner={i: i ^ 1 for i in range(8)}, groups={},
                outcomes_by_round=ob, convs=convs,
                game_ctx=dict(options=['△', '□']), shown={'A': '△', 'B': '□'}, endowment=None,
                reveal=reveal, salience=salience)


def test_close_round_with_hooks():
    led = _ledger(['betrayal'])
    ob = _outs({1: {a: 'A' for a in range(8)}, 2: {**{a: 'A' for a in range(8)}, 3: 'B'}})
    convs = [_conv('r2c0', 0, 4, [(0, "I will choose △."), (4, "I will choose □.")])]
    rec1 = led.close_round(1, ob[1], _ctx(ob, []))
    assert rec1['events'] == [] and set(rec1['agents']) == {str(a) for a in range(8)}
    rec2 = led.close_round(2, ob[2], _ctx(ob, convs))
    kinds = sorted((e['type'], e.get('kind')) for e in rec2['events'])
    assert kinds == [('exploit', None), ('game', 'break'), ('word', None), ('word', None)]
    again = led.close_round(2, ob[2], _ctx(ob, convs))
    assert again == dict(round_number=2, dyads=[])
    rec3 = led.close_round(3, {a: dict(choice='A') for a in range(8)},
                           _ctx({**ob, 3: {a: dict(choice='A') for a in range(8)}}, []))
    assert rec3['events'] == []                                # emptied after each round
    # compact ext on the dyads: game on (victim 2, by 3), word on (speaker, listener) per listener
    assert led.get(2, 3).ext['game'] == {'2': dict(count=1, last_round=2, last_kind='break')}
    assert led.get(0, 4).ext['word'] == {'4': dict(kept=1, broken=0, last_round=2),
                                         '0': dict(kept=0, broken=1, last_round=2)}
    a4, a3 = rec2['agents']['4'], rec2['agents']['3']
    assert (a4['stated'], a4['kept'], a4['consistency']) == (1, 0, round(1 / 3, 4))
    assert (a3['exploits'], a3['coop']) == (1, 0.5)
    json.dumps(rec2)
    touched = {(d['a'], d['b']) for d in rec2['dyads']}
    assert {(2, 3), (0, 4)} <= touched


def test_ledger_without_hooks_record_unchanged():
    led = _ledger([])
    rec = led.close_round(1, {}, {})
    assert set(rec) == {'round_number', 'dyads'} and led.hooks == []


def test_reputation_needs_betrayal_first():
    with pytest.raises(ValueError, match='betrayal'):
        _ledger(['reputation'])
    with pytest.raises(ValueError, match='betrayal'):
        _ledger(['reputation', 'betrayal'])
    _ledger(['betrayal', 'reputation'])


def test_rep_scores_and_latent_rep():
    ob = _outs({1: {0: 'A', 1: 'A', 2: 'A', 3: 'A', 4: 'B', 5: 'A', 6: 'A', 7: 'A'}})
    convs = [_conv('r1c0', 0, 4, [(0, "I will choose △."), (4, "I will choose △.")])]
    led = _ledger(['betrayal', 'reputation'], keep_memory=True)
    led.close_round(1, ob[1], _ctx(ob, convs, reveal='talked'))
    # agent 0 learned: 4 said △ and chose □ (broken), chose B (not cooperative)
    assert led.get(0, 4).ext['rep']['0'] == dict(W=round(1 / 3, 4), B=round(1 / 3, 4), n_word=1, n_choice=1,
                                                   exploits_seen=0, kept=0, succ=0.0)
    # agent 4 learned: 0 kept its word and cooperated
    assert led.get(0, 4).ext['rep']['4']['W'] == round(2 / 3, 4)
    led2 = _ledger(['betrayal', 'reputation'], keep_memory=True)
    led2.close_round(1, ob[1], _ctx(ob, convs, reveal='none'))
    assert 'rep' not in led2.get(0, 4).ext and 'latent_rep' in led2.get(0, 4).ext
    assert not led2.get(0, 4).memory.get(0)                      # nothing told


# 5 ----------------------------------------------------------------------------------

NAMES = {a: dict(short=n, display=f"{n} (Dept)") for a, n in enumerate(['Zed', 'Ken', 'Amy', 'Bob', 'Cat'])}


def _note(round_number, shown, partner=None, sal=1.0, owner_other=(0, 1)):
    ext = {'reveal': 'pair' if partner else 'talked', 'other_choice_shown': shown}
    if partner:
        ext['other_partner_choice_shown'] = partner
    return dy.MemoryItem(kind='note', round_number=round_number, conv_id=f"r{round_number}n{owner_other[0]}-{owner_other[1]}",
                         started_by=None, replied=True, messages=[], salience=sal, ext=ext)


def _mem_ledger():
    return dy.DyadLedger(range(5), [], {}, keep_memory=True)


def test_notes_render_in_every_tier():
    led = _mem_ledger()
    for r, sym in enumerate(['◇', '◇', '◇', '△', '◇', '△', '◇'], start=1):
        led.get(0, 1).memory.setdefault(0, []).append(_note(r, sym))
    text, shown = mem.build_memory(0, led, 8, 2.0, 3200, NAMES)
    assert text.startswith('What you were told after earlier rounds:\n')
    lines = text.splitlines()[1:]
    assert lines == ["- After round 2, Ken chose ◇.", "- After round 3, Ken chose ◇.", "- After round 4, Ken chose △.",
                     "- After round 5, Ken chose ◇.", "- After round 6, Ken chose △.", "- After round 7, Ken chose ◇.",
                     "- Ken, after round 1: chose ◇ once."]
    tiers = {i['round']: i['tier'] for i in shown['items']}
    assert tiers == {1: 'aggregate', 2: 'gist', 3: 'gist', 4: 'excerpt', 5: 'excerpt', 6: 'verbatim', 7: 'verbatim'}
    assert all(i['kind'] == 'note' and 'texts' not in i for i in shown['items'])
    # three rounds in the aggregate tier: counts per symbol, most frequent first
    text2, shown2 = mem.build_memory(0, led, 11, 2.0, 3200, NAMES)
    assert "- Ken, after rounds 1–4: chose ◇ 3 times, △ once." in text2.splitlines()
    assert shown2['aggregates'][0]['kind'] == 'note' and not shown2['aggregates'][0]['dropped']


def test_notes_pair_and_pgg_wording():
    led = _mem_ledger()
    led.get(0, 1).memory.setdefault(0, []).append(_note(1, '◇', partner='△'))
    led.get(0, 2).memory.setdefault(0, []).append(dy.MemoryItem(
        kind='note', round_number=1, conv_id='r1n0-2', started_by=None, replied=True, messages=[],
        ext=dict(reveal='pair', amount=4, group_amounts=[20, 20, 16])))
    led.get(0, 3).memory.setdefault(0, []).append(dy.MemoryItem(
        kind='note', round_number=1, conv_id='r1n0-3', started_by=None, replied=True, messages=[],
        ext=dict(reveal='talked', amount=0)))
    text, _ = mem.build_memory(0, led, 2, 2.0, 3200, NAMES)
    assert "- After round 1, Ken chose ◇; Ken's partner chose △." in text
    assert "- After round 1, Amy put in 4; the others in Amy's group put in 20, 20 and 16." in text
    assert "- After round 1, Bob put in 0." in text
    led2 = _mem_ledger()
    for r, a in enumerate([12, 10, 0, 0], start=1):
        led2.get(0, 1).memory.setdefault(0, []).append(dy.MemoryItem(
            kind='note', round_number=r, conv_id=f"r{r}n0-1", started_by=None, replied=True, messages=[],
            ext=dict(reveal='talked', amount=a)))
    text2, _ = mem.build_memory(0, led2, 12, 2.0, 3200, NAMES)
    assert text2 == "What you were told after earlier rounds:\n- Ken, after rounds 1–4: put in 12, 10, 0 and 0.\n"


def _conv_item(round_number, cid, sal=1.0):
    return dy.MemoryItem(kind='conv', round_number=round_number, conv_id=cid, started_by=1, replied=True,
                         messages=[dict(agent_id=1, text="First sentence. Second sentence."),
                                   dict(agent_id=0, text="Reply here. And more.")], salience=sal)


def test_salience_keeps_conversation_verbatim():
    out = {}
    for sal in (1.0, 2.0):
        led = _mem_ledger()
        led.get(0, 1).memory.setdefault(0, []).append(_conv_item(2, 'r2c0', sal))
        text, shown = mem.build_memory(0, led, 5, 2.0, 3200, NAMES)   # delta 3, h 2
        out[sal] = (text, shown['items'][0])
    assert out[1.0][1]['tier'] == 'excerpt' and 'Second sentence' not in out[1.0][0]
    assert out[2.0][1]['tier'] == 'verbatim' and 'Second sentence' in out[2.0][0]
    assert abs(out[2.0][1]['weight'] - 2 ** -0.75) < 1e-3


def test_notes_budget_demotes_then_drops_oldest_first():
    led = _mem_ledger()
    for other in (1, 2, 3, 4):
        for r in range(1, 10):
            led.get(0, other).memory.setdefault(0, []).append(_note(r, '◇' if r % 2 else '△', owner_other=(0, other)))
    big, shown_big = mem.build_memory(0, led, 10, 2.0, 100000, NAMES)
    text, shown = mem.build_memory(0, led, 10, 2.0, 700, NAMES)
    assert len(big) > 700 and len(text) <= 700 and shown['over_budget_demotions'] > 0
    tiers = [i['tier'] for i in shown['items']]
    assert tiers.count('aggregate') > [i['tier'] for i in shown_big['items']].count('aggregate')
    tiny, shown_tiny = mem.build_memory(0, led, 10, 2.0, 150, NAMES)
    assert len(tiny) <= 150
    dropped = [a for a in shown_tiny['aggregates'] if a['dropped']]
    assert dropped and all(a['kind'] == 'note' for a in dropped)
    kept_rounds = [a['rounds'][1] for a in shown_tiny['aggregates'] if not a['dropped']]
    dropped_rounds = [a['rounds'][1] for a in dropped]
    assert not kept_rounds or max(dropped_rounds) <= min(kept_rounds)   # oldest last round goes first


def test_notes_do_not_touch_conversation_lines():
    led = _mem_ledger()
    led.get(0, 1).memory.setdefault(0, []).append(_conv_item(1, 'r1c0'))
    plain, _ = mem.build_memory(0, led, 3, 2.0, 3200, NAMES)
    led.get(0, 1).memory[0].append(_note(1, '◇'))
    with_note, shown = mem.build_memory(0, led, 3, 2.0, 3200, NAMES)
    assert with_note.startswith(plain)
    assert with_note[len(plain):] == "What you were told after earlier rounds:\n- After round 1, Ken chose ◇.\n"
    old, _ = mem.build_memory(0, led, 9, 2.0, 3200, NAMES)
    assert "1 conversation (round 1" in old and "- Ken, after round 1: chose ◇ once." in old


# 6 ----------------------------------------------------------------------------------

def _rep_session(ch_sim, code='rc', **kw):
    b = eb.ExperimentBridge()
    base = dict(net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair', net_memory_mode='decay',
                net_reputation_word_weight=0.0, net_reputation_choice_weight=4.0)
    base.update(kw)
    _configure(b, code, ch_sim, **base)
    return b, b._sessions[code]


def test_contact_dyad_identity_and_weights(ch_sim):
    b, state = _rep_session(ch_sim)
    s = state.settings
    base = state.network['dyad']
    # round 1: nothing known, every factor is 1 -> the very same object
    d, w = b._contact_dyad(state, s, 1)
    assert d is base and w is None
    # rho 0: same object even with scores present
    s0 = eb.BridgeSettings(**{**s.__dict__, 'net_reputation_choice_weight': 0.0})
    nbr = state.network['neighbors']
    j0 = nbr[0][0]
    state.dyads.get(0, j0).ext['rep'] = {'0': dict(W=0.5, B=0.25)}
    assert b._contact_dyad(state, s0, 2) == (base, None)
    d, w = b._contact_dyad(state, s, 2)
    assert d is not base and w is not None
    assert abs(w[0][j0] - round(pow(2.718281828459045, -2.0), 4)) < 1e-9
    assert all(m == 1.0 for i, row in w.items() for j, m in row.items() if (i, j) != (0, j0))
    for j in nbr[0]:
        assert abs(d.weights[0][j] - w[0][j]) < 1e-3     # beta 0: base weights are uniform
    # same neighbours, same medium / reply parameters
    assert d.pi is base.pi and d.reply_prob is base.reply_prob


def test_contact_weights_reduce_draws(ch_sim):
    b, state = _rep_session(ch_sim)
    net = state.network
    hub = max(net['neighbors'], key=lambda a: len(net['neighbors'][a]))
    j0 = net['neighbors'][hub][0]
    state.dyads.get(hub, j0).ext['rep'] = {str(hub): dict(W=0.5, B=0.25)}
    d, _ = b._contact_dyad(state, state.settings, 2)
    plain = net['dyad']
    lam = {a: 1.0 for a in net['lambdas']}
    hits = {'rep': 0, 'plain': 0}
    for seed in range(400):
        for name, dyad in (('rep', d), ('plain', plain)):
            convs, _ = nc.sample_contacts(net['neighbors'], lam, 2, seed, 3, 4, dyad=dyad)
            hits[name] += sum(1 for c in convs if c['initiator'] == hub and c['responder'] == j0)
    assert hits['plain'] > 30 and hits['rep'] < 0.8 * hits['plain']


# 7 ----------------------------------------------------------------------------------

class SayClient(FakeClient):
    """Every agent says it will choose the first listed option; ROGUE chooses the second."""
    ROGUE = 5

    def send_game_interview(self, interviews, **kw):
        out = []
        with FAKE.lock:
            FAKE.batches.append([(it['agent_id'], it['prompt']) for it in interviews])
        for it in interviews:
            p, a = it['prompt'], it['agent_id']
            opts = re.search(r'choose (\S+) or (\S+) at the same', p).groups()
            n = re.search(r'[Rr]ound (\d+) of', p).group(1)
            if '"message"' in p:
                resp = json.dumps({"message": f"I will choose {opts[0]} in round {n}."})
            else:
                resp = json.dumps({"choice": opts[1] if a == self.ROGUE else opts[0], "reason": "x"})
            out.append(dict(agent_id=a, response=resp))
        from tests.test_network_chat import _Result
        return _Result({'answers': out})


@pytest.fixture
def say(monkeypatch):
    monkeypatch.setattr(eb, 'SimulationIPCClient', SayClient)


def _gdir(sim, code):
    return os.path.join(sim, 'game', code)


def test_end_to_end_reveal(tmp_path, say):
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    common = dict(num_rounds=3, net_memory_mode='decay', net_contact_mean=3.0)
    _configure(b, 'ctl', sim, **common)
    _configure(b, 'rev', sim, net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair',
               net_betrayal_salience=2.0, **common)
    _configure(b, 'rep', sim, net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair',
               net_betrayal_salience=2.0, net_reputation_word_weight=2.0, net_reputation_choice_weight=1.0,
               **common)
    for code in ('ctl', 'rev', 'rep'):
        _run(b, code, 16, 3)

    # control: nothing new anywhere
    for row in _events(sim, 'ctl', 'dyads.jsonl'):
        assert set(row) == {'ts', 'round_number', 'dyads'}
    for name in ('network_chat.jsonl', 'llm_answers.jsonl'):
        for row in _events(sim, 'ctl', name):
            assert 'be told' not in row['prompt'] and 'were told' not in row['prompt'] and 'are told' not in row['prompt']
    for row in _events(sim, 'ctl', 'memory_shown.jsonl'):
        assert all('kind' not in i for i in row['items'])
    assert all('reputation_weights' not in r for r in _events(sim, 'ctl', 'network_contacts.jsonl'))

    # reveal: events, notes, sentences
    rows = _events(sim, 'rev', 'dyads.jsonl')
    assert [r['round_number'] for r in rows] == [1, 2, 3] and all('events' in r and 'agents' in r for r in rows)
    events = [e for r in rows for e in r['events']]
    rogue = SayClient.ROGUE
    broken = [e for e in events if e['type'] == 'word' and not e['kept']]
    assert broken and all(e['speaker'] == rogue for e in broken)
    assert any(e['type'] == 'word' and e['kept'] for e in events)
    assert all(e['kind'] in ('first', 'repeat', 'break') for e in events if e['type'] == 'game')
    assert {e['type'] for e in events} >= {'word', 'game', 'exploit'}
    mem_rows = _events(sim, 'rev', 'memory_shown.jsonl')
    notes = [i for r in mem_rows for i in r['items'] if i.get('kind') == 'note']
    assert notes and all(i['round'] < r['round_number'] or r['purpose'] == 'network_chat'
                         for r in mem_rows for i in r['items'] if i.get('kind') == 'note')
    assert not [r for r in mem_rows if r['round_number'] == 1 and any(i.get('kind') == 'note' for i in r['items'])]
    prompts = [(r['round_number'], r['prompt']) for name in ('network_chat.jsonl', 'llm_answers.jsonl')
               for r in _events(sim, 'rev', name)]
    assert any('What you were told after earlier rounds:' in p for _, p in prompts)
    assert all(rn >= 2 for rn, p in prompts if 'What you were told after earlier rounds:' in p)
    assert any("After this round, you will each be told which option the other person chose in round"
               in p and ", and which option their partner chose." in p for _, p in prompts)
    assert any("If " in p and " replies, after this round you will each be told which option the other person chose" in p
               for _, p in prompts)
    assert any("After each round, you and each person you talked with before it were told which option the other "
               "person chose and which option their partner chose.\nWhat you were told after earlier rounds:" in p
               for _, p in prompts)
    for _, p in prompts:
        assert not re.search(r'betray|trust score|reputation', p.split('Reply with only')[0], re.I) or 'trust' in p
    # salience of a broken word: the listener's item of that conversation is 2.0
    assert any(i['salience'] == 2.0 for r in mem_rows for i in r['items'])
    assert all('reputation_weights' not in r for r in _events(sim, 'rev', 'network_contacts.jsonl'))
    method = json.load(open(os.path.join(_gdir(sim, 'rev'), 'dyads.json'), encoding='utf-8'))['method']
    assert method['betrayal'] == dict(intent_rule_version=1, reveal='pair', salience=2.0, rho_w=0.0, rho_c=0.0)

    # reputation: same contacts in round 1, weights from round 2
    rc = _events(sim, 'rep', 'network_contacts.jsonl')
    rv = _events(sim, 'rev', 'network_contacts.jsonl')
    core = lambda ev: [(c['conv_id'], c['initiator'], c['responder']) for c in ev['conversations']]  # noqa: E731
    assert core(rc[0]) == core(rv[0]) and 'reputation_weights' not in rc[0]
    assert any('reputation_weights' in r for r in rc[1:])
    w = next(r['reputation_weights'] for r in rc[1:] if 'reputation_weights' in r)
    assert any(m != 1.0 for row in w.values() for m in row.values())
    meth = json.load(open(os.path.join(_gdir(sim, 'rep'), 'dyads.json'), encoding='utf-8'))['method']
    assert meth['betrayal']['rho_w'] == 2.0

    # online == offline
    for code in ('rev', 'rep'):
        off = ab.events_from_logs(_gdir(sim, code))
        chk = ab.check_online(_gdir(sim, code), off)
        assert chk == dict(rounds=3, equal=True, mismatched_rounds=[])
        s = ab.summarize(_gdir(sim, code))
        json.dumps(s)
        assert s['reveal'] == 'pair' and s['statements']['broken'] > 0
        assert s['statements']['coverage'] == 1.0
    assert ab.check_online(_gdir(sim, 'ctl'), ab.events_from_logs(_gdir(sim, 'ctl'))) is None
    s_ctl = ab.summarize(_gdir(sim, 'ctl'))
    assert s_ctl['facts_are'] == 'latent (placebo)' and s_ctl['statements']['broken'] > 0


def test_end_to_end_topology_none_game_events_only(tmp_path, say):
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    _configure(b, 'g', sim, num_rounds=3, net_topology='none', net_dyad_hooks='betrayal')
    _run(b, 'g', 16, 3)
    events = [e for r in _events(sim, 'g', 'dyads.jsonl') for e in r['events']]
    assert events and {e['type'] for e in events} <= {'game', 'exploit'}
    off = ab.events_from_logs(_gdir(sim, 'g'))
    assert ab.check_online(_gdir(sim, 'g'), off)['equal']


def test_end_to_end_pgg_reveal(tmp_path, monkeypatch):
    class PggSay(FakeClient):
        def send_game_interview(self, interviews, **kw):
            from tests.test_network_chat import _Result
            with FAKE.lock:
                FAKE.batches.append([(it['agent_id'], it['prompt']) for it in interviews])
            out = []
            for it in interviews:
                p, a = it['prompt'], it['agent_id']
                if '"message"' in p:
                    resp = json.dumps({"message": "I'll put in 10 this round."})
                else:
                    r = int(re.search(r'round (\d+) of', p).group(1))
                    resp = json.dumps({"contribution": 10 if a % 4 else max(0, 10 - 4 * (r - 1)), "reason": "x"})
                out.append(dict(agent_id=a, response=resp))
            return _Result({'answers': out})

    monkeypatch.setattr(eb, 'SimulationIPCClient', PggSay)
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    agents = pgg_agents(16)
    b.configure('pg', agents=agents, policy='llm', simulation_dir=sim, game='pgg', include_feed=False,
                num_rounds=3, inject_results='none', net_topology='ba', net_seed=1, net_contact_mean=3.0,
                net_memory_mode='decay', net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair',
                net_betrayal_salience=2.0)
    for r in range(1, 4):
        for a in range(16):
            b.decide('pg', r, a)
        b.round_complete('pg', r, {'outcomes': [
            dict(agent_id=a, choice=b._sessions['pg'].decisions[(r, a)].choice, payoff=20.0,
                 group_agent_ids=agents[a]['group_agent_ids']) for a in range(16)]})
    import time
    time.sleep(0.2)
    events = [e for r in _events(sim, 'pg', 'dyads.jsonl') for e in r['events']]
    drops = [e for e in events if e['type'] == 'game']
    assert drops and all(e['kind'] == 'drop' and e['by'] % 4 == 0 for e in drops)
    broken = [e for e in events if e['type'] == 'word' and not e['kept']]
    assert broken and all(e['deficit'] > 0 and e['speaker'] % 4 == 0 for e in broken)
    prompts = [r['prompt'] for name in ('network_chat.jsonl', 'llm_answers.jsonl') for r in _events(sim, 'pg', name)]
    assert any("how much the other person contributed in round" in p
               and ", and how much the others in their group contributed." in p for p in prompts)
    assert any("were told how much the other person contributed and how much the others in their group contributed.\n"
               "What you were told after earlier rounds:" in p for p in prompts)
    assert any(re.search(r"- After round \d, .* put in \d+; the others in .*'s group put in [\d, and]+\.", p)
               for p in prompts)
    assert ab.check_online(_gdir(sim, 'pg'), ab.events_from_logs(_gdir(sim, 'pg')))['equal']


# 8 ----------------------------------------------------------------------------------

REVEAL = dict(net_dyad_hooks='betrayal', net_reveal_choices='talked', net_memory_mode='decay')


@pytest.mark.parametrize('kwargs,match', [
    (dict(net_reveal_choices='everyone', **{k: v for k, v in REVEAL.items() if k != 'net_reveal_choices'}),
     'net_reveal_choices'),
    (dict(net_reveal_choices='talked', net_memory_mode='decay'), "needs net_dyad_hooks with 'betrayal'"),
    (dict(net_reveal_choices='talked', net_dyad_hooks='betrayal'), "net_memory_mode 'decay'"),
    (dict(net_reveal_choices='talked', net_dyad_hooks='betrayal', net_memory_mode='decay', net_topology='none'),
     "net_topology != 'none'"),
    (dict(net_dyad_hooks='reputation'), "needs 'betrayal' before it"),
    (dict(net_dyad_hooks='reputation,betrayal'), "needs 'betrayal' before it"),
    (dict(net_betrayal_salience=0.5, **REVEAL), 'net_betrayal_salience must be >= 1'),
    (dict(net_betrayal_salience=2.0), "needs net_reveal_choices"),
    (dict(net_dyad_hooks='betrayal', net_reputation_word_weight=-1.0), '>= 0'),
    (dict(net_dyad_hooks='betrayal', net_reputation_choice_weight=-0.5), '>= 0'),
    (dict(net_reputation_word_weight=1.0, **REVEAL), "needs net_dyad_hooks with 'reputation'"),
    (dict(net_reputation_choice_weight=1.0, net_dyad_hooks='betrayal,reputation'), "needs net_reveal_choices"),
    (dict(net_reputation_choice_weight=1.0, net_dyad_hooks='betrayal,reputation', net_reveal_choices='talked',
          net_memory_mode='decay', net_channels=False), "needs net_channels"),
])
def test_betrayal_configure_rejections(ch_sim, kwargs, match):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError, match=match):
        _configure(b, 'rej', ch_sim, **kwargs)
    assert 'rej' not in b._sessions


def test_betrayal_rejects_other_games(ch_sim):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError, match="'pd' or 'pgg'"):
        b.configure('bt', agents=[dict(agent_id=i, partner_agent_id=i ^ 1, group_agent_ids=[i ^ 1, i], role=1 + i % 2)
                                  for i in range(16)], policy='llm', simulation_dir=ch_sim, game='trust',
                    num_rounds=2, net_dyad_hooks='betrayal')


def test_betrayal_configure_accepts_good(ch_sim):
    b = eb.ExperimentBridge()
    out = _configure(b, 'ok', ch_sim, net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair',
                     net_memory_mode='decay', net_betrayal_salience=2.0, net_reputation_word_weight=2.0,
                     net_reputation_choice_weight=2.0)
    assert out['net_reveal_choices'] == 'pair' and out['net_reputation_word_weight'] == 2.0
    _configure(b, 'ok2', ch_sim, net_dyad_hooks='betrayal', net_topology='none')   # game events only
    _configure(b, 'ok3', ch_sim, net_dyad_hooks='betrayal,reputation')            # latent placebo arm
    assert b._sessions['ok2'].dyads.hooks == ['betrayal']


# 9 ----------------------------------------------------------------------------------

def test_golden_files_unchanged_with_defaults(tmp_path):
    with open(os.path.join(HERE, 'golden_prompts.json'), encoding='utf-8') as f:
        assert render_golden() == json.load(f)
    with open(os.path.join(HERE, 'golden_network_prompts.json'), encoding='utf-8') as f:
        assert render_network_golden() == json.load(f)
    with open(os.path.join(HERE, 'golden_contacts.json'), encoding='utf-8') as f:
        assert json.loads(json.dumps(contacts_golden())) == json.load(f)
    for g in ('pd', 'pgg'):
        (tmp_path / g).mkdir()
    with open(os.path.join(HERE, 'golden_dyads_run.json'), encoding='utf-8') as f:
        golden = json.load(f)
    now = dyads_run_golden(tmp_path)
    assert set(now) == set(golden)
    for game in golden:
        assert set(now[game]) == set(golden[game])
        for key in golden[game]:
            assert now[game][key] == golden[game][key], (game, key)


def test_defaults_add_nothing_new(ch_sim):
    """pd_net_ba_ch_mem as it is: no new keys in any log, no new section in any prompt."""
    b = eb.ExperimentBridge()
    _configure(b, 'mem', ch_sim, net_channel_beta=1.0, net_reply_model='reach', net_memory_mode='decay')
    _run(b, 'mem', 16, 3)
    for row in _events(ch_sim, 'mem', 'dyads.jsonl'):
        assert set(row) == {'ts', 'round_number', 'dyads'}
    doc = json.load(open(os.path.join(_gdir(ch_sim, 'mem'), 'dyads.json'), encoding='utf-8'))
    assert 'betrayal' not in doc['method']
    for row in _events(ch_sim, 'mem', 'network_contacts.jsonl'):
        assert 'reputation_weights' not in row
    for row in _events(ch_sim, 'mem', 'memory_shown.jsonl'):
        assert all('kind' not in i for i in row['items']) and all('kind' not in a for a in row['aggregates'])
    for name in ('network_chat.jsonl', 'llm_answers.jsonl'):
        for row in _events(ch_sim, 'mem', name):
            assert 'told' not in row['prompt'] and 'are told' not in row['prompt']
    assert not any(k.startswith('net_reveal') for k in ())
    assert b._sessions['mem'].dyads.hooks == []


def test_analyze_betrayal_loads_rules_by_path():
    spec = importlib.util.spec_from_file_location('x', os.path.join(SCRIPTS, 'analyze_betrayal.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.B.INTENT_RULE_VERSION == bt.INTENT_RULE_VERSION
    assert mod.B.stated_intention(["I will choose △."], 'pd', PD, 1) == bt.stated_intention(["I will choose △."], 'pd', PD, 1)


def test_analyze_betrayal_sections_on_synthetic_logs(tmp_path):
    """Hand-built logs: statement counts, honesty, gossip and the selection table."""
    g = tmp_path / 'game' / 's'
    g.mkdir(parents=True)
    labels = dict(cooperate='△', defect='□', order=['△', '□'])
    log = [dict(event='configure', settings=dict(game='pd', net_reveal_choices='none'), labels=labels)]
    choices = {1: {0: 'A', 1: 'A', 2: 'B', 3: 'A', 4: 'A', 5: 'A'}, 2: {0: 'A', 1: 'A', 2: 'B', 3: 'A', 4: 'A', 5: 'A'}}
    for t, ch in choices.items():
        log.append(dict(event='round_complete', round_number=t, outcomes=[
            dict(agent_id=a, choice=c, payoff=0, partner_agent_id=a ^ 1, source='llm:x', missing=False)
            for a, c in ch.items()]))
    (g / 'bridge_log.jsonl').write_text('\n'.join(json.dumps(x) for x in log))
    contacts = [dict(round_number=1, agents={str(a): dict(initiated=[], received=[], k_realized=0) for a in range(6)},
                     conversations=[dict(conv_id='r1c0', initiator=0, responder=2)]),
                dict(round_number=2, agents={**{str(a): dict(initiated=[], received=[], k_realized=0) for a in range(6)},
                                             '0': dict(initiated=[2], received=[], k_realized=1)},
                     conversations=[dict(conv_id='r2c0', initiator=0, responder=2),
                                    dict(conv_id='r2c1', initiator=4, responder=1)])]
    (g / 'network_contacts.jsonl').write_text('\n'.join(json.dumps(x) for x in contacts))
    msgs = [(1, 'r1c0', 0, 0, 2, "I will choose △ in round 1."), (1, 'r1c0', 1, 2, 0, "I'll choose △ in round 1. Bo said hi."),
            (2, 'r2c0', 0, 0, 2, "I chose △ in round 1. I will choose △ in round 2."),
            (2, 'r2c0', 1, 2, 0, "I plan to choose □."),
            (2, 'r2c1', 0, 4, 1, "I will choose △ in round 2. Eve told me she chose □ in round 1."),
            (2, 'r2c1', 1, 1, 4, "I will choose △.")]
    chat = [dict(round_number=t, conv_id=c, turn=turn, agent_id=a, other_agent_id=o, initiator=a if turn == 0 else o,
                 message=m, parse_error=None) for t, c, turn, a, o, m in msgs]
    (g / 'network_chat.jsonl').write_text('\n'.join(json.dumps(x) for x in chat))
    nodes = [dict(agent_id=a, name=n, display=f"{n} (X)") for a, n in enumerate(['Zoe', 'Yan', 'Alice', 'Bo', 'Dana', 'Eve'])]
    (g / 'network.json').write_text(json.dumps(dict(edges=[[0, 2], [4, 1], [0, 3]], nodes=nodes)))
    s = ab.summarize(str(g))
    st = s['statements']
    assert st['statements'] == 6 and st['broken'] == 1 and st['kept'] == 5   # only agent 2 in round 1 (says △, chose □)
    assert st['by_round'][0]['statements'] == 2
    assert s['honesty']['past_reports'] == 1 and s['honesty']['verifiable'] == 1 and s['honesty']['true'] == 1
    assert s['gossip']['naming_third_party'] == 2 and s['gossip']['relaying_known_fact'] == 0
    assert s['game_events']['by_kind'] == {'first': 1, 'repeat': 1}
    assert s['facts_are'] == 'latent (placebo)'
    assert s['selection']['bad']['n'] >= 1
    json.dumps(s)


def test_dashboards_with_betrayal(tmp_path, say):
    pytest.importorskip('altair')
    import dashboard
    import dashboard_compare
    from tests.test_channel_dyads import _export_csv
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    common = dict(num_rounds=4, net_memory_mode='decay', net_contact_mean=3.0)
    _configure(b, 'rep', sim, net_dyad_hooks='betrayal,reputation', net_reveal_choices='pair',
               net_betrayal_salience=2.0, net_reputation_word_weight=2.0, net_reputation_choice_weight=1.0, **common)
    _configure(b, 'ctl', sim, **common)
    for code in ('rep', 'ctl'):
        _run(b, code, 16, 4)
    csvs = {}
    for code in ('rep', 'ctl'):
        csvs[code] = tmp_path / f'{code}.csv'
        _export_csv(b._sessions[code], csvs[code], code)

    def run_dash(code, out):
        args = dashboard.argparse.Namespace(otree_csv=str(csvs[code]), sim_dir=sim, session=code, baseline_csv=None,
                                            out=str(tmp_path / out), max_chars=600, title=None)
        return dashboard.build(args)

    res = run_dash('rep', 'rep.html')
    res['chart'].to_dict(validate=True)
    titles = json.dumps(res['spec'])
    assert 'Revealed choices and consistency' in titles and 'Announced choice against actual choice' in titles
    assert 'Consistency of each agent' in titles and 'something bad was revealed about' in titles
    assert 'reputation weights' in titles                     # rho > 0: the second expectation is drawn
    assert 'SECRET' not in res['html']
    res_ctl = run_dash('ctl', 'ctl.html')
    assert 'reputation weights' not in json.dumps(res_ctl['spec']) and 'Revealed choices' in json.dumps(res_ctl['spec'])
    # without the chat log the game events (from the outcomes) are still there, the statements are not
    os.remove(os.path.join(_gdir(sim, 'ctl'), 'network_chat.jsonl'))
    os.remove(os.path.join(_gdir(sim, 'ctl'), 'network_contacts.jsonl'))
    run_dash('ctl', 'ctl2.html')['chart'].to_dict(validate=True)
    cmp = dashboard_compare.build([('rev', str(csvs['rep']), sim, 'rep'), ('plain', str(csvs['ctl']), sim, 'ctl')],
                                  str(tmp_path / 'cmp.html'))
    assert 'Words, deeds and who gets contacted' in json.dumps(cmp['spec'])
    info = {c['info']['label']: c['info']['betrayal'] for c in cmp['conds']}
    assert info['rev']['kept_rate'] is not None and info['plain']['kept_rate'] is None
