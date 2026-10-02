"""
Betrayal events and reputation scores (NOTES.md #57), ground truth, no LLM.

Pure functions, stdlib only (re, math) and no relative imports, so the offline
analysis (scripts/experiment/analyze_betrayal.py) can load this file by path
without importing the Flask app. dyad_hooks.py wires them into the dyad
ledger; the analysis recomputes the same events from the logs of any run.

Choices are internal here: 'A' cooperate and 'B' defect for the prisoner's
dilemma, whole numbers for the public goods game (pgg). Two kinds of event:

  word    what an agent SAID it would choose in a conversation (stated_intention)
          against what it then chose in the same round. The extractor is a
          conservative rule, INTENT_RULE_VERSION below: one sentence that
          announces a choice and names exactly one option. No LLM judges
          anything, a sentence that does not qualify gives no statement.
          PD kept = stated == chose; pgg kept = chose >= stated (deficit =
          how much less it put in).
  game    what an agent DID: in PD the partner chose B while the victim chose
          A (kind 'break' after at least one mutual-A round right before,
          'repeat' if the partner also chose B the round before, else
          'first'), plus an 'exploit' event for the exploiter; in pgg a
          member 'drops' from at least the mean of the others to below it, and
          'exploit' is simply putting in less than the mean of the others.

Agents whose outcome is missing (the LLM answer was unusable, so the choice is
a default and says nothing about intent) are skipped. beta_score and
contact_multiplier are the reputation arithmetic (dyad_hooks.py, bridge).
"""

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

INTENT_RULE_VERSION = 1

SKIP_SOURCES = ('llm_default', 'default')

# A sentence announces a choice when an INTENT matches (or an INTENT_WEAK after a first-person
# marker), HEDGE does not, and what follows names exactly one option. Rule version 1 was
# tightened and widened on the 7 archived PD runs (NOTES.md #57, gate G1): strong announcements
# are the first person saying what it will do, optionally with an adverb ("I also intend",
# "I certainly plan"); the weak verbs also describe what somebody else did ("my partner's switch
# to X", "partner's return to X"), so they count only after a first-person marker; "selecting"
# is left out ("by selecting X" is a report).
_ADV = r"(?:(?:also|still|certainly|definitely|now|likely|just|really|truly|simply|probably) )?"
INTENT = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|i " + _ADV + r"(?:intend|plan|will)"
    r"|i(?:'m| am) " + _ADV + r"(?:planning|leaning|choosing|sticking with|continuing with|going with"
    r"|staying with|opting for|selecting|picking|thinking of)"
    r"|i(?:'ve| have) decided to|i decided to|planning to|thinking of choosing|i'd like to|my plan is"
    r"|i'll be choosing)\b", re.I)
INTENT_WEAK = re.compile(
    r"\b(stick with|continue with|continue choosing|keep choosing|return to|switch to|go with|select"
    r"|put in)\b", re.I)
FIRST_PERSON = re.compile(r"\b(i|i'm|i'll|i've|i'd|my plan)\b", re.I)
# What follows an announcement after these words is a contrast, not the choice: "I'll choose X to
# avoid the volatility of Y", "... rather than Y", "..., even though I'd love Y"
CONTRAST = re.compile(r"\b(avoid|avoiding|instead of|rather than|than|even though|although|though"
                      r"|despite|whereas|but|unlike|versus)\b", re.I)
# Symbols written as LaTeX by some models
LATEX_SYMBOLS = ((re.compile(r"\$?\\(?:diamond|lozenge)\$?"), '\u25c7'),
                 (re.compile(r"\$?\\(?:bigtriangleup|triangle)\$?"), '\u25b3'),
                 (re.compile(r"\$?\\(?:square|Box)\$?"), '\u25a1'),
                 (re.compile(r"\$?\\(?:bigcirc|circ)\$?"), '\u25cb'))
