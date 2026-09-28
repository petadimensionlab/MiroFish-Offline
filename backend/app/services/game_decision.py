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

_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def render_decision_prompt(
    round_number: int,
    num_rounds: int,
    payoffs: Dict[str, int],
    history: List[Dict[str, Any]],
    include_feed: bool = True,
    strict: bool = False,
    no_think: bool = False,
) -> str:
    """
    Args:
        no_think: append qwen3's soft switch to skip thinking mode. In thinking
            mode qwen3:4b spends 1000+ tokens per answer (NOTES.md #27).
    """
    return _env.get_template('game_decision.j2').render(
        m=payoffs,
        round_number=round_number,
        num_rounds=num_rounds,
        history=history,
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
