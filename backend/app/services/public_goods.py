"""
Prompt rendering and response parsing for the linear public goods game.

Standard design (e.g. Fehr & Gaechter 2000): fixed groups, each member gets
an endowment every round and chooses how much to put into a group project;
the project total is multiplied and shared equally. With a marginal per
capita return multiplier / group_size < 1, contributing nothing is the
dominant strategy, and in a finitely repeated game the subgame-perfect
equilibrium is zero in every round; full contribution is the social optimum.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .game_decision import _env, _clean, FEED_PLACEHOLDER

DEFAULT_PGG = dict(endowment=20, multiplier=1.6, group_size=4)


def pgg_points(own: int, total: int, params: Dict[str, Any]) -> float:
    """own contribution, group total (including own) -> round points."""
    return round(params['endowment'] - own + params['multiplier'] * total / params['group_size'], 4)


def _fmt(x: float) -> str:
    return f"{x:g}"


def _rules(params: Dict[str, Any]) -> Dict[str, Any]:
    e, n = params['endowment'], params['group_size']
    return dict(
        endowment=e, multiplier=_fmt(params['multiplier']), group_size=n,
        ex=dict(all_zero=_fmt(pgg_points(0, 0, params)),
                all_full=_fmt(pgg_points(e, e * n, params)),
                free_ride=_fmt(pgg_points(0, e * (n - 1), params)),
                sucker=_fmt(pgg_points(e, e * (n - 1), params))),
    )


def render_pgg_prompt(round_number: int, num_rounds: int, params: Dict[str, Any],
                      history: List[Dict[str, Any]], include_feed: bool = False,
                      strict: bool = False, no_think: bool = False) -> str:
    """
    Args:
        history: [{"round_number", "own", "others": [int], "total", "payoff"}]
    """
    return _env.get_template('game_pgg.j2').render(
        **_rules(params), round_number=round_number, num_rounds=num_rounds, history=history,
        include_feed=include_feed, feed_placeholder=FEED_PLACEHOLDER, strict=strict, no_think=no_think)


_CONTRIB_RE = re.compile(r'"contribution"\s*:\s*"?(-?\d+(?:\.\d+)?)')


def parse_contribution(text: Optional[str], endowment: int) -> Tuple[Optional[int], str, Optional[str]]:
    """Returns (contribution 0..endowment or None, reason, error)."""
    if not text:
        return None, '', 'empty response'
    cleaned = _clean(text)

    def valid(value) -> Optional[int]:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return None
        if x != int(x) or not 0 <= x <= endowment:
            return None
        return int(x)

    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            c = valid(obj.get('contribution'))
            if c is not None:
                return c, str(obj.get('reason', '')).strip(), None
    match = _CONTRIB_RE.search(cleaned)
    if match and valid(match.group(1)) is not None:
        return valid(match.group(1)), '', None
    return None, '', f'unparseable response: {cleaned[:200]!r}'


# Cells asked in the comprehension check, as (own, each other member). Not
# the examples shown in the rules, so the answer cannot be copied.
COMPREHENSION_CELLS = ((20, 0), (10, 10))


def render_pgg_comprehension_prompt(params: Dict[str, Any], no_think: bool = False
                                    ) -> Tuple[str, Dict[str, float]]:
    n = params['group_size']
    (o1, t1), (o2, t2) = COMPREHENSION_CELLS
    prompt = _env.get_template('comprehension_pgg.j2').render(
        **_rules(params), q1=dict(own=o1, others=t1), q2=dict(own=o2, others=t2), no_think=no_think)
    return prompt, {'q1': pgg_points(o1, o1 + t1 * (n - 1), params),
                    'q2': pgg_points(o2, o2 + t2 * (n - 1), params)}


def parse_pgg_comprehension(text: Optional[str]) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    if not text:
        return None, 'empty response'
    cleaned = _clean(text)
    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
            return {'q1': round(float(obj['q1']), 4), 'q2': round(float(obj['q2']), 4)}, None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None, f'unparseable response: {cleaned[:200]!r}'