HEDGE = re.compile(r"\b(whether|deciding|undecided|not sure)\b", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")
# pgg: a whole number that is neither a round number nor a quantity of something else
_NUMBER = re.compile(r"(?<![\d.])(\d+)(?![\d.]*\d)")
_NOT_AMOUNT_BEFORE = re.compile(r"rounds?\s*$", re.I)
_NOT_AMOUNT_AFTER = re.compile(r"\s*(points?\b|pts\b|%|rounds?\b)", re.I)
# Past tense self-reports (analysis only, analyze_betrayal.py)
PAST_REPORT = re.compile(r"\b(i chose|i picked|i went with|i selected|i put in)\b", re.I)
ROUND_REF = re.compile(r"\bround\s+(\d+)", re.I)

STATUSES = ('none', 'single', 'ambiguous', 'hedged')


def _norm(text: str) -> str:
    text = text.replace('\u2019', "'").replace('\u2018', "'")
    for pat, sym in LATEX_SYMBOLS:
        text = pat.sub(sym, text)
    return text


def _options_in(sentence: str, game: str, ctx: Dict[str, Any]) -> List[str]:
    """Distinct options a sentence names: PD the shown symbols, pgg the amounts."""
    if game == 'pd':
        # a letter label ('A') is a word of its own, a symbol is found anywhere
        return [o for o in ctx.get('options', ())
                if (re.search(r"(?<![A-Za-z0-9])" + re.escape(o) + r"(?![A-Za-z0-9])", sentence)
                    if o.isalnum() else o in sentence)]
    out: List[str] = []
    for m in _NUMBER.finditer(sentence):
        if int(m.group(1)) > ctx.get('endowment', 0):
            continue
        if _NOT_AMOUNT_BEFORE.search(sentence[:m.start()]) or _NOT_AMOUNT_AFTER.match(sentence[m.end():]):
            continue
        if str(int(m.group(1))) not in out:
            out.append(str(int(m.group(1))))
    return out


def _intent_match(sentence: str):
    """First announcement in a sentence: a strong INTENT match, or a weak one after a first-person marker."""
    best = INTENT.search(sentence)
    for m in INTENT_WEAK.finditer(sentence):
        if FIRST_PERSON.search(sentence[:m.start()]):
            if best is None or m.start() < best.start():
                best = m
            break
    return best


def stated_intention(texts: Iterable[str], game: str, ctx: Dict[str, Any],
                     round_number: Optional[int] = None) -> Tuple[Optional[str], str]:
    """The one option a speaker announced for this round, from its own messages.

    A sentence qualifies if an announcement matches (INTENT, or INTENT_WEAK after
    a first-person marker), HEDGE does not, and the part from the first match on, up to a contrast
    word (avoid, than, but, ...), names exactly one option (so a report of what
    the other did before 'I'll choose X' does not count). A sentence about
    another round, 'in round N' with N after round_number, does not qualify.
    Qualifying sentences that agree give the value; that disagree, 'ambiguous'.

    Returns:
        (value, status): value is the shown symbol (PD) or the amount as str
        (pgg), None unless status == 'single'. status: 'none' (nothing
        announced), 'single', 'ambiguous', 'hedged' (only hedged sentences
        that name an option).
    """
    values: List[str] = []
    hedged = False
    for text in texts:
        for sentence in SENTENCE_SPLIT.split(_norm(text or '')):
            m = _intent_match(sentence)
            if not m:
                continue
            tail = sentence[m.start():]
            cut = CONTRAST.search(tail)
            opts = _options_in(tail[:cut.start()] if cut else tail, game, ctx)
            if HEDGE.search(sentence):
                hedged = hedged or bool(opts)
                continue
            if round_number is not None and any(int(r) > round_number for r in ROUND_REF.findall(sentence)):
                continue
            if len(opts) == 1:
                values.append(opts[0])
    if values:
        return (values[0], 'single') if len(set(values)) == 1 else (None, 'ambiguous')
    return (None, 'hedged') if hedged else (None, 'none')


# -- outcomes -------------------------------------------------------------------------

def usable(outcome: Optional[Dict[str, Any]]) -> bool:
    """An outcome that is a real decision: present, not missing, not a default."""
    return bool(outcome) and not outcome.get('missing') and outcome.get('source') not in SKIP_SOURCES


def talked(conv: Dict[str, Any]) -> bool:
    """A conversation both sides took part in and the listener saw: answered, with a
    message from each of the two. What the choice reveal (#57) refers to."""
    return (conv.get('replied') is not False
            and len({m.get('agent_id') for m in conv.get('messages', [])}) >= 2)


def _amount(outcome: Dict[str, Any]) -> int:
    return int(float(outcome['choice']))


def _speaker_texts(conv: Dict[str, Any], speaker: int) -> List[str]:
    return [m['text'] for m in conv.get('messages', []) if m.get('agent_id') == speaker]


def word_events(round_number: int, convs: List[Dict[str, Any]], outcomes: Dict[int, Dict[str, Any]],
                game: str, ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """What speakers said against what they chose, per answered conversation.

    Args:
        convs: [{conv_id, initiator, responder, messages: [{agent_id, text}], replied?}]
        ctx: pd {'options': shown symbols, 'shown': {'A': .., 'B': ..}}; pgg {'endowment'}

    Returns:
        (events [{type: 'word', round, conv_id, speaker, listener, stated,
        stated_shown, chose, chose_shown, kept, status} (+ deficit for pgg)],
        counts {'skipped_missing', 'statements', 'speakers'}). Unanswered
        conversations are ignored (the listener never saw them).
    """
    events: List[Dict[str, Any]] = []
    counts = dict(skipped_missing=0, statements=0, speakers=0)
    shown = ctx.get('shown') or {}
    inverse = {v: k for k, v in shown.items()}
    for c in convs:
        if c.get('replied') is False:
            continue
        for speaker, listener in ((c['initiator'], c['responder']), (c['responder'], c['initiator'])):
            texts = _speaker_texts(c, speaker)
            if not texts:
                continue
            counts['speakers'] += 1
            out = outcomes.get(speaker)
            if not usable(out):
                counts['skipped_missing'] += 1
                continue
            value, status = stated_intention(texts, game, ctx, round_number)
            if value is None:
                continue
            counts['statements'] += 1
            ev: Dict[str, Any] = dict(type='word', round=round_number, conv_id=c['conv_id'],
                                      speaker=speaker, listener=listener, status=status)
            if game == 'pd':
                stated = inverse.get(value)
                if stated is None:
                    continue
                chose = out['choice']
                ev.update(stated=stated, stated_shown=value, chose=chose, chose_shown=shown.get(chose, chose),
                          kept=stated == chose)
            else:
                stated, chose = int(value), _amount(out)
                ev.update(stated=stated, stated_shown=stated, chose=chose, chose_shown=chose,
                          kept=chose >= stated, deficit=max(0, stated - chose))
            events.append(ev)
    return events, counts


# -- game events -----------------------------------------------------------------------

def _choice(outcomes_by_round: Dict[int, Dict[int, Dict[str, Any]]], t: int, a: int) -> Optional[str]:
    out = outcomes_by_round.get(t, {}).get(a)
    return out['choice'] if usable(out) else None


def game_events_pd(round_number: int, outcomes_by_round: Dict[int, Dict[int, Dict[str, Any]]],
                   partner: Dict[int, int]) -> Tuple[List[Dict[str, Any]], int]:
    """PD events of one round: [{type: 'game', round, victim, by, kind, streak},
    {type: 'exploit', round, by, partner}] and the number of pairs skipped
    because an outcome was missing."""
    t = round_number
    events: List[Dict[str, Any]] = []
    skipped = 0
    for i in sorted(partner):
        j = partner[i]
        if j not in partner or i == j:
            continue
        ci, cj = _choice(outcomes_by_round, t, i), _choice(outcomes_by_round, t, j)
        if ci is None or cj is None:
            skipped += int(i < j)   # a pair, once
            continue
        if not (ci == 'A' and cj == 'B'):
            continue
        streak, k = 0, t - 1
        while k >= 1 and _choice(outcomes_by_round, k, i) == 'A' and _choice(outcomes_by_round, k, j) == 'A':
            streak += 1
            k -= 1
        if streak >= 1:
            kind = 'break'
        elif _choice(outcomes_by_round, t - 1, j) == 'B':
            kind = 'repeat'
        else:
            kind = 'first'
        events.append(dict(type='game', round=t, victim=i, by=j, kind=kind, streak=streak))
        events.append(dict(type='exploit', round=t, by=j, partner=i))
    return events, skipped


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def game_events_pgg(round_number: int, outcomes_by_round: Dict[int, Dict[int, Dict[str, Any]]],
                    groups: Dict[int, List[int]], endowment: int) -> Tuple[List[Dict[str, Any]], int]:
    """pgg events of one round: a member who gave at least the mean of the
    others last round and gives less than that mean now, and less than it
    gave, 'drops' ({type: 'game', round, by, kind: 'drop', prev, now,
    mean_others_prev, mean_others_now, victims}); victims are the members who
    put in more than it now. {type: 'exploit', round, by, victims} for every
    member below the mean of the others. Returns (events, skipped)."""
    t = round_number
    events: List[Dict[str, Any]] = []
    skipped = 0

    def amounts(r: int, members: List[int]) -> Dict[int, int]:
        got = outcomes_by_round.get(r, {})
        return {m: _amount(got[m]) for m in members if usable(got.get(m))}

    for j in sorted(groups):
        members = [m for m in groups[j] if m in groups]
        now = amounts(t, members)
        others_now = [v for m, v in now.items() if m != j]
        if j not in now or not others_now:
            skipped += 1
            continue
        mo_now = _mean(others_now)
        victims = sorted(m for m, v in now.items() if m != j and v > now[j])
        if now[j] < mo_now:
            events.append(dict(type='exploit', round=t, by=j, victims=victims))
        prev = amounts(t - 1, members) if t > 1 else {}
        others_prev = [v for m, v in prev.items() if m != j]
        if j in prev and others_prev:
            mo_prev = _mean(others_prev)
            if prev[j] >= mo_prev and now[j] < mo_now and now[j] < prev[j]:
                events.append(dict(type='game', round=t, by=j, kind='drop', prev=prev[j], now=now[j],
                                   mean_others_prev=round(mo_prev, 4), mean_others_now=round(mo_now, 4),
                                   victims=victims))
    return events, skipped


# -- reputation arithmetic ---------------------------------------------------------------

def beta_score(successes: float, n: float) -> float:
    """Posterior mean of a Beta(1, 1) prior after `successes` of n: (1 + s) / (2 + n)."""
    return (1.0 + successes) / (2.0 + n)


def contact_multiplier(w: float, b: float, rho_w: float, rho_c: float) -> float:
    """Factor on the weight of contacting a neighbour with word score w and
    choice score b (both in [0, 1], 0.5 = nothing known)."""
    return math.exp(rho_w * (2.0 * w - 1.0) + rho_c * (2.0 * b - 1.0))
