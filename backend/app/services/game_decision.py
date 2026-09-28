"""
Prompt rendering and response parsing for LLM game decisions.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prompts')
# Must match scripts/experiment/ipc_protocol.py FEED_PLACEHOLDER
FEED_PLACEHOLDER = "{{FEED}}"

# auto_reload=False: the prompt is fixed for the life of the bridge process.
# With Jinja's default, editing the template mid-run changes the prompt from
# the next round on (NOTES.md #33).
_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    auto_reload=False,
)


def display_label(internal: str, swap_labels: bool) -> str:
    """Internal choices are A = cooperate, B = defect. With swap_labels the
    agent sees them the other way round, to control for label / position
    bias (plan.md §5). The mapping is its own inverse."""
    if not swap_labels:
        return internal
    return {'A': 'B', 'B': 'A'}.get(internal, internal)


def _payoff_rows(payoffs: Dict[str, int], swap_labels: bool) -> List[Dict[str, Any]]:
    c, d = display_label('A', swap_labels), display_label('B', swap_labels)
    rows = [
        (c, c, payoffs['R'], payoffs['R']),
        (c, d, payoffs['S'], payoffs['T']),
        (d, c, payoffs['T'], payoffs['S']),
        (d, d, payoffs['P'], payoffs['P']),
    ]
    rows.sort(key=lambda r: (r[0], r[1]))  # always list A before B
    out = []
    for own, other, own_pts, other_pts in rows:
        text = (f"Both choose {own}" if own == other
                else f"You choose {own}, the other chooses {other}")
        out.append(dict(text=text, own=own_pts, other=other_pts))
    return out


def render_decision_prompt(
    round_number: int,
    num_rounds: int,
    payoffs: Dict[str, int],
    history: List[Dict[str, Any]],
    include_feed: bool = True,
    strict: bool = False,
    no_think: bool = False,
    swap_labels: bool = False,
) -> str:
    """
    Args:
        no_think: append qwen3's soft switch to skip thinking mode. In thinking
            mode qwen3:4b spends 1000+ tokens per answer (NOTES.md #27).
    """
    shown_history = [
        {**h, 'own': display_label(h['own'], swap_labels),
         'partner': display_label(h['partner'], swap_labels)}
        for h in history
    ]
    return _env.get_template('game_decision.j2').render(
        rows=_payoff_rows(payoffs, swap_labels),
        round_number=round_number,
        num_rounds=num_rounds,
        history=shown_history,
        include_feed=include_feed,
        feed_placeholder=FEED_PLACEHOLDER,
        strict=strict,
        no_think=no_think,
    )


_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r'```(?:json)?', re.IGNORECASE)
_CHOICE_RE = re.compile(r'"choice"\s*:\s*"?\s*(?:option\s*)?([AB])\b', re.IGNORECASE)


def parse_decision(text: Optional[str]) -> Tuple[Optional[str], str, Optional[str]]:
    """Parse an agent's answer.

    Returns:
        (choice "A"/"B" or None, reason, error). error is None on success.
    """
    if not text:
        return None, '', 'empty response'
    cleaned = _THINK_RE.sub('', text)
    # An unterminated <think> block (truncated output) leaves nothing usable
    if '<think>' in cleaned.lower():
        cleaned = cleaned.lower().split('</think>')[-1]
    cleaned = _FENCE_RE.sub('', cleaned).strip()

    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        choice = str(obj.get('choice', '')).strip().upper().replace('OPTION', '').strip()
        if choice in ('A', 'B'):
            return choice, str(obj.get('reason', '')).strip(), None

    # Broken JSON but a readable "choice" field
    match = _CHOICE_RE.search(cleaned)
    if match:
        return match.group(1).upper(), '', None
    return None, '', f'unparseable response: {cleaned[:200]!r}'


def wants_no_think(setting: str, model: str) -> bool:
    """setting: "auto" (qwen3 models only), "on" or "off"."""
    if setting == 'on':
        return True
    if setting == 'off':
        return False
    return 'qwen3' in (model or '').lower()


DEFAULT_BELIEF_STATEMENT = (
    "When you deal with the same person again and again, it is better to trust them "
    "than to look out for yourself first."
)


def render_belief_prompt(statement: str, include_feed: bool = True, no_think: bool = False) -> str:
    """7-point Likert item, asked before and after the game (plan.md §5, internal state)."""
    return _env.get_template('belief_survey.j2').render(
        statement=statement,
        include_feed=include_feed,
        feed_placeholder=FEED_PLACEHOLDER,
        no_think=no_think,
    )


_SCORE_RE = re.compile(r'"score"\s*:\s*"?([1-7])\b')


def parse_likert(text: Optional[str]) -> Tuple[Optional[int], str, Optional[str]]:
    """Returns (score 1-7 or None, reason, error)."""
    if not text:
        return None, '', 'empty response'
    cleaned = _FENCE_RE.sub('', _THINK_RE.sub('', text)).strip()
    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        try:
            score = int(obj.get('score'))
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 7:
            return score, str(obj.get('reason', '')).strip(), None
    match = _SCORE_RE.search(cleaned)
    if match:
        return int(match.group(1)), '', None
    return None, '', f'unparseable response: {cleaned[:200]!r}'
