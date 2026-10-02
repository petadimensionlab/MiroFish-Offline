"""Communication memory with forgetting (NOTES.md #55): weights and tiers,
budget, extraction, the ledger and the bridge end to end. Fake client, no LLM."""

import json
import os
import re
import threading

import pytest

from app.services import dyads as dy
from app.services import experiment_bridge as eb
from app.services import memory as mem
from tests.test_channel_dyads import _configure, write_channel_sim
from tests.test_network_chat import (  # noqa: F401
    FAKE, FakeClient, _events, _run, fake, pd_agents, pgg_agents, sim_dir)

NAMES = {i: dict(short=f"P{i}", display=f"P{i} (Dept, Acme)") for i in range(8)}
PD_CTX = dict(options=['○', '◇'])
OPTS = PD_CTX['options']


def long_text(tag):
    return f"{tag} I will pick ◇ this round because it looks fair. Then a second sentence follows."


def conv(r, idx, i, j, texts=None, replied=True):
    texts = texts or [long_text(f"r{r}a"), f"r{r}b ○ ok"]
    speakers = [i, j]
    msgs = [dict(agent_id=speakers[k % 2], text=t) for k, t in enumerate(texts)]
    return dict(conv_id=f"r{r}c{idx}", initiator=i, responder=j, messages=msgs, replied=replied)


def ledger(rounds, other=1, n=6, per_round=None):
    led = dy.DyadLedger(range(n), [], {}, None, keep_memory=True)
    ment = lambda t: mem.extract_mentions(t, 'pd', PD_CTX)  # noqa: E731
    for r in rounds:
        led.record_network_round(r, per_round(r) if per_round else [conv(r, 0, 0, other)], ment)
    return led


def build(led, r=10, h=2.0, budget=3200, **kw):
    return mem.build_memory(0, led, r, h, budget, NAMES, PD_CTX, **kw)


# 2 -------------------------------------------------------------------------------

def test_weight_and_tiers():
    assert mem.weight(2, 2.0) == pytest.approx(0.5) and mem.weight(0, 2.0) == 1.0
    assert mem.weight(3, 3.0) == pytest.approx(0.5)
    ws = [mem.weight(d, 2.0) for d in range(12)]
    assert all(a > b for a, b in zip(ws, ws[1:]))
    for d in range(10):
        for s in (0.5, 1.0, 4.0):
            assert mem.weight(d, 2.0, s) == pytest.approx(mem.weight(d / s, 2.0, 1.0))
    # at h = 2 the verbatim tier is exactly the #54 window (delta 0-2)
    assert [mem.tier_index(mem.weight(d, 2.0)) for d in range(9)] == [0, 0, 0, 1, 1, 2, 2, 3, 3]
    assert mem.MEMORY_TIERS[0] == ('verbatim', 0.5)


# 3 -------------------------------------------------------------------------------

def test_tier_ordering_and_aggregate():
    led = ledger(range(1, 10))
    block, shown = build(led, 10)
    tiers = {it['round']: it['tier'] for it in shown['items']}
    assert [tiers[r] for r in (9, 8)] == ['verbatim'] * 2
    assert [tiers[r] for r in (7, 6)] == ['excerpt'] * 2
    assert [tiers[r] for r in (5, 4)] == ['gist'] * 2
    assert [tiers[r] for r in (3, 2, 1)] == ['aggregate'] * 3
    order = [t[0] for t in mem.MEMORY_TIERS]
    by_delta = [order.index(it['tier']) for it in sorted(shown['items'], key=lambda i: i['delta'])]
    assert by_delta == sorted(by_delta)
    assert 'Before round 9, with P1 (Dept, Acme):' in block
    assert 'Older conversations, as you remember them:\n- Round 4, with P1 (you started): ' in block
    assert 'What you remember about people you talked with earlier:\n- P1: 3 conversations (rounds 1–3);' in block
    assert block.index('Before round 9') < block.index('Older conversations') < block.index('What you remember')
    assert shown['aggregates'] == [dict(other='P1', n_convs=3, rounds=[1, 3], dropped=False)]
    # excerpt: first sentence only
    assert 'r7a I will pick ◇ this round because it looks fair. …' in block
    assert 'Then a second sentence' in block.split('Before round 8')[1].split('Older')[0]
    assert 'Then a second sentence' not in block.split('Before round 7')[1].split('Before round 8')[0]
    # the full history is kept in the ledger: nothing is dropped by age
    assert len(shown['items']) == 9


