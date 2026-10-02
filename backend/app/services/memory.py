"""
Communication memory with forgetting (NOTES.md #55).

In window mode (#54) an agent sees its conversations verbatim for the last
net_memory_rounds rounds and nothing older: memory is a cliff. Here every
past conversation stays available, at a level of detail that falls with its
age, as a person's memory of a conversation does.

A conversation's weight is w = 2^(-delta / (h * s)): delta = rounds since it
took place, h = net_memory_half_life, s = its salience (1.0; hooks of the
dyad layer may raise it for what mattered). The weight picks the tier:

    w >= 0.5    verbatim   what was said, as in window mode (delta 0-2 at h = 2)
    w >= 0.25   excerpt    the first sentence of each message
    w >= 0.1    gist       one line per conversation, what each side mentioned
    w <  0.1    aggregate  one line per person over all their items in this tier

Nothing is dropped by age, only by the character budget: while the block is
over budget the lowest-weight item moves down one tier, then aggregate lines
go, oldest last conversation first. This round's conversations are never
demoted. Everything is deterministic (no random forgetting). What is said is
never interpreted: gists and aggregates only record which option symbols
(PD) or amounts (pgg) each side named, in neutral words ("mentioned").

Revealed choices (NOTES.md #57): a MemoryItem of kind 'note' is a fact the
agent was told after a round, what the person it talked with chose (and, when
the reveal is 'pair', what that person's partner or group chose). Notes are
no conversation: they stay out of the detail, gist and conversation aggregate
lines and have their own section, rendered last and only if there are notes:
one line per note down to the gist tier, one line per person (counts of what
they chose) below it. They age and demote like conversations (their salience
is the hook's), and their aggregate lines are dropped under budget pressure
with the key ('note', person), oldest first.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .dyads import DyadLedger, MemoryItem

MEMORY_TIERS = (('verbatim', 0.5), ('excerpt', 0.25), ('gist', 0.1), ('aggregate', 0.0))
MEMORY_MODES = ('window', 'decay')
MEMORY_SUMMARIES = ('extract', 'llm')
EXCERPT_CHARS = 120
QUOTE_CHARS = 80
_EPS = 1e-9
_SENTENCE_END = re.compile(r'[.!?](?=\s|$)')
_CONV_INDEX = re.compile(r'c(\d+)$')
_NUMBER = re.compile(r'(?<![\d.])\d+(?![\d.]*\d)')


def weight(delta: float, half_life: float, salience: float = 1.0) -> float:
    """w = 2^(-delta / (h * s)); 1 at delta 0, 0.5 at delta = h * s."""
    return 2.0 ** (-delta / (half_life * salience))


def tier_index(w: float) -> int:
    for k, (_name, floor) in enumerate(MEMORY_TIERS):
        if w >= floor - _EPS:
            return k
    return len(MEMORY_TIERS) - 1


def salience(item: MemoryItem, dyad: Any = None) -> float:
    """Salience of an item. item.salience for now; the place where a later
    hook (betrayal, a broken promise) makes a conversation hard to forget."""
    return item.salience


def extract_mentions(text: str, game: str, ctx: Dict[str, Any]) -> List[str]:
    """What a message names, in order of first mention: PD the option symbols
    (ctx['options']), pgg the whole numbers 0..ctx['endowment']."""
    if game == 'pd':
        found = sorted((text.find(o), o) for o in ctx.get('options', ()) if o in text)
        return [o for _, o in found]
    if game == 'pgg':
        out: List[str] = []
        for m in _NUMBER.finditer(text):
            if int(m.group(0)) <= ctx.get('endowment', 0) and m.group(0) not in out:
                out.append(str(int(m.group(0))))
        return out
    return []


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def first_sentence(text: str, limit: int = EXCERPT_CHARS) -> str:
    """First sentence of a message, at most `limit` chars; '…' when anything
    was left out."""
    m = _SENTENCE_END.search(text)
    head = text[:m.end()] if m else text
    cut = len(head) < len(text.rstrip())
    if len(head) > limit:
        return head[:limit - 1].rstrip() + '…'
    return head + ' …' if cut else head


def _order(item: MemoryItem) -> Tuple[int, int, str]:
    m = _CONV_INDEX.search(item.conv_id or '')
    return (item.round_number, int(m.group(1)) if m else 0, item.conv_id or '')


def _times(n: int) -> str:
    return {1: 'once', 2: 'twice'}.get(n, f"{n} times")


def _syms(xs: List[str]) -> str:
    return xs[0] if len(xs) == 1 else ', '.join(xs[:-1]) + ' and ' + xs[-1]


def _rounds(rs: List[int]) -> str:
    lo, hi = min(rs), max(rs)
    return f"round {lo}" if lo == hi else f"rounds {lo}–{hi}"


class _Entry:
    def __init__(self, item: MemoryItem, other: int, delta: int, w: float, sal: float):
        self.item, self.other, self.delta, self.w, self.sal = item, other, delta, w, sal
        self.tier = tier_index(w)
        self.natural = self.tier
        self.chars = 0
        self.texts: List[str] = []


def build_memory(owner: int, dyads: DyadLedger, round_number: int, half_life: float, budget: int,
                 names: Dict[int, Dict[str, str]], game_ctx: Optional[Dict[str, Any]] = None,
                 exclude_conv: Optional[str] = None, use_display: bool = True,
                 live_items: Optional[Dict[int, List[MemoryItem]]] = None
                 ) -> Tuple[str, Dict[str, Any]]:
    """The memory block for owner's prompt at round_number.

    Args:
        names: {agent_id: {short, display}}
        exclude_conv: the conversation being written (chat prompts)
        use_display: verbatim / excerpt headers name the other by display
            (decision prompts) or short (chat prompts), as in window mode
        live_items: {other: [MemoryItem]} of this round's conversations not
            yet in the ledger (the network phase records it at its end)

    Returns:
        (block text, '' when there is nothing to show; shown = log record
        {round_number, agent_id, chars, budget, over_budget_demotions, items,
        aggregates})
    """
    def nm(a: int) -> Dict[str, str]:
        return names.get(a) or dict(short=f"Participant {a + 1}", display=f"Participant {a + 1}")

    entries: List[_Entry] = []
    for other in dyads.ids:
        if other == owner:
            continue
        d = dyads.get(owner, other)
        items = list(d.memory.get(owner, []))
        have = {i.conv_id for i in items}
        items += [i for i in (live_items or {}).get(other, []) if i.conv_id not in have]
        for item in items:
            if item.round_number > round_number or (exclude_conv and item.conv_id == exclude_conv):
                continue
            delta = round_number - item.round_number
            sal = salience(item, d)
            entries.append(_Entry(item, other, delta, weight(delta, half_life, sal), sal))
    entries.sort(key=lambda e: _order(e.item))

    def detail(e: _Entry) -> str:
        """Verbatim / excerpt item as a conversation block."""
        label = nm(e.other)['display' if use_display else 'short']
        this = ' (this round)' if e.delta == 0 else ''
        lines = [f"Before round {e.item.round_number}{this}, with {label}:"]
        e.texts = []
        for m in e.item.messages:
            who = 'You' if m['agent_id'] == owner else nm(e.other)['short']
            text = m['text'] if e.tier == 0 else first_sentence(m['text'])
            e.texts.append(text)
            lines.append(f"- {who}: {text}")
        if not e.item.replied:
            lines.append(f"- ({nm(e.other)['short']} did not reply)")
        return '\n'.join(lines) + '\n'

    def gist(e: _Entry) -> str:
        short = nm(e.other)['short']
        it = e.item
        if it.summary:
            return f"- Round {it.round_number}, with {short}: {it.summary}\n"
        if not it.replied:
            return f"- Round {it.round_number}, you wrote to {short}; no reply.\n"
        starter = 'you started' if it.started_by == owner else f"{short} started"
        other_part = f"{short} mentioned {_syms(it.mentions_other)}" if it.mentions_other \
            else f"{short} mentioned nothing specific"
        own_part = f"you mentioned {_syms(it.mentions_own)}" if it.mentions_own \
            else "you mentioned nothing specific"
        return f"- Round {it.round_number}, with {short} ({starter}): {other_part}; {own_part}.\n"

    def note_line(e: _Entry) -> str:
        """A note down to the gist tier: what one person was shown to have chosen."""
        short = nm(e.other)['short']
        x = e.item.ext
        if 'amount' in x:
            line = f"- After round {e.item.round_number}, {short} put in {x['amount']}"
            if x.get('group_amounts'):
                line += f"; the others in {short}'s group put in {_syms([str(a) for a in x['group_amounts']])}"
            return line + ".\n"
        line = f"- After round {e.item.round_number}, {short} chose {x['other_choice_shown']}"
        if x.get('other_partner_choice_shown'):
            line += f"; {short}'s partner chose {x['other_partner_choice_shown']}"
        return line + ".\n"

    def note_aggregates(pool: List[_Entry]) -> List[Tuple[int, Any, str, Dict[str, Any]]]:
        """[(last_round, ('note', other), line, log)] one per person, over their notes below the gist tier."""
        by: Dict[int, List[_Entry]] = {}
        for e in pool:
            if e.item.kind == 'note':
                by.setdefault(e.other, []).append(e)
        out = []
        for other, es in by.items():
            short = nm(other)['short']
            es = sorted(es, key=lambda e: _order(e.item))
            rounds = [e.item.round_number for e in es]
            if 'amount' in es[0].item.ext:
                what = "put in " + _syms([str(e.item.ext['amount']) for e in es])
            else:
                counts: Dict[str, int] = {}
                for e in es:
                    sym = e.item.ext['other_choice_shown']
                    counts[sym] = counts.get(sym, 0) + 1
                what = "chose " + ', '.join(f"{sym} {_times(c)}" for sym, c in sorted(
                    counts.items(), key=lambda x: (-x[1], x[0])))
            out.append((max(rounds), ('note', other), f"- {short}, after {_rounds(rounds)}: {what}.\n",
                        dict(other=short, n_convs=len(es), rounds=[min(rounds), max(rounds)], dropped=False,
                             kind='note')))
        return out

    def aggregates(pool: List[_Entry]) -> List[Tuple[int, Any, str, Dict[str, Any]]]:
        """[(last_round, key, line, log)] one per person; key is the person (conversations) or
        ('note', person) (revealed choices)."""
        by: Dict[int, List[_Entry]] = {}
        for e in pool:
            if e.item.kind != 'note':
                by.setdefault(e.other, []).append(e)
        out = []
        for other, es in by.items():
            short = nm(other)['short']
            rounds = [e.item.round_number for e in es]
            unans = sum(1 for e in es if not e.item.replied)
            n = len(es)
            head = f"{n} conversation{'s' if n != 1 else ''} ({_rounds(rounds)}" \
                   + (f", {unans} unanswered" if unans else '') + ')'
            counts: Dict[str, int] = {}
            for e in es:
                for s in e.item.mentions_other:
                    counts[s] = counts.get(s, 0) + 1
            parts = [head]
            if counts:
                parts.append("mentioned " + ', '.join(
                    f"{s} {_times(c)}" for s, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))))
            said = [m['text'] for e in es for m in e.item.messages if m['agent_id'] != owner]
            if said:
                parts.append(f'last said: "{_clip(said[-1], QUOTE_CHARS)}"')
            out.append((max(rounds), other, f"- {short}: " + '; '.join(parts) + "\n",
                        dict(other=short, n_convs=n, rounds=[min(rounds), max(rounds)], dropped=False)))
        return out

    def order_key(a: Tuple[int, Any, str, Dict[str, Any]]) -> Tuple[int, int, int]:
        return (a[0], 0, a[1]) if isinstance(a[1], int) else (a[0], 1, a[1][1])

    def render(dropped: set) -> Tuple[str, List[Tuple[int, Any, str, Dict[str, Any]]]]:
        parts = []
        for e in entries:
            if e.tier <= 1 and e.item.kind != 'note':
                text = detail(e)
                e.chars = len(text)
                parts.append(text)
        older = []
        for e in entries:
            if e.tier == 2 and e.item.kind != 'note':
                line = gist(e)
                e.chars = len(line)
                older.append(line)
        told = []
        for e in entries:
            if e.tier <= 2 and e.item.kind == 'note':
                line = note_line(e)
                e.chars = len(line)
                told.append(line)
        for e in entries:
            if e.tier >= 3:
                e.chars = 0
        aggs = sorted(aggregates([e for e in entries if e.tier >= 3])
                      + note_aggregates([e for e in entries if e.tier >= 3]), key=order_key)
        kept = [a for a in aggs if a[1] not in dropped]
        for a in aggs:
            a[3]['dropped'] = a[1] in dropped
        text = ''.join(parts)
        if older:
            text += 'Older conversations, as you remember them:\n' + ''.join(older)
        kept_conv = [a for a in kept if isinstance(a[1], int)]
        if kept_conv:
            text += 'What you remember about people you talked with earlier:\n' + ''.join(a[2] for a in kept_conv)
        kept_notes = [a[2] for a in kept if not isinstance(a[1], int)]
        if told or kept_notes:
            text += 'What you were told after earlier rounds:\n' + ''.join(told) + ''.join(kept_notes)
        return text, aggs

    dropped: set = set()
    demotions = 0
    text, aggs = render(dropped)
    while len(text) > budget:
        movable = [e for e in entries if e.delta > 0 and e.tier < len(MEMORY_TIERS) - 1]
        if movable:
            min(movable, key=lambda e: (e.w, e.item.round_number, e.item.conv_id or '')).tier += 1
            demotions += 1
        else:
            live = [a for a in aggs if a[1] not in dropped]
            if not live:
                break
            dropped.add(live[0][1])  # sorted: oldest last conversation first
        text, aggs = render(dropped)

    shown = dict(
        round_number=round_number, agent_id=owner, chars=len(text), budget=budget,
        over_budget_demotions=demotions,
        items=[dict(conv_id=e.item.conv_id, other=nm(e.other)['short'], round=e.item.round_number,
                    delta=e.delta, weight=round(e.w, 4), salience=e.sal,
                    tier=MEMORY_TIERS[e.tier][0], chars=e.chars,
                    mentions_other=list(e.item.mentions_other),
                    **({'kind': 'note'} if e.item.kind == 'note' else {}),
                    **({'texts': e.texts} if e.tier <= 1 and e.item.kind != 'note' else {}))
               for e in entries],
        aggregates=[a[3] for a in aggs])
    return text, shown
