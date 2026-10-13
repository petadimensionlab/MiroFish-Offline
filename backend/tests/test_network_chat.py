"""Network chat (NOTES.md #54): graph, contact sampling, wave loop and the
bridge end to end with a fake step-server client. No LLM calls."""

import json
import os
import re
import threading
import time

import numpy as np
import pytest

from app.services import experiment_bridge as eb
from app.services import network_chat as nc
from app.services.simulation_ipc import CommandStatus
from tests._golden import render_golden

HERE = os.path.dirname(__file__)


# -- fake step-server -------------------------------------------------------------

class _Result:
    def __init__(self, result):
        self.status = CommandStatus.COMPLETED
        self.result = result
        self.error = None


class FakeState:
    def __init__(self):
        self.batches = []     # [[(agent_id, prompt)]]
        self.fail = None      # callable(agent_id, prompt) -> True: answer garbage
        self.lock = threading.Lock()


FAKE = FakeState()


class FakeClient:
    def __init__(self, sim_dir):
        pass

    def send_game_interview(self, interviews, **kw):
        ids = [it['agent_id'] for it in interviews]
        assert len(ids) == len(set(ids)), f"duplicate agent in one batch: {ids}"
        with FAKE.lock:
            FAKE.batches.append([(it['agent_id'], it['prompt']) for it in interviews])
        out = []
        for it in interviews:
            prompt = it['prompt']
            if FAKE.fail is not None and FAKE.fail(it['agent_id'], prompt):
                resp = 'I refuse { not json'
            elif '"message"' in prompt:
                resp = json.dumps({"message": f"agent {it['agent_id']} says hello"})
            elif '"contribution"' in prompt:
                resp = json.dumps({"contribution": 10, "reason": "x"})
            else:
                opts = re.search(r'choose (\S+) or (\S+) at the same', prompt).groups()
                resp = json.dumps({"choice": opts[0], "reason": "x"})
            out.append(dict(agent_id=it['agent_id'], response=resp))
        return _Result({'answers': out})

    def send_inject_posts(self, *a, **k):
        return _Result({})

    def send_run_rounds(self, *a, **k):
        return _Result({})


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FAKE.batches, FAKE.fail = [], None
    monkeypatch.setattr(eb, 'SimulationIPCClient', FakeClient)


@pytest.fixture
def sim_dir(tmp_path):
    d = tmp_path / 'sim'
    d.mkdir()
    (d / 'reddit_profiles.json').write_text(json.dumps(
        [dict(user_id=i, name=f"Person{i:02d} Q.", profession='Analyst') for i in range(16)]))
    (d / 'personas_meta.json').write_text(json.dumps(dict(
        agent_order=list(range(16)),
        people=[dict(agent_id=i, name=f"Person{i:02d} Q.", company='Acme', department=f"Dept{i % 3}",
                     seniority='staff') for i in range(16)])))
    return str(d)


def pd_agents(n):
    return [dict(agent_id=i, partner_agent_id=i ^ 1) for i in range(n)]