def test_this_round_shown_with_marker_and_window_equivalence():
    led = ledger([1, 2, 3])
    block, _ = build(led, 3)
    assert 'Before round 3 (this round), with P1 (Dept, Acme):\n- You: r3a I will pick' in block
    assert '- P1: r3b ○ ok' in block
    assert 'Older' not in block


# 4 -------------------------------------------------------------------------------

def test_budget_cap_and_demotion_order():
    per = lambda r: [conv(r, k, 0, 1 + k) for k in range(3)]  # noqa: E731
    led = ledger(range(1, 10), per_round=per)
    full, sfull = build(led, 10, budget=100000)
    assert sfull['over_budget_demotions'] == 0 and len(full) > 1500
    budget = 1200
    block, shown = build(led, 10, budget=budget)
    assert shown['over_budget_demotions'] > 0 and len(block) <= budget and shown['chars'] == len(block)
    order = [t[0] for t in mem.MEMORY_TIERS]
    # an older item is never shown in more detail than a newer one of the same person
    for p in ('P1', 'P2', 'P3'):
        rows = sorted((i for i in shown['items'] if i['other'] == p), key=lambda i: i['delta'])
        ranks = [order.index(i['tier']) for i in rows]
        assert ranks == sorted(ranks)
    # items of round 9 (delta 1) are demoted only after all older ones moved
    older = [order.index(i['tier']) for i in shown['items'] if i['delta'] >= 3]
    assert min(older) >= 1
    # tiny budget: aggregate lines go, oldest last conversation first
    block2, shown2 = build(led, 10, budget=400)
    assert len(block2) <= 400 or all(i['delta'] == 0 for i in shown2['items'] if i['tier'] != 'aggregate')
    assert shown2['over_budget_demotions'] >= shown['over_budget_demotions']


def test_this_round_never_demoted():
    per = lambda r: [conv(r, 0, 0, 1, texts=['x' * 300, 'y' * 300])]  # noqa: E731
    led = ledger([1, 2, 3], per_round=per)
    block, shown = build(led, 3, budget=400)
    now = [i for i in shown['items'] if i['delta'] == 0]
    assert now and now[0]['tier'] == 'verbatim'
    assert shown['chars'] > 400  # over budget rather than hiding this round
    old = [i for i in shown['items'] if i['delta'] > 0]
    assert all(i['tier'] in ('gist', 'aggregate') for i in old)


# 5 -------------------------------------------------------------------------------

def test_determinism():
    led = ledger(range(1, 8), per_round=lambda r: [conv(r, 0, 0, 1), conv(r, 1, 0, 2)])
    a = build(led, 9, budget=900)
    assert a == build(led, 9, budget=900) == build(led, 9, budget=900)


# 6 -------------------------------------------------------------------------------

def test_extract_mentions():
    assert mem.extract_mentions('I pick ◇ and not ○, ◇ again', 'pd', PD_CTX) == ['◇', '○']
    assert mem.extract_mentions('I like ○', 'pd', PD_CTX) == ['○']
    assert mem.extract_mentions('no symbols here', 'pd', PD_CTX) == []
    g = dict(endowment=20)
    assert mem.extract_mentions('I will put in 10, maybe 15 or 25, not 7.5 or 20.', 'pgg', g) == ['10', '15', '20']
    assert mem.extract_mentions('none', 'pgg', g) == []
    assert mem.first_sentence('Short one.') == 'Short one.'
    assert mem.first_sentence('A. B.') == 'A. …'
    assert len(mem.first_sentence('w' * 300)) == mem.EXCERPT_CHARS


