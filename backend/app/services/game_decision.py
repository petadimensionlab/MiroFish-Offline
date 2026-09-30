"""
Prompt rendering and response parsing for LLM game decisions.

Internally choices are always A = cooperate, B = defect (the oTree data use
these). What agents see is decided by a Labels mapping, because qwen3:4b
picked the letter A as "the cooperative one" even when A was the defect
option in the payoff table it had just read correctly (NOTES.md #39, #40).
"""

import hashlib
import json
import os
import random
import re
from dataclasses import dataclass
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

INTERNAL = ('A', 'B')  # A = cooperate, B = defect
# Outline shapes: no alphabetical order, no positive / negative valence
SYMBOL_POOL = ('△', '□', '○', '◇')
LABEL_SCHEMES = ('letters', 'symbols')


@dataclass(frozen=True)
class Labels:
    """How the two internal choices are shown to agents.

    shown: internal choice -> label the agent sees
    order: the two shown labels in the order options are listed
    """
    shown: Dict[str, str]
    order: Tuple[str, str]

    def show(self, internal: str) -> str:
        return self.shown.get(internal, internal)

    def internal(self, shown: str) -> Optional[str]:
        for k, v in self.shown.items():
            if v == shown:
                return k
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {'cooperate': self.shown['A'], 'defect': self.shown['B'], 'order': list(self.order)}


