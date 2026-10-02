"""
Dyad state layer (NOTES.md #55).

Every pair of participants, not only the edges of the social graph and not
only the game partners, is a Dyad: fixed attributes from the channel
compatibility (network_chat.compatibility), running counters of what
happened between the two, what each remembers of the other (MemoryItem, used
by memory.py) and an `ext` dict that later elements of the study hang their
state on (trust, betrayal, reputation; none exist yet).

The layer is pure bookkeeping. The bridge calls it under its lock:
record_network_round at the end of a round's network phase,
record_pair_chat per pair in the pair-chat phase, close_round once per new
round_complete. close_round runs the registered hooks, DYAD_HOOKS, selected
by net_dyad_hooks. A hook gets (ledger, round_number, outcomes, context) and
may change dyad.ext, item.salience, add kind='note' memory items, and must
call ledger.touch(a, b) for a dyad it changed. The registry is empty on
purpose: betrayal and reputation are for later.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

Pair = Tuple[int, int]
# hook(ledger, round_number, outcomes {agent_id: outcome}, context) -> None
DyadHook = Callable[['DyadLedger', int, Dict[int, Dict[str, Any]], Dict[str, Any]], None]
DYAD_HOOKS: Dict[str, DyadHook] = {}

MEMORY_KINDS = ('conv', 'pair_chat', 'note')


def _pair(i: int, j: int) -> Pair:
    return (i, j) if i < j else (j, i)


@dataclass
class MemoryItem:
    """One communication event as its owner remembers it (memory.py renders it).

    mentions_other / mentions_own: option symbols (PD) or amounts (pgg) the
    other / the owner named in it (memory.extract_mentions).
    """
    kind: str
    round_number: int
    conv_id: Optional[str]
    started_by: Optional[int]
    replied: bool
    messages: List[Dict[str, Any]]
    mentions_other: List[str] = field(default_factory=list)
    mentions_own: List[str] = field(default_factory=list)
    salience: float = 1.0
    summary: Optional[str] = None
    ext: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Dyad:
    a: int
    b: int
    edge: bool = False
    excluded: bool = False           # partner / group member: where betrayal attaches
    compat: Optional[float] = None
    topic: Optional[float] = None
    media: Optional[float] = None
    z: Optional[float] = None
    reach: Dict[int, float] = field(default_factory=dict)   # {a: R_a->b, b: R_b->a}
    shared: List[str] = field(default_factory=list)
    attempts: Dict[int, int] = field(default_factory=dict)  # conversations started by a / b
    answered: int = 0
    unanswered: int = 0
    messages: int = 0
    pair_chats: int = 0
    pair_chat_skips: int = 0
    rounds: List[int] = field(default_factory=list)
    last_round: Optional[int] = None
    # what each owner remembers about the other: owner -> items
    memory: Dict[int, List[MemoryItem]] = field(default_factory=dict)
    ext: Dict[str, Any] = field(default_factory=dict)


def memory_items_from_convs(owner: int, convs: Iterable[Dict[str, Any]],
                            mentions: Optional[Callable[[str], List[str]]] = None
                            ) -> Dict[int, List[MemoryItem]]:
    """owner's view of conversations {round_number, conv_id, initiator,
    responder, messages, replied?}: {other_agent_id: [MemoryItem]}. Empty
    conversations and the responder's side of an unanswered one give no item
    (the responder never saw it)."""
    out: Dict[int, List[MemoryItem]] = {}
    for c in convs:
        if owner not in (c['initiator'], c['responder']) or not c['messages']:
            continue
        replied = c.get('replied') is not False
        if not replied and owner == c['responder']:
            continue
        other = c['responder'] if c['initiator'] == owner else c['initiator']
        msgs = [dict(agent_id=m['agent_id'], text=m['text']) for m in c['messages']]
        ment = mentions or (lambda _t: [])
        mo: List[str] = []
        mw: List[str] = []
        for m in msgs:
            target = mw if m['agent_id'] == owner else mo
            target.extend(x for x in ment(m['text']) if x not in target)
        out.setdefault(other, []).append(MemoryItem(
            kind='conv', round_number=c['round_number'], conv_id=c['conv_id'], started_by=c['initiator'],
            replied=replied, messages=msgs, mentions_other=mo, mentions_own=mw))
    return out


class DyadLedger:
    """All N (N - 1) / 2 dyads of a session."""

    def __init__(self, ids: Iterable[int], edges: Iterable[Iterable[int]],
                 exclusions: Dict[int, Iterable[int]], compat: Optional[Any] = None,
                 keep_memory: bool = False, hooks: Optional[List[str]] = None):
        """compat: network_chat.Compat or None (no channels: attributes stay None).
        keep_memory: store MemoryItems (net_memory_mode 'decay').
        hooks: names in DYAD_HOOKS, run in this order by close_round."""
        self.ids = sorted(int(i) for i in ids)
        self.keep_memory = keep_memory
        unknown = [h for h in (hooks or []) if h not in DYAD_HOOKS]
        if unknown:
            raise ValueError(f"unknown net_dyad_hooks: {unknown} (registered: {sorted(DYAD_HOOKS)})")
        self.hooks = list(hooks or [])
        edge_set = {_pair(int(a), int(b)) for a, b in edges}
        excl = {_pair(a, b) for a, xs in exclusions.items() for b in xs}
        self.compat = compat
        self.dyads: Dict[Pair, Dyad] = {}
        for x, a in enumerate(self.ids):
            for b in self.ids[x + 1:]:
                d = Dyad(a=a, b=b, edge=(a, b) in edge_set, excluded=(a, b) in excl,
                         attempts={a: 0, b: 0})
                if compat is not None:
                    d.compat = compat.compat[(a, b)]
                    d.topic = compat.topic[(a, b)]
                    d.media = compat.media[(a, b)]
                    d.z = compat.z[(a, b)]
                    d.reach = {a: compat.reach[(a, b)], b: compat.reach[(b, a)]}
                    d.shared = list(compat.shared[(a, b)])
                self.dyads[(a, b)] = d
        self.recorded_rounds: set = set()
        self.closed_rounds: set = set()
        self._touched: Dict[Pair, Dict[str, Any]] = {}

    # -- access ---------------------------------------------------------------

    def get(self, i: int, j: int) -> Dyad:
        return self.dyads[_pair(i, j)]

    def touch(self, i: int, j: int) -> None:
        """Mark a dyad as changed in the current round (for the round record)."""
        self._touched.setdefault(_pair(i, j), {})

    def _delta(self, i: int, j: int) -> Dict[str, Any]:
        return self._touched.setdefault(_pair(i, j), {})

    # -- recording (caller holds the bridge lock) --------------------------------

    def record_network_round(self, round_number: int, convs: List[Dict[str, Any]],
                             mentions: Optional[Callable[[str], List[str]]] = None) -> None:
        """Counters of every conversation of the round, and (keep_memory) each
        side's memory items. Once per round."""
        if round_number in self.recorded_rounds:
            return
        self.recorded_rounds.add(round_number)
        for c in convs:
            d = self.get(c['initiator'], c['responder'])
            delta = self._delta(d.a, d.b)
            unanswered = c.get('replied') is False
            d.attempts[c['initiator']] += 1
            delta.setdefault('attempts', {})[c['initiator']] = delta.get('attempts', {}).get(c['initiator'], 0) + 1
            if unanswered:
                d.unanswered += 1
                delta['unanswered'] = delta.get('unanswered', 0) + 1
            else:
                d.answered += 1
                delta['answered'] = delta.get('answered', 0) + 1
            n = len(c['messages'])
            d.messages += n
            delta['messages'] = delta.get('messages', 0) + n
            if not d.rounds or d.rounds[-1] != round_number:
                d.rounds.append(round_number)
            d.last_round = round_number
        if self.keep_memory:
            for owner in self.ids:
                for other, items in memory_items_from_convs(
                        owner, [dict(c, round_number=round_number) for c in convs], mentions).items():
                    self.get(owner, other).memory.setdefault(owner, []).extend(items)

    def record_pair_chat(self, round_number: int, pair: Pair, chatted: bool, n_msgs: int,
                         messages: Optional[List[Dict[str, Any]]] = None,
                         mentions: Optional[Callable[[str], List[str]]] = None) -> None:
        """A partner pair's chat of the round (or its skipped chat,
        net_pair_chat_model 'compat')."""
        d = self.get(*pair)
        delta = self._delta(d.a, d.b)
        if chatted:
            d.pair_chats += 1
            delta['pair_chat'] = 1
            d.messages += n_msgs
            delta['messages'] = delta.get('messages', 0) + n_msgs
            if not d.rounds or d.rounds[-1] != round_number:
                d.rounds.append(round_number)
            d.last_round = round_number
            if self.keep_memory and messages:
                for owner in (d.a, d.b):
                    for item in memory_items_from_convs(owner, [dict(
                            round_number=round_number, conv_id=f"r{round_number}p{d.a}-{d.b}",
                            initiator=d.a, responder=d.b, messages=messages)], mentions).get(
                            d.b if owner == d.a else d.a, []):
                        item.kind = 'pair_chat'
                        d.memory.setdefault(owner, []).append(item)
        else:
            d.pair_chat_skips += 1
            delta['pair_chat_skipped'] = 1

    def close_round(self, round_number: int, outcomes: Dict[int, Dict[str, Any]],
                    context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """End of a round: run the hooks, return the round's record
        {round_number, dyads: [{a, b, attempts, answered, unanswered, messages,
        pair_chat, ext}]} for the dyads that changed (communication or ext).
        Once per round (a repeated call returns an empty record)."""
        if round_number in self.closed_rounds:
            return dict(round_number=round_number, dyads=[])
        self.closed_rounds.add(round_number)
        before = {k: json.dumps(d.ext, sort_keys=True, default=str) for k, d in self.dyads.items()} \
            if self.hooks else {}
        for name in self.hooks:
            DYAD_HOOKS[name](self, round_number, outcomes, context or {})
        for k, d in self.dyads.items():
            if k in before and json.dumps(d.ext, sort_keys=True, default=str) != before[k]:
                self._touched.setdefault(k, {})
        records = []
        for (a, b), delta in sorted(self._touched.items()):
            d = self.dyads[(a, b)]
            att = delta.get('attempts', {})
            records.append(dict(
                a=a, b=b, attempts={str(a): att.get(a, 0), str(b): att.get(b, 0)},
                answered=delta.get('answered', 0), unanswered=delta.get('unanswered', 0),
                messages=delta.get('messages', 0), pair_chat=delta.get('pair_chat', 0),
                pair_chat_skipped=delta.get('pair_chat_skipped', 0), ext=d.ext))
        self._touched = {}
        return dict(round_number=round_number, dyads=records)

    # -- output ---------------------------------------------------------------------

    def static_records(self) -> List[Dict[str, Any]]:
        """Fixed attributes of every dyad (dyads.json)."""
        return [dict(a=d.a, b=d.b, edge=d.edge, excluded=d.excluded, compat=d.compat, topic=d.topic,
                     media=d.media, z=d.z,
                     reach_ab=d.reach.get(d.a), reach_ba=d.reach.get(d.b), shared=d.shared)
                for d in self.dyads.values()]

    def snapshot(self) -> List[Dict[str, Any]]:
        """Fixed attributes plus the counters so far."""
        out = []
        for rec, d in zip(self.static_records(), self.dyads.values()):
            out.append(dict(rec, attempts={str(d.a): d.attempts[d.a], str(d.b): d.attempts[d.b]},
                            answered=d.answered, unanswered=d.unanswered, messages=d.messages,
                            pair_chats=d.pair_chats, pair_chat_skips=d.pair_chat_skips,
                            rounds=list(d.rounds), last_round=d.last_round, ext=d.ext))
        return out