def test_gist_aggregate_formats_and_unanswered():
    # gist: who started, what each side mentioned
    led = ledger([4], per_round=lambda r: [conv(4, 0, 1, 0, texts=['P1 here ◇ only', 'me ○ ok'])])
    _, shown = build(led, 10)
    assert shown['items'][0]['tier'] == 'gist'
    block, _ = build(led, 10)
    assert '- Round 4, with P1 (P1 started): P1 mentioned ◇; you mentioned ○.' in block
    # nothing named
    led = ledger([4], per_round=lambda r: [conv(4, 0, 0, 1, texts=['hello', 'hi'])])
    assert '(you started): P1 mentioned nothing specific; you mentioned nothing specific.' in build(led, 10)[0]
    # aggregate line with counts, quote and unanswered
    led = ledger([1, 2, 3], per_round=lambda r: [conv(r, 0, 1, 0, texts=[f'msg{r} ◇', 'x ○'])
                                                  if r < 3 else conv(3, 0, 0, 1, ['no answer'], False)])
    block, shown = build(led, 12)
    assert '- P1: 3 conversations (rounds 1–3, 1 unanswered); mentioned ◇ twice; last said: "msg2 ◇"' in block
    # unanswered, initiator side
    led = ledger([4], per_round=lambda r: [conv(4, 0, 0, 1, ['anyone there?'], False)])
    assert '- Round 4, you wrote to P1; no reply.' in build(led, 10)[0]
    block, _ = build(led, 4)
    assert 'Before round 4 (this round), with P1 (Dept, Acme):\n- You: anyone there?\n- (P1 did not reply)\n' in block
    # the responder never sees it, in the ledger or live
    resp, shown = mem.build_memory(1, led, 10, 2.0, 3200, NAMES, PD_CTX)
    assert resp == '' and shown['items'] == []
    live = dy.memory_items_from_convs(1, [dict(conv(4, 0, 0, 1, ['hi'], False), round_number=4)])
    assert live == {}


def test_excluded_conv_and_live_items():
    led = ledger([1])
    c = dict(conv(2, 0, 0, 2), round_number=2)
    live = dy.memory_items_from_convs(0, [c], lambda t: mem.extract_mentions(t, 'pd', PD_CTX))
    block, _ = build(led, 2, live_items=live, use_display=False)
    assert 'Before round 2 (this round), with P2:' in block and 'with P1:' in block
    block, _ = build(led, 2, live_items=live, exclude_conv='r2c0', use_display=False)
    assert 'with P2' not in block


# 8 -------------------------------------------------------------------------------

def test_salience_keeps_item_one_tier_up():
    led = ledger([7], per_round=lambda r: [conv(7, 0, 0, 1), conv(7, 1, 0, 2)])
    led.get(0, 2).memory[0][0].salience = 4.0
    _, shown = build(led, 10)  # delta 3
    tiers = {i['other']: i['tier'] for i in shown['items']}
    assert tiers == {'P1': 'excerpt', 'P2': 'verbatim'}
    assert mem.salience(led.get(0, 2).memory[0][0]) == 4.0


def test_salience_set_by_hook(monkeypatch):
    def hook(ledger_, r, outcomes, ctx):
        for d in ledger_.dyads.values():
            for owner, items in d.memory.items():
                for it in items:
                    if it.round_number == r and d.b == 2:
                        it.salience = 4.0

    monkeypatch.setitem(dy.DYAD_HOOKS, 'sal', hook)
    led = dy.DyadLedger(range(6), [], {}, None, keep_memory=True, hooks=['sal'])
    led.record_network_round(1, [conv(1, 0, 0, 1), conv(1, 1, 0, 2)])
    led.close_round(1, {}, {})
    _, shown = build(led, 4)
    assert {i['other']: i['tier'] for i in shown['items']} == {'P1': 'excerpt', 'P2': 'verbatim'}


# 7 -------------------------------------------------------------------------------

class MentionClient(FakeClient):
    """Every message names the first option and is unique."""
    n = 0
    lock = threading.Lock()

    def send_game_interview(self, interviews, **kw):
        res = super().send_game_interview(interviews, **kw)
        for it, a in zip(interviews, res.result['answers']):
            if '"message"' in a['response']:
                opt = re.search(r'choose (\S+) or (\S+) at the same', it['prompt']).group(1)
                with MentionClient.lock:
                    MentionClient.n += 1
                    k = MentionClient.n
                a['response'] = json.dumps({'message': f"Note{k}: I lean to {opt} this time. And more."})
        return res


@pytest.fixture
def mention(monkeypatch):
    MentionClient.n = 0
    monkeypatch.setattr(eb, 'SimulationIPCClient', MentionClient)