def make_labels(session_code: str, scheme: str = 'letters', randomize: bool = False,
                swap: bool = False, seed: int = 0) -> Labels:
    """
    Args:
        scheme: "letters" (A / B) or "symbols" (two shapes from SYMBOL_POOL)
        randomize: pick the mapping and listing order per session, seeded by
            session_code, so label effects cancel out across sessions
        swap: letters only, without randomize: show A/B swapped (fixed)
    """
    if scheme not in LABEL_SCHEMES:
        raise ValueError(f"unknown label scheme: {scheme} (expected one of {LABEL_SCHEMES})")
    digest = hashlib.sha256(f"labels:{seed}:{session_code}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    pair = list(rng.sample(SYMBOL_POOL, 2)) if scheme == 'symbols' else ['A', 'B']
    if randomize:
        rng.shuffle(pair)
        shown = {'A': pair[0], 'B': pair[1]}
        order = tuple(rng.sample(pair, 2))
    else:
        if swap and scheme == 'letters':
            pair = ['B', 'A']
        shown = {'A': pair[0], 'B': pair[1]}
        order = tuple(sorted(pair)) if scheme == 'letters' else (pair[0], pair[1])
    return Labels(shown=shown, order=order)


def _points(payoffs: Dict[str, int], own: str, other: str) -> int:
    if own == 'A':
        return payoffs['R'] if other == 'A' else payoffs['S']
    return payoffs['T'] if other == 'A' else payoffs['P']


def _option_blocks(payoffs: Dict[str, int], labels: Labels) -> List[Dict[str, Any]]:
    """Payoffs restated per option: for each option the agent could pick, what
    each of the other's choices would give both players."""
    blocks = []
    for own_shown in labels.order:
        own = labels.internal(own_shown)
        cases = []
        for other_shown in labels.order:
            other = labels.internal(other_shown)
            cases.append(dict(other=other_shown, own_points=_points(payoffs, own, other),
                              other_points=_points(payoffs, other, own)))
        blocks.append(dict(label=own_shown, cases=cases))
    return blocks


def render_decision_prompt(
    round_number: int,
    num_rounds: int,
    payoffs: Dict[str, int],
    history: List[Dict[str, Any]],
    labels: Labels,
    include_feed: bool = True,
    strict: bool = False,
    no_think: bool = False,
    chat: Optional[List[Dict[str, Any]]] = None,
    network_chat: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Args:
        history: internal choices; shown through `labels`
        no_think: append qwen3's soft switch to skip thinking mode. In thinking
            mode qwen3:4b spends 1000+ tokens per answer (NOTES.md #27).
        chat: the pair's private chat, [{"round_number", "messages": [{"who", "text"}]}]
            with "who" already written from this agent's view (see chat_view)
        network_chat: conversations with other (non-partner) participants,
            [{"round_number", "other_display", "messages": [{"who", "text"}]}]
            (see network_chat.conversation_view; NOTES.md #54)
    """
    return _env.get_template('game_decision.j2').render(
        options=labels.order,
        blocks=_option_blocks(payoffs, labels),
        round_number=round_number,
        num_rounds=num_rounds,
        history=_shown_history(history, labels),
        include_feed=include_feed,
        feed_placeholder=FEED_PLACEHOLDER,
        strict=strict,
        no_think=no_think,
        chat=chat or [],
        network_chat=network_chat or [],
    )


def _shown_history(history: List[Dict[str, Any]], labels: Labels) -> List[Dict[str, Any]]:
    return [{**h, 'own': labels.show(h['own']), 'partner': labels.show(h['partner'])}
            for h in history]


# -- pair chat (cheap talk before each round) ----------------------------------
#
# Debate rounds on the simulated platform never turned to the game: agents
# post about their persona's interests (NOTES.md #34, #44, #46). The pair chat
# is a direct channel between the two partners, prompted with the game itself,
# so what is said is about the game and reaches exactly the person it concerns.

def chat_view(chat: List[Dict[str, Any]], agent_id: int) -> List[Dict[str, Any]]:
    """Stored transcript [{"round_number", "messages": [{"agent_id", "text"}]}]
    -> the same with "who" = "You" / "The other person" for this agent."""
    return [
        dict(round_number=c['round_number'], messages=[
            dict(who='You' if m['agent_id'] == agent_id else 'The other person', text=m['text'])
            for m in c['messages']])
        for c in chat
    ]


def render_chat_prompt(
    round_number: int,
    num_rounds: int,
    payoffs: Dict[str, int],
    history: List[Dict[str, Any]],
    labels: Labels,
    past_chat: List[Dict[str, Any]],
    current: List[Dict[str, Any]],
    no_think: bool = False,
) -> str:
    """
    Args:
        past_chat: earlier rounds' chat, in chat_view form
        current: this round's messages so far, [{"who", "text"}]; empty = speak first
    """
    return _env.get_template('game_chat.j2').render(
        options=labels.order,
        blocks=_option_blocks(payoffs, labels),
        round_number=round_number,
        num_rounds=num_rounds,
        history=_shown_history(history, labels),
        past_chat=past_chat,
        current=current,
        no_think=no_think,
    )


def render_network_chat_prompt(
    game: str,
    round_number: int,
    num_rounds: int,
    rules_ctx: Dict[str, Any],
    history: List[Dict[str, Any]],
    other: Dict[str, str],
    earlier: List[Dict[str, Any]],
    current: List[Dict[str, Any]],
    no_think: bool = False,
) -> str:
    """One message of a one-to-one conversation with a non-partner (NOTES.md #54).

    Args:
        game: "pd" or "pgg"
        rules_ctx: pd: {"options", "blocks"} (from _option_blocks); pgg:
            public_goods._rules(params)
        history: pd in shown labels (see _shown_history); pgg as recorded
        other: {"display", "short"} of the person spoken to
        earlier: the speaker's other conversations, in conversation_view form
        current: this conversation so far, [{"who", "text"}]; empty = speak first
    """
    return _env.get_template('game_network_chat.j2').render(
        **rules_ctx,
        pd=(game == 'pd'),
        round_number=round_number,
        num_rounds=num_rounds,
        history=history,
        other=other,
        earlier=earlier,
        current=current,
        no_think=no_think,
    )


def parse_chat_message(text: Optional[str], max_chars: int = 400) -> Tuple[Optional[str], Optional[str]]:
    """Returns (message or None, error)."""
    if not text:
        return None, 'empty response'
    cleaned = _clean(text)
    message = None
    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('message'), str):
            message = obj['message']
            break
    if message is None:
        match = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)', cleaned, re.DOTALL)
        if match:
            message = match.group(1).replace('\\"', '"')
    if message is None:
        # Plain prose is still a usable message, unless it is an attempt at JSON
        if '{' in cleaned:
            return None, f'unparseable response: {cleaned[:200]!r}'
        message = cleaned
    message = ' '.join(message.split())
    if not message:
        return None, 'empty message'
    if len(message) > max_chars:
        message = message[:max_chars].rstrip() + '...'
    return message, None


_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r'```(?:json)?', re.IGNORECASE)


def _clean(text: str) -> str:
    cleaned = _THINK_RE.sub('', text)
    # An unterminated <think> block (truncated output) leaves nothing usable
    if '<think>' in cleaned.lower():
        cleaned = cleaned.split('</think>')[-1]
    return _FENCE_RE.sub('', cleaned).strip()


def _normalize_choice(value: Any, labels: Labels) -> Optional[str]:
    """Shown label -> internal choice, tolerating 'Option X' and case."""
    raw = str(value or '').strip().strip('"\'')
    raw = re.sub(r'^option\s*', '', raw, flags=re.IGNORECASE).strip()
    for shown in labels.order:
        if raw == shown or raw.upper() == shown.upper():
            return labels.internal(shown)
    return None


def parse_decision(text: Optional[str], labels: Labels) -> Tuple[Optional[str], str, Optional[str]]:
    """Parse an agent's answer.

    Returns:
        (internal choice "A"/"B" or None, reason, error). error is None on success.
    """
    if not text:
        return None, '', 'empty response'
    cleaned = _clean(text)

    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        choice = _normalize_choice(obj.get('choice'), labels)
        if choice is not None:
            return choice, str(obj.get('reason', '')).strip(), None

    # Broken JSON but a readable "choice" field
    alternatives = '|'.join(re.escape(s) for s in labels.order)
    match = re.search(r'"choice"\s*:\s*"?\s*(?:option\s*)?(' + alternatives + r')',
                      cleaned, re.IGNORECASE)
    if match:
        choice = _normalize_choice(match.group(1), labels)
        if choice is not None:
            return choice, '', None
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
    cleaned = _clean(text)
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


# Cells asked in the comprehension check, as (own, other) internal choices:
# the temptation cell and mutual defection.
COMPREHENSION_CELLS = (('B', 'A'), ('B', 'B'))


def render_comprehension_prompt(payoffs: Dict[str, int], labels: Labels,
                                no_think: bool = False) -> Tuple[str, Dict[str, int]]:
    """Payoff-table comprehension check (NOTES.md #39).

    Returns (prompt, expected answers {"q1", "q2"}).
    """
    (o1, t1), (o2, t2) = COMPREHENSION_CELLS
    prompt = _env.get_template('comprehension_check.j2').render(
        options=labels.order,
        blocks=_option_blocks(payoffs, labels),
        q1=dict(own=labels.show(o1), other=labels.show(t1)),
        q2=dict(own=labels.show(o2), other=labels.show(t2)),
        no_think=no_think,
    )
    return prompt, {'q1': _points(payoffs, o1, t1), 'q2': _points(payoffs, o2, t2)}


def parse_comprehension(text: Optional[str]) -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    if not text:
        return None, 'empty response'
    cleaned = _clean(text)
    for match in re.finditer(r'\{.*?\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
            return {'q1': int(obj['q1']), 'q2': int(obj['q2'])}, None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None, f'unparseable response: {cleaned[:200]!r}'
