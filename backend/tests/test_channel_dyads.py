"""Channel-compatible dyads (NOTES.md #55): compatibility, biased contact
sampling, unanswered conversations, the dyad ledger and its logs. Fake
step-server client, no LLM calls."""

import json
import os
import re
import sys
import threading
from typing import Any, Dict, List, Set, Tuple

import numpy as np
import pytest

from app.services import dyads as dy
from app.services import experiment_bridge as eb
from app.services import network_chat as nc
from tests._golden import render_network_golden, contacts_golden
from tests.test_network_chat import (  # noqa: F401  (fixtures and helpers shared with #54 tests)
    FAKE, FakeClient, _Result, _events, _run, fake, pd_agents, pgg_agents, pd_excl, sim_dir)

HERE = os.path.dirname(__file__)
REAL_SIM = ('/Users/nakaoka/workspace/research/MiroFish-offline/backend/uploads/simulations/'
            'sim_workplace_ch_s1_n48')
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts', 'experiment'))


# -- verbatim copy of sample_contacts as of #54 (before dyads) ----------------------

def _sample_contacts_v1(neighbors: Dict[int, List[int]], lambdas: Dict[int, float], round_number: int,
                    seed: int, max_initiate: int, max_load: int
                    ) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Who starts a conversation with whom this round.

    k_drawn ~ Poisson(lambda_i); k_target = min(k_drawn, max_initiate,
    degree). In random order, round-robin over s = 1..max_initiate, an agent
    with k_target >= s picks a uniformly random neighbour whose pair is unused
    this round and whose load (initiated + received) is below max_load. No
    candidate, or the agent itself at max_load: the contact is dropped.

    Returns:
        (conversations [{conv_id, index, initiator, responder}],
         per-agent record {lambda, k_drawn, k_realized, initiated, received, dropped})
    """
    ids = sorted(neighbors)
    rng = np.random.default_rng([seed, 2, round_number])
    k_drawn = {a: int(rng.poisson(lambdas[a])) for a in ids}
    k_target = {a: min(k_drawn[a], max_initiate, len(neighbors[a])) for a in ids}
    order = [int(a) for a in rng.permutation(ids)]
    load = {a: 0 for a in ids}
    used: Set[Tuple[int, int]] = set()
    rec = {a: dict(**{'lambda': round(lambdas[a], 4)}, k_drawn=k_drawn[a], k_realized=0,
                   initiated=[], received=[], dropped=0) for a in ids}
    convs: List[Dict[str, Any]] = []
    for s in range(1, max_initiate + 1):
        for i in order:
            if k_target[i] < s:
                continue
            if load[i] >= max_load:
                rec[i]['dropped'] += 1
                continue
            cands = [j for j in neighbors[i]
                     if (min(i, j), max(i, j)) not in used and load[j] < max_load]
            if not cands:
                rec[i]['dropped'] += 1
                continue
            j = cands[int(rng.integers(len(cands)))]
            used.add((min(i, j), max(i, j)))
            load[i] += 1
            load[j] += 1
            index = len(convs)
            convs.append(dict(conv_id=f"r{round_number}c{index}", index=index, initiator=i, responder=j))
            rec[i]['initiated'].append(j)
            rec[j]['received'].append(i)
            rec[i]['k_realized'] += 1
    return convs, rec



# -- synthetic channel simulation -----------------------------------------------------

CHANNELS = [f"ch{k}" for k in range(8)]
MEDIA = list(nc.PEER_MEDIA) + ['all_staff_email']


def make_people(n, habits=None, seed=0, names=None):
    rng = np.random.default_rng(seed)
    levels = list(nc.LEVEL_VALUE)
    hab = list(nc.SEND_WEIGHT)
    people = []
    for i in range(n):
        people.append(dict(
            agent_id=i, name=(names[i] if names else f"Person{i:02d} Q."), company='Acme',
            department=f"Dept{i % 3}", seniority='staff', channel_engagement=float(rng.normal()),
            channels={c: dict(level=levels[int(rng.integers(4))], score=0.0) for c in CHANNELS},
            media_habits={m: (habits or hab[int(rng.integers(3))]) for m in MEDIA}))
    return people


def write_channel_sim(root, n=16, habits=None, seed=0, names=None, with_channels=True):
    d = root / 'chsim'
    d.mkdir()
    people = make_people(n, habits, seed, names)
    (d / 'personas_meta.json').write_text(json.dumps(dict(agent_order=list(range(n)), people=people)))
    (d / 'reddit_profiles.json').write_text(json.dumps(
        [dict(user_id=i, name=people[i]['name'], profession='Analyst') for i in range(n)]))
    if with_channels:
        (d / 'channels.json').write_text(json.dumps(dict(
            version=1, levels=list(nc.LEVEL_VALUE), media_levels=list(nc.SEND_WEIGHT),
            channels=[dict(id=c, use_in_text=(c != 'ch7')) for c in CHANNELS],
            media=[dict(id=m) for m in MEDIA])))
    return str(d)


@pytest.fixture
def ch_sim(tmp_path):
    return write_channel_sim(tmp_path)


def _configure(b, code, sim, n=16, **kw):
    base = dict(policy='llm', simulation_dir=sim, include_feed=False, num_rounds=3,
                inject_results='none', net_topology='ba', net_seed=1, net_contact_mean=2.0,
                net_channels=True)
    base.update(kw)
    return b.configure(code, agents=pd_agents(n), **base)


# 1 -------------------------------------------------------------------------------

@pytest.mark.parametrize('topology', ['er', 'ba', 'ws', 'ring'])
@pytest.mark.parametrize('mu', [1.0, 2.0])
def test_off_path_identical_to_v1(topology, mu):
    net = nc.build_network(range(16), pd_excl(16), topology, 4, 0.1, seed=3)
    lam = nc.draw_lambdas(range(16), mu, 0.5, seed=3)
    for seed in range(10):
        for r in range(1, 11):
            assert nc.sample_contacts(net['neighbors'], lam, r, seed, 3, 4) == \
                _sample_contacts_v1(net['neighbors'], lam, r, seed, 3, 4)


def test_golden_files_unchanged():
    with open(os.path.join(HERE, 'golden_contacts.json'), encoding='utf-8') as f:
        assert json.loads(json.dumps(contacts_golden())) == json.load(f)
    with open(os.path.join(HERE, 'golden_network_prompts.json'), encoding='utf-8') as f:
        golden = json.load(f)
    now = render_network_golden()
    assert set(now) == set(golden)
    for k in golden:
        assert now[k] == golden[k], k


# 2 -------------------------------------------------------------------------------

def _core(convs):
    return [(c['conv_id'], c['initiator'], c['responder']) for c in convs]


def test_beta0_always_same_contacts_as_v1(ch_sim):
    prof = nc.channel_profiles(ch_sim)
    compat = nc.compatibility(prof)
    net = nc.build_network(range(16), pd_excl(16), 'ba', 4, 0.1, seed=3)
    lam = nc.draw_lambdas(range(16), 1.5, 0.5, seed=3)
    dyad = nc.dyad_sampling(compat, net['neighbors'], 0.0, 'always')
    assert dyad.weights is None
    for seed in range(5):
        for r in range(1, 8):
            convs, rec = nc.sample_contacts(net['neighbors'], lam, r, seed, 3, 4, dyad=dyad)
            v1c, v1r = _sample_contacts_v1(net['neighbors'], lam, r, seed, 3, 4)
            assert _core(convs) == _core(v1c)
            assert all(c['replied'] is True and c['medium'] in nc.PEER_MEDIA for c in convs)
            for a, row in rec.items():
                assert {k: row[k] for k in v1r[a]} == v1r[a]
                assert row['unanswered'] == 0 and row['ignored'] == []


def test_beta0_always_end_to_end_same_contacts(ch_sim):
    b = eb.ExperimentBridge()
    _configure(b, 'on', ch_sim, net_channel_beta=0.0, net_reply_model='always')
    _configure(b, 'off', ch_sim, net_channels=False)
    _run(b, 'on', pd_agents(16), 3)
    _run(b, 'off', pd_agents(16), 3)
    on = _events(ch_sim, 'on', 'network_contacts.jsonl')
    off = _events(ch_sim, 'off', 'network_contacts.jsonl')
    assert len(on) == len(off) == 3
    for x, y in zip(on, off):
        assert [(c['conv_id'], c['initiator'], c['responder']) for c in x['conversations']] == \
            [(c['conv_id'], c['initiator'], c['responder']) for c in y['conversations']]
        assert x['agents'].keys() == y['agents'].keys()
        for a in x['agents']:
            assert {k: x['agents'][a][k] for k in y['agents'][a]} == y['agents'][a]
        assert all(c['replied'] is True and 'medium' in c and 'reply_draw' in c for c in x['conversations'])
        assert all(set(c) == {'conv_id', 'initiator', 'responder'} for c in y['conversations'])


# 4 -------------------------------------------------------------------------------

def test_compat_properties(ch_sim):
    prof = nc.channel_profiles(ch_sim)
    assert prof['channels'] == CHANNELS and prof['media'] == list(nc.PEER_MEDIA)
    comp = nc.compatibility(prof)
    assert len(comp.compat) == 16 * 15 // 2
    for (a, b), v in comp.compat.items():
        assert a < b and 0.0 <= v <= 1.0 and 0.0 <= comp.topic[(a, b)] <= 1.0
        assert comp.compat[(a, b)] == pytest.approx(
            0.5 * comp.topic[(a, b)] + 0.5 * comp.media[(a, b)])
        assert comp.media[(a, b)] == pytest.approx((comp.reach[(a, b)] + comp.reach[(b, a)]) / 2)
    assert np.mean(list(comp.z.values())) == pytest.approx(0.0, abs=1e-9)
    assert np.std(list(comp.z.values())) == pytest.approx(1.0, abs=1e-9)
    w0 = nc.compatibility(prof, topic_weight=0.0)
    assert w0.compat == w0.media
    with pytest.raises(ValueError):
        nc.compatibility(prof, topic_weight=1.5)


def test_compat_identical_personas_and_hand_calc(tmp_path):
    root = tmp_path / 'x'
    root.mkdir()
    d = write_channel_sim(root, n=6)
    meta = json.load(open(os.path.join(d, 'personas_meta.json')))
    p = meta['people']
    p[1]['channels'] = json.loads(json.dumps(p[0]['channels']))  # person 1 = person 0
    p[0]['media_habits'] = {m: 'habitually' for m in MEDIA}
    p[1]['media_habits'] = {m: 'habitually' for m in MEDIA}
    p[2]['media_habits'] = {m: 'rarely' for m in MEDIA}
    p[2]['media_habits']['direct_email'] = 'habitually'
    json.dump(meta, open(os.path.join(d, 'personas_meta.json'), 'w'))
    comp = nc.compatibility(nc.channel_profiles(d))
    assert comp.topic[(0, 1)] == pytest.approx(1.0)
    assert comp.reach[(0, 1)] == pytest.approx(0.95)  # habitually -> habitually
    # 0 writes to 2 (habitually everywhere): all media equally likely, 2 answers
    # with 0.95 on direct_email and 0.2 on the four others
    assert comp.reach[(0, 2)] == pytest.approx((0.95 + 4 * 0.2) / 5)
    # 2 writes by direct_email (3 of 7) or else rarely (1 each), 0 answers 0.95 everywhere
    assert comp.reach[(2, 0)] == pytest.approx(0.95)
    assert comp.media[(0, 2)] == pytest.approx((comp.reach[(0, 2)] + 0.95) / 2)
    assert dict(comp.pi[2])['direct_email'] == pytest.approx(3 / 7)
    assert sum(p for _, p in comp.pi[0]) == pytest.approx(1.0)


def test_compat_missing_channels_is_none(tmp_path):
    root = tmp_path / 'y'
    root.mkdir()
    d = write_channel_sim(root, with_channels=False)
    assert nc.channel_profiles(d) is None
    assert nc.channel_profiles(None) is None
    assert nc.channel_profiles(str(tmp_path / 'nonexistent')) is None


def test_configure_net_channels_without_channels_raises(sim_dir):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError, match='channels'):
        b.configure('nochan', agents=pd_agents(16), policy='llm', simulation_dir=sim_dir,
                    net_topology='ba', net_channels=True)
    assert 'nochan' not in b._sessions


def test_real_channel_sim_numbers():
    if not os.path.isdir(REAL_SIM):
        pytest.skip('real channel sim missing')
    comp = nc.compatibility(nc.channel_profiles(REAL_SIM))
    A = np.array(list(comp.compat.values()))
    assert len(comp.ids) == 48 and A.min() > 0.3 and A.max() < 0.95
    assert comp.pop_sd == pytest.approx(0.092, abs=0.005)
    assert np.median(list(comp.topic.values())) == pytest.approx(0.48, abs=0.02)


# 5 -------------------------------------------------------------------------------

def test_beta_prefers_compatible_neighbour(ch_sim):
    comp = nc.compatibility(nc.channel_profiles(ch_sim))
    nb = {i: [j for j in range(16) if j != i] for i in range(16)}
    top = max(nb[0], key=lambda j: comp.z[(0, j)])
    lam = {i: (50.0 if i == 0 else 0.0) for i in range(16)}
    counts = {}
    for beta in (0.0, 3.0):
        dyad = nc.dyad_sampling(comp, nb, beta, 'always')
        hit = 0
        for r in range(1, 301):
            convs, _ = nc.sample_contacts(nb, lam, r, 7, 1, 4, dyad=dyad)
            hit += sum(1 for c in convs if c['initiator'] == 0 and c['responder'] == top)
        counts[beta] = hit
    assert counts[0.0] < 300 * 3 / 15
    assert counts[3.0] > 3 * counts[0.0] and counts[3.0] > 60
    with pytest.raises(ValueError):
        nc.dyad_sampling(comp, nb, -1.0)


def test_dyad_stream_does_not_touch_contact_stream(ch_sim):
    comp = nc.compatibility(nc.channel_profiles(ch_sim))
    net = nc.build_network(range(16), pd_excl(16), 'er', 4, 0.1, seed=3)
    lam = nc.draw_lambdas(range(16), 1.5, 0.5, seed=3)
    a = nc.sample_contacts(net['neighbors'], lam, 2, 5, 3, 4, dyad=nc.dyad_sampling(comp, net['neighbors'], 0, 'always'))
    b = nc.sample_contacts(net['neighbors'], lam, 2, 5, 3, 4, dyad=nc.dyad_sampling(comp, net['neighbors'], 0, 'reach'))
    assert [(c['initiator'], c['responder'], c['medium'], c['reply_draw']) for c in a[0]] == \
        [(c['initiator'], c['responder'], c['medium'], c['reply_draw']) for c in b[0]]


# 6 -------------------------------------------------------------------------------

class CountingClient(FakeClient):
    """Every message is unique, so a prompt can be searched for one conversation."""
    n = 0
    lock = threading.Lock()

    def send_game_interview(self, interviews, **kw):
        res = super().send_game_interview(interviews, **kw)
        for a in res.result['answers']:
            if '"message"' in a['response']:
                with CountingClient.lock:
                    CountingClient.n += 1
                    a['response'] = json.dumps({'message': f"note{CountingClient.n} from agent {a['agent_id']}"})
        return res


@pytest.fixture
def counting(monkeypatch):
    CountingClient.n = 0
    monkeypatch.setattr(eb, 'SimulationIPCClient', CountingClient)


def test_reach_end_to_end(tmp_path, counting):
    sim = write_channel_sim(tmp_path, habits='rarely')
    b = eb.ExperimentBridge()
    _configure(b, 'rch', sim, num_rounds=10, net_reply_model='reach', net_contact_mean=3.0)
    _run(b, 'rch', pd_agents(16), 10)
    state = b._sessions['rch']
    contacts = _events(sim, 'rch', 'network_contacts.jsonl')
    convs = [c for e in contacts for c in e['conversations']]
    assert len(convs) > 150  # about 18 a round
    rate = np.mean([c['replied'] for c in convs])
    assert 0.1 < rate < 0.3  # REPLY_PROB['rarely'] = 0.2
    assert all(c['replied'] == c['reply_draw'] for c in convs)
    prompts = {}
    for row in _events(sim, 'rch', 'llm_answers.jsonl'):
        prompts[(row['round_number'], row['agent_id'])] = row['prompt']
    checked = 0
    for r, ev in enumerate(contacts, start=1):
        load = {a: 0 for a in range(16)}
        for c in ev['conversations']:
            load[c['initiator']] += 1
            load[c['responder']] += int(c['replied'])
        assert max(load.values()) <= state.settings.net_max_load
        for a, row in ev['agents'].items():
            assert len(row['received']) + len(row['initiated']) == load[int(a)]
            assert row['unanswered'] == sum(1 for c in ev['conversations']
                                            if c['initiator'] == int(a) and not c['replied'])
        live = {c['conv_id']: c for c in state.net_convs[r]}
        for c in ev['conversations']:
            if c['replied']:
                continue
            conv = live[c['conv_id']]
            assert len(conv['messages']) <= 1 and conv['max_turns'] == 1
            if not conv['messages']:
                continue
            text = conv['messages'][0]['text']
            assert text not in prompts[(r, c['responder'])]
            assert text in prompts[(r, c['initiator'])]
            after = prompts[(r, c['initiator'])].split(text, 1)[1]
            assert 'did not reply' in after
            assert c['initiator'] in ev['agents'][str(c['responder'])]['ignored']
            assert c['initiator'] not in ev['agents'][str(c['responder'])]['received']
            checked += 1
    assert checked > 20
    logs = _events(sim, 'rch', 'network_chat.jsonl')
    assert logs
    # a decide() answer of the responder does not list the unanswered conversation
    out = b.decide('rch', 10, 0)
    for c in out['network_chat']:
        assert not (c.get('replied') is False and c['other_agent_id'] != 0 and c['initiator'] != 0)


def test_conversation_view_marks_unanswered():
    names = {0: dict(short='A', display='A (x)'), 1: dict(short='B', display='B (x)')}
    conv = dict(round_number=2, conv_id='r2c0', index=0, initiator=0, responder=1, replied=False,
                messages=[dict(agent_id=0, text='hi')])
    v = nc.conversation_view([conv], 0, names)
    assert v[0]['unanswered'] is True
    assert 'unanswered' not in nc.conversation_view([dict(conv, replied=True)], 0, names)[0]
    assert 'unanswered' not in nc.conversation_view([{k: v for k, v in conv.items() if k != 'replied'}], 0, names)[0]


# 7 -------------------------------------------------------------------------------

def test_next_wave_respects_max_turns():
    c1 = dict(conv_id='a', index=0, initiator=0, responder=1, messages=[], max_turns=1)
    c2 = dict(conv_id='b', index=1, initiator=2, responder=3, messages=[])
    w = nc.next_wave([c1, c2], 2)
    assert {(c['conv_id'], s) for c, s in w} == {('a', 0), ('b', 2)}
    c1['messages'].append(dict(agent_id=0, text='x'))
    c2['messages'].append(dict(agent_id=2, text='x'))
    w = nc.next_wave([c1, c2], 2)
    assert [(c['conv_id'], s) for c, s in w] == [('b', 3)]


# 8 -------------------------------------------------------------------------------

def test_dyad_logs_match_contacts(ch_sim):
    b = eb.ExperimentBridge()
    _configure(b, 'dl', ch_sim, net_reply_model='reach', net_channel_beta=1.0)
    _run(b, 'dl', pd_agents(16), 3)
    doc = json.load(open(os.path.join(ch_sim, 'game', 'dl', 'dyads.json'), encoding='utf-8'))
    assert doc['version'] == 1 and len(doc['dyads']) == 120
    assert {'levels', 'peer_media', 'send_weight', 'reply_prob', 'topic_weight', 'pop_mean', 'pop_sd',
            'channels_sha256'} <= set(doc['method'])
    assert {'a', 'b', 'edge', 'excluded', 'compat', 'topic', 'media', 'z', 'reach_ab', 'reach_ba',
            'shared'} <= set(doc['dyads'][0])
    assert sum(d['excluded'] for d in doc['dyads']) == 8
    lines = _events(ch_sim, 'dl', 'dyads.jsonl')
    assert [x['round_number'] for x in lines] == [1, 2, 3]
    contacts = _events(ch_sim, 'dl', 'network_contacts.jsonl')
    for line, ev in zip(lines, contacts):
        convs = ev['conversations']
        assert sum(sum(d['attempts'].values()) for d in line['dyads']) == len(convs)
        assert sum(d['answered'] for d in line['dyads']) == sum(c['replied'] for c in convs)
        assert sum(d['unanswered'] for d in line['dyads']) == sum(not c['replied'] for c in convs)
    state = b._sessions['dl']
    snap = state.dyads.snapshot()
    allc = [c for ev in contacts for c in ev['conversations']]
    assert sum(d['answered'] + d['unanswered'] for d in snap) == len(allc)
    assert sum(d['messages'] for d in snap) == sum(len(c['messages']) for r in state.net_convs.values() for c in r)
    net = json.load(open(os.path.join(ch_sim, 'game', 'dl', 'network.json'), encoding='utf-8'))
    assert len(net['edge_attrs']) == len(net['edges']) and net['edges'][0] == [net['edge_attrs'][0]['a'],
                                                                             net['edge_attrs'][0]['b']]
    assert net['channels']['beta'] == 1.0 and net['channels']['reply_model'] == 'reach'
    assert 'channel_engagement' in net['nodes'][0]


def test_hook_runs_once_per_round(ch_sim, monkeypatch):
    calls = []

    def hook(ledger, r, outcomes, ctx):
        calls.append((r, sorted(outcomes)))
        ledger.get(0, 1).ext['seen'] = r
        ledger.touch(0, 1)

    monkeypatch.setitem(dy.DYAD_HOOKS, 'probe', hook)
    b = eb.ExperimentBridge()
    _configure(b, 'hk', ch_sim, net_dyad_hooks='probe')
    _run(b, 'hk', pd_agents(16), 3)
    summary = {'outcomes': [dict(agent_id=a, choice='A', payoff=10) for a in range(16)]}
    assert b.round_complete('hk', 3, summary)['duplicate'] is True
    assert [c[0] for c in calls] == [1, 2, 3]
    lines = _events(ch_sim, 'hk', 'dyads.jsonl')
    assert len(lines) == 3
    assert all(any(d['a'] == 0 and d['b'] == 1 and d['ext'] == {'seen': x['round_number']}
                   for d in x['dyads']) for x in lines)


def test_unknown_hook_rejected(ch_sim):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError, match='hooks'):
        _configure(b, 'bad', ch_sim, net_dyad_hooks='betrayal')
    assert 'bad' not in b._sessions
    assert dy.DYAD_HOOKS == {}


def test_ledger_without_network_control_arm(ch_sim):
    b = eb.ExperimentBridge()
    _configure(b, 'ctl', ch_sim, net_topology='none')
    _run(b, 'ctl', pd_agents(16), 2)
    doc = json.load(open(os.path.join(ch_sim, 'game', 'ctl', 'dyads.json'), encoding='utf-8'))
    assert len(doc['dyads']) == 120 and not any(d['edge'] for d in doc['dyads'])
    lines = _events(ch_sim, 'ctl', 'dyads.jsonl')
    assert [x['dyads'] for x in lines] == [[], []]
    assert not os.path.exists(os.path.join(ch_sim, 'game', 'ctl', 'network.json'))


# 9 -------------------------------------------------------------------------------

def _chat_run(sim, code, model, rounds=4):
    b = eb.ExperimentBridge()
    b.configure(code, agents=pd_agents(16), policy='llm', simulation_dir=sim, include_feed=False,
                num_rounds=rounds, inject_results='none', chat_turns=1, net_channels=True,
                net_pair_chat_model=model)
    _run(b, code, pd_agents(16), rounds)
    return b


def test_pair_chat_compat_reference(ch_sim):
    a = _chat_run(ch_sim, 'pa', 'compat')
    b = _chat_run(ch_sim, 'pb', 'compat')
    c = _chat_run(ch_sim, 'pc', 'always')
    ev = lambda code: [e for e in _events(ch_sim, code) if e['event'] == 'chat_phase']  # noqa: E731
    sk_a = [e['skipped_pairs'] for e in ev('pa')]
    assert sk_a == [e['skipped_pairs'] for e in ev('pb')]
    assert sum(len(x) for x in sk_a) > 0
    assert all(e['pairs'] + len(s) == 8 for e, s in zip(ev('pa'), sk_a))
    assert all('skipped_pairs' not in e and e['pairs'] == 8 for e in ev('pc'))
    snap = a._sessions['pa'].dyads.snapshot()
    assert sum(d['pair_chat_skips'] for d in snap) == sum(len(x) for x in sk_a)
    assert sum(d['pair_chats'] for d in snap) == sum(e['pairs'] for e in ev('pa'))
    skipped_pair = tuple(sk_a[0][0])
    assert skipped_pair not in a._sessions['pa'].chats[1]
    assert all(len(v) == 1 for v in c._sessions['pc'].chats[1].values()) and len(c._sessions['pc'].chats[1]) == 8


# 10 ------------------------------------------------------------------------------

@pytest.mark.parametrize('kwargs', [
    dict(net_channel_beta=-1.0),
    dict(net_channel_topic_weight=1.5),
    dict(net_reply_model='sometimes'),
    dict(net_channel_prompt='topic'),
    dict(net_pair_chat_model='random'),
    dict(net_channels=False, net_channel_beta=1.0),
    dict(net_channels=False, net_reply_model='reach'),
    dict(net_channels=False, net_channel_prompt='medium'),
    dict(net_topology='none', net_channel_beta=1.0),
    dict(net_topology='none', net_reply_model='reach'),
    dict(net_pair_chat_model='compat'),
    dict(net_memory_mode='forget'),
    dict(net_memory_half_life=0.0),
    dict(net_memory_budget_chars=100),
    dict(net_memory_summary='llm'),
    dict(net_memory_summary='bogus'),
    dict(net_topology='none', net_memory_mode='decay'),
    dict(net_dyad_hooks='nope'),
])
def test_channel_configure_rejections(ch_sim, kwargs):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError):
        _configure(b, 'rej', ch_sim, **kwargs)
    assert 'rej' not in b._sessions


def test_channel_configure_accepts_good(ch_sim):
    b = eb.ExperimentBridge()
    out = _configure(b, 'ok', ch_sim, net_channel_beta=1.0, net_reply_model='reach',
                     net_channel_prompt='medium', net_channel_topic_weight=0.3)
    assert out['net_channel_beta'] == 1.0 and out['net_reply_model'] == 'reach'
    # the topology none control arm may keep the ledger
    _configure(b, 'ok2', ch_sim, net_topology='none')
    assert b._sessions['ok2'].dyads is not None and b._sessions['ok2'].network is None


# 11 ------------------------------------------------------------------------------

def test_existing_configs_add_nothing(sim_dir):
    b = eb.ExperimentBridge()
    agents = pd_agents(16)
    b.configure('plain', agents=agents, policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=2, inject_results='none', net_topology='ba', net_seed=1)
    b.configure('off', agents=agents, policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=1, inject_results='none')
    _run(b, 'plain', agents, 2)
    _run(b, 'off', agents, 1)
    for code in ('plain', 'off'):
        for name in ('dyads.json', 'dyads.jsonl', 'memory_shown.jsonl'):
            assert not os.path.exists(os.path.join(sim_dir, 'game', code, name))
        assert b._sessions[code].dyads is None
    net = json.load(open(os.path.join(sim_dir, 'game', 'plain', 'network.json'), encoding='utf-8'))
    assert 'channels' not in net and 'edge_attrs' not in net
    assert 'channel_engagement' not in net['nodes'][0]
    for ev in _events(sim_dir, 'plain', 'network_contacts.jsonl'):
        assert all(set(c) == {'conv_id', 'initiator', 'responder'} for c in ev['conversations'])
        assert all('unanswered' not in r and 'ignored' not in r for r in ev['agents'].values())
    for code in ('plain', 'off'):
        for name in ('llm_answers.jsonl', 'network_chat.jsonl'):
            path = os.path.join(sim_dir, 'game', code, name)
            if os.path.exists(path):
                assert not any('Older conversations' in r['prompt'] or 'did not reply' in r['prompt']
                               for r in _events(sim_dir, code, name))


# 13 ------------------------------------------------------------------------------

def test_medium_phrase_in_prompt(ch_sim):
    phrases = tuple(nc.MEDIUM_PHRASE.values())
    for mode, code in (('medium', 'pm'), ('none', 'pn')):
        b = eb.ExperimentBridge()
        _configure(b, code, ch_sim, net_channel_prompt=mode, num_rounds=2)
        _run(b, code, pd_agents(16), 2)
        rows = _events(ch_sim, code, 'network_chat.jsonl')
        assert rows
        talk = [''.join(re.search(r'You are now talking with (.*?\))(.*?)\. \S', r['prompt'], re.S).groups()) for r in rows]
        if mode == 'medium':
            assert all(t.endswith(phrases) for t in talk)
            assert len({t.rsplit(')', 1)[1] for t in talk}) > 1
        else:
            assert all(t.endswith(')') for t in talk)


# -- duplicate names (#55) ----------------------------------------------------------

def test_duplicate_names_disambiguated(tmp_path):
    root = tmp_path / 'dup'
    root.mkdir()
    names = ['Elena V.', 'Alex R.', 'Elena V.', 'Alex R.', 'Sam T.', 'Zoe K.']
    d = write_channel_sim(root, n=6, names=names)
    out = nc.participant_names(d, range(6))
    assert out[4]['short'] == 'Sam T.'                      # unique: unchanged
    assert out[0]['short'] == 'Elena V. (Dept0)' and out[2]['short'] == 'Elena V. (Dept2)'
    assert out[1]['short'] == 'Alex R. (Dept1)' and out[3]['short'] == 'Alex R. (Dept0)'
    assert out[0]['name'] == 'Elena V.'
    assert len({v['short'] for v in out.values()}) == 6
    # same name, same department: #id as the last resort
    meta = json.load(open(os.path.join(d, 'personas_meta.json')))
    meta['people'][2]['department'] = 'Dept0'
    json.dump(meta, open(os.path.join(d, 'personas_meta.json'), 'w'))
    out = nc.participant_names(d, range(6))
    assert out[0]['short'] == 'Elena V. (Dept0) #0' and out[2]['short'] == 'Elena V. (Dept0) #2'
    assert out[2]['display'].endswith(' #2') and out[4]['short'] == 'Sam T.'
    # a duplicate outside the session's agents does not matter
    assert nc.participant_names(d, [0, 1, 4])[0]['short'] == 'Elena V.'
    # anon and general personas
    assert nc.participant_names(d, [0, 2], identity='anon')[0]['short'] == 'Participant 1'
    g = root / 'gen'
    g.mkdir()
    (g / 'reddit_profiles.json').write_text(json.dumps([
        dict(name='Kim', profession='Nurse'), dict(name='Kim', profession='Chef'), dict(name='Lee', profession='X')]))
    out = nc.participant_names(str(g), range(3))
    assert out[0]['short'] == 'Kim (Nurse)' and out[1]['short'] == 'Kim (Chef)' and out[2]['short'] == 'Lee'


# 12 ------------------------------------------------------------------------------

def _export_csv(state, path, code):
    import csv
    with open(path, 'w', newline='', encoding='utf-8') as f:
        cols = ['session_code', 'participant_label', 'agent_id', 'round_number', 'pair_id', 'partner_agent_id',
                'choice', 'cooperated', 'partner_choice', 'payoff', 'decision_source', 'decision_missing',
                'decision_reason', 'chat_transcript']
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for (r, a), d in sorted(state.decisions.items(), key=lambda x: (x[0][1], x[0][0])):
            w.writerow(dict(session_code=code, participant_label=f"agent_{a}", agent_id=a, round_number=r,
                            pair_id=a // 2 + 1, partner_agent_id=a ^ 1, choice=d.choice,
                            cooperated=int(d.choice == 'A'), partner_choice='A', payoff=10.0,
                            decision_source='llm:test', decision_missing=0, decision_reason='x',
                            chat_transcript=''))


def test_analysis_and_dashboard_with_dyads(tmp_path):
    pytest.importorskip('altair')
    import analyze_network
    import dashboard
    sim = write_channel_sim(tmp_path)
    b = eb.ExperimentBridge()
    _configure(b, 'ana', sim, num_rounds=5, net_reply_model='reach', net_channel_beta=1.0,
               net_memory_mode='decay', net_contact_mean=2.0)
    _run(b, 'ana', pd_agents(16), 5)
    state = b._sessions['ana']
    csv_path = tmp_path / 'pd_debate_custom.csv'
    _export_csv(state, csv_path, 'ana')
    s = analyze_network.summarize(str(csv_path), sim)
    json.dumps(s)
    d = s['dyads']
    assert d['compat_all_dyads']['n'] == 120 and d['compat_edges']['n'] == len(state.network['file']['edges'])
    assert d['unanswered'] > 0 and d['conversations'] > d['unanswered']
    assert len(d['reply_rate_by_compat_tercile']) == 3 and d['beta'] == 1.0 and d['reply_model'] == 'reach'
    assert 0 < d['realised_reply_rate'] < 1 and 0 < d['mean_reach'] < 1
    assert s['memory']['decision']['prompts'] > 0 and 'remembered_exposure' in s['memory']
    # unanswered conversations are no exposure: only answered ones with messages count as conversations
    answered = sum(1 for r in state.net_convs.values() for c in r if c['replied'] and c['messages'])
    assert sum(r['conversations'] for r in s['rounds']) == answered
    analyze_network.show(s)

    def run_dash():
        args = dashboard.argparse.Namespace(otree_csv=str(csv_path), sim_dir=sim, session='ana',
                                            baseline_csv=None, out=str(tmp_path / 'd.html'), max_chars=600, title=None)
        return dashboard.build(args)

    res = run_dash()
    res['chart'].to_dict(validate=True)
    titles = json.dumps(res['spec']['vconcat'])
    assert 'Compatibility of dyads' in titles and 'Memory in decision prompts' in titles
    assert 'SECRET' not in res['html'] and len(res['html'].encode('utf-8')) < 2_000_000
    # without edge_attrs / memory log: the dashboard is the #54 one
    gdir = os.path.join(sim, 'game', 'ana')
    net = json.load(open(os.path.join(gdir, 'network.json'), encoding='utf-8'))
    net.pop('edge_attrs')
    json.dump(net, open(os.path.join(gdir, 'network.json'), 'w'))
    os.remove(os.path.join(gdir, 'memory_shown.jsonl'))
    res = run_dash()
    res['chart'].to_dict(validate=True)
    titles = json.dumps(res['spec']['vconcat'])
    assert 'Compatibility of dyads' not in titles and 'Memory in decision prompts' not in titles
    assert 'Network (' in titles
    s2 = analyze_network.summarize(str(csv_path), sim)
    assert 'dyads' not in s2 and 'memory' not in s2