def test_decay_end_to_end(tmp_path, mention):
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    _configure(b, 'dec', sim, num_rounds=10, net_topology='er', net_channels=True,
               net_memory_mode='decay', net_contact_mean=2.0, net_reply_model='reach')
    _run(b, 'dec', pd_agents(16), 10)
    state = b._sessions['dec']
    shown = _events(sim, 'dec', 'memory_shown.jsonl')
    assert {r['purpose'] for r in shown} == {'decision', 'network_chat'}
    dec = {(r['round_number'], r['agent_id']): r for r in _events(sim, 'dec', 'llm_answers.jsonl')}
    # 1 decay mode never shows the #54 window block format twice
    for row in dec.values():
        assert row['prompt'].count('Conversations you had with other participants') <= 1
    # the round-10 decision prompt has the aggregate line of somebody met in round 1
    names = state.network['names']
    snap = {(d['a'], d['b']): d for d in state.dyads.snapshot()}
    found = 0
    for (a, b_), d in snap.items():
        for owner, other in ((a, b_), (b_, a)):
            items = state.dyads.get(owner, other).memory.get(owner, [])
            rounds = sorted({it.round_number for it in items})
            if rounds and rounds[0] == 1 and rounds[-1] <= 3:
                prompt = dec[(10, owner)]['prompt']
                short = names[other]['short']
                assert 'What you remember about people you talked with earlier:' in prompt
                assert re.search(rf"- {re.escape(short)}: \d conversations? \(rounds? 1", prompt), (owner, other)
                found += 1
    assert found > 0
    # every verbatim / excerpt text shown is in the prompt that was logged
    net_prompts = {(r['conv_id'], r['agent_id']): r['prompt']
                   for r in _events(sim, 'dec', 'network_chat.jsonl')}
    checked = 0
    for r in shown:
        texts = [t for it in r['items'] for t in it.get('texts', [])]
        if r['purpose'] == 'decision':
            prompt = dec[(r['round_number'], r['agent_id'])]['prompt']
        else:
            prompt = net_prompts.get((r['conv_id'], r['agent_id']))
        if prompt is None:
            continue
        for t in texts:
            assert t in prompt
            checked += 1
        assert r['chars'] <= r['budget'] or all(i['delta'] == 0 for i in r['items'] if i['tier'] != 'aggregate')
    assert checked > 50
    # verbatim window at h = 2 equals the #54 window: delta <= 2
    assert all(i['tier'] == 'verbatim' for r in shown for i in r['items'] if i['delta'] <= 2)
    assert all(i['tier'] != 'verbatim' for r in shown for i in r['items'] if i['delta'] > 2)
    # the responder never saw an unanswered conversation
    unanswered = [(c['conv_id'], c['responder']) for r in state.net_convs.values() for c in r
                  if c['replied'] is False]
    assert unanswered
    for conv_id, resp in unanswered:
        assert not any(i['conv_id'] == conv_id for r in shown if r['agent_id'] == resp for i in r['items'])
    json.dumps(shown)


def test_window_mode_writes_no_memory_log(tmp_path):
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    _configure(b, 'win', sim, num_rounds=2)
    _run(b, 'win', pd_agents(16), 2)
    assert not os.path.exists(os.path.join(sim, 'game', 'win', 'memory_shown.jsonl'))
    assert b._sessions['win'].dyads.keep_memory is False
    assert all(not d.memory for d in b._sessions['win'].dyads.dyads.values())


def test_decay_pgg_end_to_end(tmp_path):
    sim = write_channel_sim(tmp_path, n=8)
    b = eb.ExperimentBridge()
    agents = pgg_agents(8)
    b.configure('pg', agents=agents, policy='llm', simulation_dir=sim, game='pgg', include_feed=False,
                num_rounds=5, inject_results='none', net_topology='er', net_mean_degree=2,
                net_contact_mean=2.0, net_contact_dispersion=0, net_memory_mode='decay')
    _run(b, 'pg', agents, 5)
    prompts = [r['prompt'] for r in _events(sim, 'pg', 'llm_answers.jsonl') if r['round_number'] == 5]
    assert any('plays with their own group' in p and 'Before round' in p for p in prompts)
    assert _events(sim, 'pg', 'memory_shown.jsonl')


# 10 ------------------------------------------------------------------------------

@pytest.mark.parametrize('kwargs', [
    dict(net_memory_half_life=-1.0),
    dict(net_memory_budget_chars=399),
    dict(net_memory_mode='decay', net_memory_summary='llm'),
    dict(net_memory_mode='window', net_memory_summary='llm'),
    dict(net_memory_mode='decay', net_topology='none'),
    dict(net_memory_mode='nope'),
])
def test_memory_configure_rejections(tmp_path, kwargs):
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError):
        _configure(b, 'rej', sim, **kwargs)
    assert 'rej' not in b._sessions
