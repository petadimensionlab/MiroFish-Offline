"""Fixed-input renderings of the decision / chat prompts (NOTES #33, #54).

golden_prompts.json was produced by this function BEFORE the network-chat
edits; with the network off the prompts must stay byte-identical.
"""

from app.services.game_decision import (
    make_labels, render_decision_prompt, render_chat_prompt, DEFAULT_BELIEF_STATEMENT)
from app.services.public_goods import render_pgg_prompt, DEFAULT_PGG
from app.services.games import GAMES

PAYOFFS = dict(R=30, T=50, S=0, P=10)
PD_HISTORY = [dict(round_number=1, own='A', partner='B', payoff=0.0),
              dict(round_number=2, own='B', partner='B', payoff=10.0)]
PGG_HISTORY = [dict(round_number=1, own=10, others=[5, 20, 0], total=35, payoff=34.0)]
CHAT = [dict(round_number=2, messages=[dict(who='You', text='Shall we both pick the first one?'),
                                       dict(who='The other person', text='Maybe.')])]


def render_golden():
    labels = make_labels('golden', scheme='symbols', randomize=True)
    out = {}
    for name, kw in (('plain', {}), ('feed', dict(include_feed=True)),
                     ('strict_nothink', dict(strict=True, no_think=True)),
                     ('chat', dict(chat=CHAT)), ('chat_nothink', dict(chat=CHAT, no_think=True))):
        out[f'pd_decision_{name}'] = render_decision_prompt(3, 10, PAYOFFS, PD_HISTORY, labels, **kw)
    out['pd_decision_first_round'] = render_decision_prompt(1, 10, PAYOFFS, [], labels, include_feed=False)
    out['pd_chat_first'] = render_chat_prompt(3, 10, PAYOFFS, PD_HISTORY, labels, past_chat=CHAT, current=[])
    out['pd_chat_reply'] = render_chat_prompt(
        3, 10, PAYOFFS, PD_HISTORY, labels, past_chat=[],
        current=[dict(who='The other person', text='Hi.')], no_think=True)
    for name, kw in (('plain', {}), ('feed', dict(include_feed=True)),
                     ('strict_nothink', dict(strict=True, no_think=True))):
        out[f'pgg_decision_{name}'] = render_pgg_prompt(2, 10, dict(DEFAULT_PGG), PGG_HISTORY, **kw)
    out['pgg_decision_first_round'] = render_pgg_prompt(1, 10, dict(DEFAULT_PGG), [])
    spec = GAMES['pgg']
    out['pgg_game_prompt'] = spec.prompt(spec.params(None), 2, 10, 1, PGG_HISTORY, None, no_think=True)
    return out


# -- network chat (NOTES.md #54, #55) ---------------------------------------------
#
# golden_network_prompts.json / golden_contacts.json were produced BEFORE the
# dyad / memory edits (#55); with net_channels off, net_memory_mode 'window' and
# no unanswered conversation they must stay byte-identical.

NET_OTHER = dict(display='Jo Q. (Finance, Acme)', short='Jo Q.')
NET_EARLIER = [dict(round_number=2, conv_id='r2c0', other='Kim Z.', other_display='Kim Z. (HR, Acme)',
                    messages=[dict(who='You', text='I plan to pick the first one.'),
                              dict(who='Kim Z.', text='Sounds fine to me.')])]


def render_network_golden():
    from app.services.game_decision import render_network_chat_prompt, _option_blocks
    from app.services import public_goods as pg
    labels = make_labels('golden', scheme='symbols', randomize=True)
    hist = [dict(h, own=labels.show(h['own']), partner=labels.show(h['partner'])) for h in PD_HISTORY]
    pd_rules = dict(options=labels.order, blocks=_option_blocks(PAYOFFS, labels))
    pgg_rules = pg._rules(dict(DEFAULT_PGG))
    out = {}
    for game, rules, history in (('pd', pd_rules, hist), ('pgg', pgg_rules, PGG_HISTORY)):
        out[f'{game}_netchat_first'] = render_network_chat_prompt(
            game, 3, 10, rules, history, NET_OTHER, [], [])
        out[f'{game}_netchat_reply'] = render_network_chat_prompt(
            game, 3, 10, rules, history, NET_OTHER, [],
            [dict(who='Jo Q.', text='Hi there.')], no_think=True)
        out[f'{game}_netchat_earlier'] = render_network_chat_prompt(
            game, 3, 10, rules, history, NET_OTHER, NET_EARLIER,
            [dict(who='Jo Q.', text='Hi there.')])
    nchat = [dict(round_number=2, conv_id='r2c0', other='Kim Z.', other_display='Kim Z. (HR, Acme)',
                  messages=NET_EARLIER[0]['messages']),
             dict(round_number=3, conv_id='r3c1', other='Jo Q.', other_display='Jo Q. (Finance, Acme)',
                  messages=[dict(who='Jo Q.', text='Hi there.'), dict(who='You', text='Hello.')])]
    out['pd_decision_network'] = render_decision_prompt(3, 10, PAYOFFS, PD_HISTORY, labels,
                                                        include_feed=False, network_chat=nchat)
    out['pgg_decision_network'] = render_pgg_prompt(2, 10, dict(DEFAULT_PGG), PGG_HISTORY,
                                                    include_feed=False, network_chat=nchat)
    return out