def pgg_agents(n):
    return [dict(agent_id=i, group_agent_ids=list(range(i // 4 * 4, i // 4 * 4 + 4)), role=1)
            for i in range(n)]


def pd_excl(n):
    return {i: {i ^ 1} for i in range(n)}


def pgg_excl(n):
    return {i: set(range(i // 4 * 4, i // 4 * 4 + 4)) - {i} for i in range(n)}


def _connected(net, n):
    seen, todo = {0}, [0]
    while todo:
        for j in net['neighbors'][todo.pop()]:
            if j not in seen:
                seen.add(j)
                todo.append(j)
    return len(seen) == n


# 1 -------------------------------------------------------------------------------

@pytest.mark.parametrize('n', [8, 16, 48])
@pytest.mark.parametrize('topology', ['er', 'ba', 'ws', 'ring'])
def test_build_network(n, topology):
    cases = [('pd', pd_excl(n), 4)]
    if n >= 16:  # two groups (n=8) only admit bipartite graphs, which er / ba / ws do not give
        cases.append(('pgg', pgg_excl(n), 4))
    for _game, excl, k in cases:
        net = nc.build_network(range(n), excl, topology, k, 0.1, seed=3)
        assert net['stats']['connected'] and _connected(net, n)
        assert not net['excluded_edges_dropped']
        for a, b in net['edges']:
            assert b not in excl[a]
        assert net == nc.build_network(range(n), excl, topology, k, 0.1, seed=3)
    if topology != 'ring':
        other = nc.build_network(range(n), pd_excl(n), topology, 4, 0.1, seed=4)
        assert other['edges'] != nc.build_network(range(n), pd_excl(n), topology, 4, 0.1, seed=3)['edges']


def test_hubs_only_in_ba():
    ba = nc.build_network(range(48), pd_excl(48), 'ba', 4, 0.1, seed=1)
    ring = nc.build_network(range(48), pd_excl(48), 'ring', 4, 0.1, seed=1)
    assert ba['stats']['max_degree'] > ring['stats']['max_degree']
    assert ring['stats']['max_degree'] == 4


# 2 -------------------------------------------------------------------------------

def test_contact_rate_moments():
    mu, r, n = 1.0, 0.5, 20000
    lam = nc.draw_lambdas(range(n), mu, r, seed=5)
    rng = np.random.default_rng(1)
    k = rng.poisson(np.array([lam[i] for i in range(n)]))
    assert abs(k.mean() - mu) / mu < 0.05
    assert abs(k.var() - (mu + mu ** 2 / r)) / (mu + mu ** 2 / r) < 0.05
    lam0 = nc.draw_lambdas(range(n), mu, 0, seed=5)
    assert set(lam0.values()) == {mu}
    k0 = rng.poisson(np.full(n, mu))
    assert abs(k0.var() - mu) / mu < 0.05


def test_lambda_assign_degree():
    net = nc.build_network(range(16), pd_excl(16), 'ba', 4, 0.1, seed=2)
    deg = {a: len(v) for a, v in net['neighbors'].items()}
    lam = nc.draw_lambdas(range(16), 1.0, 0.5, seed=2, assign='degree', degrees=deg)
    top = max(deg, key=lambda a: (deg[a], -a))
    assert lam[top] == max(lam.values())


# 3 -------------------------------------------------------------------------------

def test_sample_contacts():
    net = nc.build_network(range(16), pd_excl(16), 'er', 4, 0.1, seed=1)
    lam = nc.draw_lambdas(range(16), 2.0, 0.5, seed=1)
    for rnd in (1, 2, 3):
        convs, rec = nc.sample_contacts(net['neighbors'], lam, rnd, 1, 3, 4)
        again = nc.sample_contacts(net['neighbors'], lam, rnd, 1, 3, 4)
        assert (convs, rec) == again
        pairs = [frozenset((c['initiator'], c['responder'])) for c in convs]
        assert len(pairs) == len(set(pairs))
        load = {a: 0 for a in range(16)}
        for c in convs:
            i, j = c['initiator'], c['responder']
            assert i != j and j in net['neighbors'][i] and j != (i ^ 1)
            load[i] += 1
            load[j] += 1
        assert max(load.values()) <= 4
        for a, r in rec.items():
            assert r['k_realized'] <= r['k_drawn'] and r['k_realized'] == len(r['initiated'])
    assert nc.sample_contacts(net['neighbors'], lam, 1, 1, 3, 4) != nc.sample_contacts(
        net['neighbors'], lam, 2, 1, 3, 4)


# 4 -------------------------------------------------------------------------------

def test_wave_loop():
    net = nc.build_network(range(16), pd_excl(16), 'ba', 4, 0.1, seed=1)
    lam = nc.draw_lambdas(range(16), 2.0, 0.5, seed=1)
    convs, _ = nc.sample_contacts(net['neighbors'], lam, 1, 1, 3, 4)
    convs = [dict(c, round_number=1, messages=[], aborted=False) for c in convs]
    assert convs
    turns, waves = 2, 0
    while True:
        wave = nc.next_wave(convs, turns)
        if not wave:
            break
        speakers = [s for _, s in wave]
        assert len(speakers) == len(set(speakers))
        for c, s in wave:
            expected = c['initiator'] if len(c['messages']) % 2 == 0 else c['responder']
            assert s == expected
            c['messages'].append(dict(agent_id=s, text='x'))
        waves += 1
        assert waves <= 4 * turns + 2
    assert all(len(c['messages']) == turns for c in convs)


# 5 -------------------------------------------------------------------------------

def _run(b, code, agents, rounds):
    out = {}
    for r in range(1, rounds + 1):
        for a in range(len(agents)):
            out[(r, a)] = b.decide(code, r, a)
        b.round_complete(code, r, {'outcomes': [
            dict(agent_id=a, choice=b._sessions[code].decisions[(r, a)].choice, payoff=10)
            for a in range(len(agents))]})
    time.sleep(0.2)
    return out


def _events(sim_dir, code, name='bridge_log.jsonl'):
    path = os.path.join(sim_dir, 'game', code, name)
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def test_end_to_end_pd(sim_dir):
    b = eb.ExperimentBridge()
    agents = pd_agents(16)
    b.configure('pdnet', agents=agents, policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=3, inject_results='none', net_topology='ba', net_seed=1,
                net_contact_mean=1.5, label_order_per_agent=True)
    out = _run(b, 'pdnet', agents, 3)

    doc = json.load(open(os.path.join(sim_dir, 'game', 'pdnet', 'network.json'), encoding='utf-8'))
    assert doc['version'] == 1 and doc['game'] == 'pd' and doc['topology'] == 'ba'
    assert {'params', 'contact', 'exclude_partners', 'nodes', 'edges', 'excluded_edges_dropped',
            'repair_edges', 'stats', 'layout'} <= set(doc)
    assert len(doc['nodes']) == 16 and len(doc['layout']) == 16 and doc['stats']['m'] == len(doc['edges'])
    assert {'agent_id', 'name', 'display', 'degree', 'lambda', 'excluded'} <= set(doc['nodes'][0])
    assert all(a < b for a, b in doc['edges'])
    assert doc['nodes'][3]['display'] == 'Person03 Q. (Dept0, Acme)'

    chat = _events(sim_dir, 'pdnet', 'network_chat.jsonl')
    assert chat and all(m['message'] for m in chat)
    assert len(_events(sim_dir, 'pdnet', 'network_contacts.jsonl')) == 3
    log = _events(sim_dir, 'pdnet')
    assert [e['event'] for e in log].count('network_phase') == 3
    assert any(e['event'] == 'network_built' for e in log)

    names = {a: doc['nodes'][a]['display'] for a in range(16)}
    shown = 0
    for batch in FAKE.batches:
        for a, prompt in batch:
            partner_name = names[a ^ 1].split(' (')[0]
            assert partner_name not in prompt, (a, partner_name)
            if '"choice"' in prompt and 'Conversations you had with other participants' in prompt:
                shown += 1
                assert any(names[o] in prompt for o in range(16) if o != a)
    assert shown > 0
    assert any(out[(3, a)]['network_chat'] for a in range(16))
    for (r, a), d in out.items():
        assert d['choice'] in ('A', 'B') and isinstance(d['network_chat'], list)
        json.dumps(d['network_chat'])
        for c in d['network_chat']:
            assert {'conv_id', 'other_agent_id', 'initiator', 'messages'} <= set(c)


# 6 -------------------------------------------------------------------------------

def test_end_to_end_pgg(sim_dir):
    b = eb.ExperimentBridge()
    agents = pgg_agents(8)
    b.configure('pggnet', agents=agents, policy='llm', simulation_dir=sim_dir, game='pgg',
                include_feed=False, num_rounds=2, inject_results='none', net_topology='er',
                net_mean_degree=2, net_contact_mean=2.0, net_contact_dispersion=0)
    out = _run(b, 'pggnet', agents, 2)
    doc = json.load(open(os.path.join(sim_dir, 'game', 'pggnet', 'network.json'), encoding='utf-8'))
    for a, c in doc['edges']:
        assert c not in pgg_excl(8)[a]
    assert any(out[(2, a)]['network_chat'] for a in range(8))
    groupish = [p for batch in FAKE.batches for _, p in batch if '"contribution"' in p]
    assert groupish and any('plays with their own group' in p for p in groupish)
    for batch in FAKE.batches:
        for a, p in batch:
            if '"message"' in p:
                assert 'What happened in your group so far:' in p or 'Round 1 of' in p


# 7 -------------------------------------------------------------------------------

def test_failing_message_aborts_only_its_conversation(sim_dir):
    victim = {}

    def fail(agent_id, prompt):
        if '"message"' not in prompt:
            return False
        key = (agent_id, re.search(r'You are now talking with (.*?\))', prompt).group(1))
        victim.setdefault('k', key)
        return key == victim['k']

    FAKE.fail = fail
    b = eb.ExperimentBridge()
    agents = pd_agents(16)
    b.configure('fail', agents=agents, policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=1, inject_results='none', net_topology='er', net_contact_mean=2.0,
                net_contact_dispersion=0)
    b.decide('fail', 1, 0)
    convs = b._sessions['fail'].net_convs[1]
    assert sum(c['aborted'] for c in convs) == 1
    done = [c for c in convs if not c['aborted']]
    assert len(convs) > 1 and all(len(c['messages']) == 2 for c in done)
    aborted = next(c for c in convs if c['aborted'])
    assert aborted['messages'] == [] or aborted['initiator'] != victim['k'][0]
    phase = [e for e in _events(sim_dir, 'fail') if e['event'] == 'network_phase'][0]
    assert phase['aborted'] == 1 and phase['failed'] == 2


# 8 -------------------------------------------------------------------------------

@pytest.mark.parametrize('kwargs,agents', [
    (dict(label_unit='pair'), pd_agents(8)),
    (dict(label_unit='agent'), pd_agents(8)),
    (dict(chat_turns=2), pd_agents(8)),
    (dict(game='trust'), pgg_agents(8)),
    (dict(net_topology='smallworld'), pd_agents(8)),
    (dict(net_turns=0), pd_agents(8)),
])
def test_configure_rejections(sim_dir, kwargs, agents):
    b = eb.ExperimentBridge()
    with pytest.raises(ValueError):
        b.configure('bad', agents=agents, policy='llm', simulation_dir=sim_dir,
                    **{'net_topology': 'ba', **kwargs})
    assert 'bad' not in b._sessions


# 9 -------------------------------------------------------------------------------

def test_network_off_prompts_identical_to_golden():
    with open(os.path.join(HERE, 'golden_prompts.json'), encoding='utf-8') as f:
        golden = json.load(f)
    now = render_golden()
    assert set(now) == set(golden)
    for k in golden:
        assert now[k] == golden[k], k


def test_network_off_bridge_unchanged(sim_dir):
    b = eb.ExperimentBridge()
    b.configure('off', agents=pd_agents(4), policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=1, inject_results='none')
    d = b.decide('off', 1, 0)
    assert 'network_chat' not in d and b._sessions['off'].network is None
    assert not any('Conversations you had' in p for batch in FAKE.batches for _, p in batch)
    assert not os.path.exists(os.path.join(sim_dir, 'game', 'off', 'network.json'))


# 10 ------------------------------------------------------------------------------

def test_label_order_per_agent(sim_dir):
    b = eb.ExperimentBridge()
    b.configure('ord', agents=pd_agents(16), policy='random', label_unit='session',
                label_order_per_agent=True)
    state = b._sessions['ord']
    labs = [b._labels('ord', state.settings, a) for a in range(16)]
    assert len({tuple(sorted(l.shown.items())) for l in labs}) == 1
    assert len({l.order for l in labs}) == 2
    assert all(sorted(l.order) == sorted(labs[0].order) for l in labs)
    assert b._labels('ord', state.settings).order == b._labels('ord', state.settings).order
    summary = b._labels_summary('ord', state)
    assert set(summary) == {str(a) for a in range(16)}
    off = eb.ExperimentBridge()
    off.configure('o2', agents=pd_agents(16), policy='random')
    assert len({off._labels('o2', off._sessions['o2'].settings, a).order for a in range(16)}) == 1


# 11 ------------------------------------------------------------------------------

def test_analyze_network_runs(sim_dir, tmp_path):
    import csv
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts', 'experiment'))
    import analyze_network

    b = eb.ExperimentBridge()
    agents = pd_agents(16)
    b.configure('ana', agents=agents, policy='llm', simulation_dir=sim_dir, include_feed=False,
                num_rounds=3, inject_results='none', net_topology='er', net_contact_mean=1.5,
                label_order_per_agent=True)
    _run(b, 'ana', agents, 3)
    state = b._sessions['ana']
    path = tmp_path / 'pd_debate_custom.csv'
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['session_code', 'agent_id', 'round_number', 'choice', 'cooperated'])
        for (r, a), d in sorted(state.decisions.items()):
            w.writerow(['ana', a, r, d.choice, int(d.choice == 'A')])
    s = analyze_network.summarize(str(path), sim_dir)
    assert s['game'] == 'pd' and len(s['agents']) == 16 and len(s['rounds']) == 3
    assert s['talk_quality']['messages'] > 0 and s['contact_count_check']['n'] == 48
    json.dumps(s)
    analyze_network.show(s)
