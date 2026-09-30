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