def contacts_golden():
    from app.services import network_chat as nc
    out = {}
    for topo in ('er', 'ba'):
        excl = {i: {i ^ 1} for i in range(16)}
        net = nc.build_network(range(16), excl, topo, 4, 0.1, seed=3)
        lam = nc.draw_lambdas(range(16), 1.5, 0.5, seed=3)
        for seed in (0, 1, 7):
            for r in (1, 2, 3):
                convs, rec = nc.sample_contacts(net['neighbors'], lam, r, seed, 3, 4)
                out[f'{topo}_s{seed}_r{r}'] = dict(convs=convs, rec={str(a): v for a, v in rec.items()})
    return out


# -- dyad ledger run (NOTES.md #57) -------------------------------------------------
#
# golden_dyads_run.json was produced BEFORE the betrayal / reputation edits by
# this function: a 3-round fake-client run with channels, beta 1, reach replies
# and decaying memory (pd_net_ba_ch_mem-like), PD and pgg. With the new
# settings at their defaults every log record and prompt must stay identical.

def dyads_run_golden(root):
    import json
    import os
    import re
    from app.services import experiment_bridge as eb
    from tests.test_channel_dyads import write_channel_sim

    class RecordingClient:
        batches: list = []
        active = None   # sim dir of the run being recorded: threads of other tests may still call in

        def __init__(self, sim_dir):
            self.sim_dir = sim_dir

        def send_game_interview(self, interviews, **kw):
            from app.services.simulation_ipc import CommandStatus
            out = []
            if self.sim_dir == RecordingClient.active:
                RecordingClient.batches.append([(it['agent_id'], it['prompt']) for it in interviews])
            for it in interviews:
                p, a = it['prompt'], it['agent_id']
                if '"message"' in p:
                    resp = json.dumps({"message": f"agent {a} says hello ({len(p) % 7})"})
                elif '"contribution"' in p:
                    r = int(re.search(r'round (\d+) of', p).group(1))
                    resp = json.dumps({"contribution": (a * 3 + r * 5) % 21, "reason": "x"})
                else:
                    r = int(re.search(r'round (\d+) of', p, re.I).group(1))
                    opts = re.search(r'choose (\S+) or (\S+) at the same', p).groups()
                    resp = json.dumps({"choice": opts[(a + r) % 3 == 0], "reason": "x"})
                out.append(dict(agent_id=a, response=resp))

            class R:
                status = CommandStatus.COMPLETED
                result = {'answers': out}
                error = None
            return R()

        def send_inject_posts(self, *a, **k):
            return None

        def send_run_rounds(self, *a, **k):
            return None

    orig = eb.SimulationIPCClient
    eb.SimulationIPCClient = RecordingClient
    result = {}
    try:
        for game in ('pd', 'pgg'):
            RecordingClient.batches = []
            sim = write_channel_sim(root / game)
            RecordingClient.active = sim
            b = eb.ExperimentBridge()
            if game == 'pd':
                agents = [dict(agent_id=i, partner_agent_id=i ^ 1) for i in range(16)]
            else:
                agents = [dict(agent_id=i, group_agent_ids=list(range(i // 4 * 4, i // 4 * 4 + 4)), role=1)
                          for i in range(16)]
            extra = {} if game == 'pd' else dict(game='pgg')
            b.configure('gold', agents=agents, policy='llm', simulation_dir=sim, include_feed=False,
                        num_rounds=3, inject_results='none', net_topology='ba', net_seed=1,
                        net_contact_mean=2.0, net_channels=True, net_channel_beta=1.0,
                        net_reply_model='reach', net_memory_mode='decay', **extra)
            for r in range(1, 4):
                for a in range(16):
                    b.decide('gold', r, a)
                b.round_complete('gold', r, {'outcomes': [
                    dict(agent_id=a, choice=b._sessions['gold'].decisions[(r, a)].choice, payoff=10)
                    for a in range(16)]})
            import time
            time.sleep(0.2)
            gdir = os.path.join(sim, 'game', 'gold')

            def lines(name):
                with open(os.path.join(gdir, name), encoding='utf-8') as f:
                    return [{k: v for k, v in json.loads(x).items() if k != 'ts'} for x in f]
            doc = json.load(open(os.path.join(gdir, 'dyads.json'), encoding='utf-8'))
            result[game] = dict(
                dyads_json=doc, dyads=lines('dyads.jsonl'), memory_shown=lines('memory_shown.jsonl'),
                network_contacts=lines('network_contacts.jsonl'),
                prompts=[[[a, p] for a, p in batch] for batch in RecordingClient.batches])
    finally:
        eb.SimulationIPCClient = orig
    return json.loads(json.dumps(result))
