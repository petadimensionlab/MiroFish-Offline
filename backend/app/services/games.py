"""
Game registry for the experiment bridge (every game except the PD, which
keeps its own path in experiment_bridge.py for labels and the pair chat).

A game defines, for its parameters p:
  prompt(...)       the decision prompt for one agent in one round / stage
  parse(...)        answer -> decision value (a string) or an error
  history(...)      what the agent has seen so far, from recorded outcomes
  stage_info(...)   sequential games: the first mover's decision, for the second
  comprehension(...) two payoff questions, expected answers

Decision values are strings (oTree fields hold the typed value). Groups:
state.groups[agent] = all members; state.roles[agent] = 1 (first mover /
simultaneous) or 2 (second mover).

Games (defaults):
  pgg        linear public goods, groups of 4, endowment 20, multiplier 1.6
  beauty     p-beauty contest, groups of 4, guess 0..100, p = 2/3, prize 20
  trust      investment game, pairs, both get 10, sent amount tripled
  ultimatum  pairs, split 20, responder accepts or rejects
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .game_decision import _env, _clean, FEED_PLACEHOLDER
from . import public_goods as pg


def _fmt(x) -> str:
    return f"{float(x):g}"


def _json_objects(text: str):
    for match in re.finditer(r'\{.*?\}', text, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _int_in(value, lo: int, hi: int) -> Optional[int]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != int(x) or not lo <= x <= hi:
        return None
    return int(x)


def parse_int_field(text: Optional[str], field: str, lo: int, hi: int) -> Tuple[Optional[str], str, Optional[str]]:
    if not text:
        return None, '', 'empty response'
    cleaned = _clean(text)
    for obj in _json_objects(cleaned):
        v = _int_in(obj.get(field), lo, hi)
        if v is not None:
            return str(v), str(obj.get('reason', '')).strip(), None
    m = re.search(r'"%s"\s*:\s*"?(-?\d+(?:\.\d+)?)' % re.escape(field), cleaned)
    if m and _int_in(m.group(1), lo, hi) is not None:
        return str(_int_in(m.group(1), lo, hi)), '', None
    return None, '', f'unparseable response: {cleaned[:200]!r}'


def parse_comprehension(text: Optional[str]) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    return pg.parse_pgg_comprehension(text)


class Game:
    name = ''
    sequential = False
    defaults: Dict[str, Any] = {}
    template = ''
    comprehension_template = ''

    def params(self, given: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {**self.defaults, **(given or {})}

    def rules(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return dict(p)

    def render(self, template: str, p: Dict[str, Any], **kw) -> str:
        return _env.get_template(template).render(**self.rules(p), feed_placeholder=FEED_PLACEHOLDER, **kw)

    def prompt(self, p, round_number, num_rounds, role, history, stage_info,
               include_feed=False, strict=False, no_think=False, network_chat=None,
               memory=None, reveal=None) -> str:
        return self.render(self.template, p, round_number=round_number, num_rounds=num_rounds, role=role,
                           history=history, stage=stage_info, include_feed=include_feed,
                           strict=strict, no_think=no_think)

    def auto_decision(self, p, role, stage_info) -> Optional[str]:
        """A decision that needs no LLM call (e.g. nothing to return), else None."""
        return None

    def stage_info(self, p, stage_outcomes: Dict[int, Dict[str, Any]], agent_id: int,
                   members: List[int]) -> Optional[Dict[str, Any]]:
        return None

    def comprehension(self, p, role, no_think=False) -> Tuple[str, Dict[str, float]]:
        raise NotImplementedError

    def result_post(self, p, round_number, outcome) -> str:
        return (f"Decision task, round {round_number}: I got {_fmt(outcome.get('payoff') or 0)} points.")


# -- public goods -----------------------------------------------------------

class PublicGoods(Game):
    name = 'pgg'
    defaults = dict(pg.DEFAULT_PGG)

    def prompt(self, p, round_number, num_rounds, role, history, stage_info,
               include_feed=False, strict=False, no_think=False, network_chat=None,
               memory=None, reveal=None):
        return pg.render_pgg_prompt(round_number, num_rounds, p, history, include_feed=include_feed,
                                    strict=strict, no_think=no_think, network_chat=network_chat,
                                    memory=memory, reveal=reveal)

    def parse(self, text, p, role, stage_info):
        return parse_int_field(text, 'contribution', 0, p['endowment'])

    def default(self, p, role, stage_info):
        return str(p['endowment'] // 2)

    def history(self, p, outcomes, agent_id, members, before_round):
        out = []
        for r in range(1, before_round):
            got = outcomes.get(r, {})
            if any(m not in got for m in members):
                continue
            c = {m: int(float(got[m]['choice'])) for m in members}
            out.append(dict(round_number=r, own=c[agent_id], others=[c[m] for m in members if m != agent_id],
                            total=sum(c.values()), payoff=float(got[agent_id].get('payoff') or 0)))
        return out

    def comprehension(self, p, role, no_think=False):
        return pg.render_pgg_comprehension_prompt(p, no_think=no_think)

    def result_post(self, p, round_number, o):
        return (f"Decision task, round {round_number}: I put {int(float(o['choice']))} of my "
                f"{p['endowment']} points into the group project, my group put in "
                f"{o.get('group_total', '?')} in total, and I got {_fmt(o.get('payoff') or 0)} points.")


# -- p-beauty contest -------------------------------------------------------

def beauty_points(guesses: Dict[int, int], p) -> Tuple[float, Dict[int, float]]:
    """-> (target, points per member). The prize is split among the closest."""
    target = p['p'] * sum(guesses.values()) / len(guesses)
    dist = {a: abs(g - target) for a, g in guesses.items()}
    best = min(dist.values())
    winners = [a for a, d in dist.items() if abs(d - best) < 1e-9]
    return round(target, 4), {a: (p['prize'] / len(winners) if a in winners else 0.0) for a in guesses}


class BeautyContest(Game):
    name = 'beauty'
    defaults = dict(p=2 / 3, p_text='two thirds', max_guess=100, prize=20, group_size=4)
    template = 'game_beauty.j2'
    comprehension_template = 'comprehension_beauty.j2'

    def parse(self, text, p, role, stage_info):
        return parse_int_field(text, 'guess', 0, p['max_guess'])

    def default(self, p, role, stage_info):
        return str(p['max_guess'] // 2)

    def history(self, p, outcomes, agent_id, members, before_round):
        out = []
        for r in range(1, before_round):
            got = outcomes.get(r, {})
            if any(m not in got for m in members):
                continue
            g = {m: int(float(got[m]['choice'])) for m in members}
            target, pts = beauty_points(g, p)
            out.append(dict(round_number=r, own=g[agent_id], others=[g[m] for m in members if m != agent_id],
                            mean=_fmt(round(sum(g.values()) / len(g), 2)), target=_fmt(round(target, 2)),
                            payoff=_fmt(pts[agent_id])))
        return out

    def comprehension(self, p, role, no_think=False):
        # q1: guesses 20, 40, 60, 80 -> mean 50, target 33.3, 40 is closest -> the 40-guesser wins
        # q2: everyone guesses 30 -> all tie -> prize split
        g1 = {0: 40, 1: 20, 2: 60, 3: 80}
        _, pts1 = beauty_points(g1, p)
        g2 = {a: 30 for a in range(p['group_size'])}
        _, pts2 = beauty_points(g2, p)
        prompt = self.render(self.comprehension_template, p, no_think=no_think,
                             q1=dict(own=40, others='20, 60 and 80'), q2=dict(own=30, others='30'))
        return prompt, {'q1': round(pts1[0], 4), 'q2': round(pts2[0], 4)}

    def rules(self, p):
        return {**p, 'prize': _fmt(p['prize'])}

    def result_post(self, p, round_number, o):
        return (f"Decision task, round {round_number}: I guessed {o['choice']}, "
                f"and I got {_fmt(o.get('payoff') or 0)} points.")


# -- trust (investment) game ------------------------------------------------

class TrustGame(Game):
    name = 'trust'
    sequential = True
    defaults = dict(endowment=10, multiplier=3)
    template = 'game_trust.j2'
    comprehension_template = 'comprehension_trust.j2'

    def parse(self, text, p, role, stage_info):
        if role == 1:
            return parse_int_field(text, 'send', 0, p['endowment'])
        return parse_int_field(text, 'return', 0, p['multiplier'] * int(stage_info['sent']))

    def default(self, p, role, stage_info):
        if role == 1:
            return str(p['endowment'] // 2)
        return str(p['multiplier'] * int(stage_info['sent']) // 2)

    def auto_decision(self, p, role, stage_info):
        if role == 2 and int(stage_info['sent']) == 0:
            return '0'  # nothing arrived, nothing to return
        return None

    def stage_info(self, p, stage_outcomes, agent_id, members):
        sender = next(m for m in members if m != agent_id)
        o = stage_outcomes.get(sender)
        if o is None:
            return None
        sent = int(float(o['choice']))
        return dict(sent=sent, received=sent * p['multiplier'])

    def history(self, p, outcomes, agent_id, members, before_round):
        out = []
        for r in range(1, before_round):
            got = outcomes.get(r, {})
            if any(m not in got for m in members):
                continue
            o = got[agent_id]
            out.append(dict(round_number=r, sent=int(o['sent']), received=int(o['sent']) * p['multiplier'],
                            returned=int(o['returned']), payoff=_fmt(o.get('payoff') or 0)))
        return out

    def comprehension(self, p, role, no_think=False):
        e, k = p['endowment'], p['multiplier']
        # q1: A sends 6, B returns 9 -> A gets e-6+9, B gets e+18-9
        # q2: A sends 10, B returns 0 -> A gets 0, B gets e+30
        prompt = self.render(self.comprehension_template, p, no_think=no_think, role=role)
        if role == 1:
            return prompt, {'q1': float(e - 6 + 9), 'q2': float(e - 10 + 0)}
        return prompt, {'q1': float(e + 6 * k - 9), 'q2': float(e + 10 * k - 0)}

    def result_post(self, p, round_number, o):
        return f"Decision task, round {round_number}: I got {_fmt(o.get('payoff') or 0)} points."


# -- ultimatum game ---------------------------------------------------------

class Ultimatum(Game):
    name = 'ultimatum'
    sequential = True
    defaults = dict(pie=20)
    template = 'game_ultimatum.j2'
    comprehension_template = 'comprehension_ultimatum.j2'

    def parse(self, text, p, role, stage_info):
        if role == 1:
            return parse_int_field(text, 'offer', 0, p['pie'])
        if not text:
            return None, '', 'empty response'
        cleaned = _clean(text)
        for obj in _json_objects(cleaned):
            v = str(obj.get('decision', '')).strip().lower()
            if v in ('accept', 'reject'):
                return v, str(obj.get('reason', '')).strip(), None
        m = re.search(r'"decision"\s*:\s*"?(accept|reject)', cleaned, re.IGNORECASE)
        if m:
            return m.group(1).lower(), '', None
        return None, '', f'unparseable response: {cleaned[:200]!r}'

    def default(self, p, role, stage_info):
        return str(p['pie'] // 2) if role == 1 else 'accept'

    def stage_info(self, p, stage_outcomes, agent_id, members):
        proposer = next(m for m in members if m != agent_id)
        o = stage_outcomes.get(proposer)
        if o is None:
            return None
        offer = int(float(o['choice']))
        return dict(offer=offer, keep=p['pie'] - offer)

    def history(self, p, outcomes, agent_id, members, before_round):
        out = []
        for r in range(1, before_round):
            got = outcomes.get(r, {})
            if any(m not in got for m in members):
                continue
            o = got[agent_id]
            out.append(dict(round_number=r, offer=int(o['offer']), accepted=bool(o['accepted']),
                            payoff=_fmt(o.get('payoff') or 0)))
        return out

    def comprehension(self, p, role, no_think=False):
        pie = p['pie']
        # q1: offer 5, accepted -> proposer pie-5, responder 5; q2: offer 5, rejected -> 0
        prompt = self.render(self.comprehension_template, p, no_think=no_think, role=role)
        if role == 1:
            return prompt, {'q1': float(pie - 5), 'q2': 0.0}
        return prompt, {'q1': 5.0, 'q2': 0.0}


GAMES: Dict[str, Game] = {g.name: g for g in (PublicGoods(), BeautyContest(), TrustGame(), Ultimatum())}
