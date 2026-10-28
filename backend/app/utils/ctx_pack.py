"""TensorFold ctx-400 packing for LLMClient.chat()."""
import logging

_log = logging.getLogger(__name__)


def _est_tokens(text: str) -> int:
    # Japanese/mixed CJK is ~1 token/char (slightly more with punctuation);
    # ASCII is ~3.5-4 chars/token. 1.2/3.5 is deliberately conservative.
    jp = sum(1 for ch in text if ord(ch) > 0x2E80)
    return int(jp * 1.2 + (len(text) - jp) / 3.5)


def parse_ctx_400(exc: Exception):
    s = str(exc)
    if "context length" not in s or "400" not in s:
        return None
    room = int(s.split("has ")[1].split(" tokens")[0]) if "has " in s else 0
    server_ctx = int(s.split("context length is ")[1].split(" tokens")[0]) if "context length is " in s else 8192
    return room, server_ctx


def pack_messages(messages, budget_max, room, server_ctx):
    """Pack conversation to fit server_ctx: keep system + tail turns, truncate
    the oldest kept turn to fit. Returns (msgs2, budget)."""
    budget = max(256, min(budget_max, server_ctx - room - 32))
    avail_tok = server_ctx - budget - 32
    head = [m for m in messages if m.get("role") == "system"]
    tail = [m for m in messages if m.get("role") != "system"]
    head_tok = sum(_est_tokens(m.get("content") or "") for m in head)

    # truncate system if it alone does not fit
    if head and head_tok > avail_tok:
        c0 = head[0].get("content") or ""
        lo, hi = 1, len(c0)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _est_tokens(c0[:mid]) <= avail_tok:
                lo = mid
            else:
                hi = mid - 1
        head = [dict(head[0], content=c0[:lo] + "\n[…]")]
        head_tok = _est_tokens(head[0]["content"])

    # keep tail turns from the newest; stop when next would overflow
    kept = []
    used = head_tok
    for m in reversed(tail):
        t = _est_tokens(m.get("content") or "")
        if used + t > avail_tok:
            break
        kept.append(m)
        used += t
    kept.reverse()

    if not kept and tail:
        # even the newest single turn does not fit: truncate it to avail
        m = tail[-1]
        c0 = m.get("content") or ""
        avail_for_turn = max(64, avail_tok - head_tok)
        lo, hi = 1, len(c0)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _est_tokens(c0[:mid]) <= avail_for_turn:
                lo = mid
            else:
                hi = mid - 1
        kept = [dict(m, content=c0[:lo] + "\n[…]")]
    elif kept:
        # the oldest kept turn may have overflowed; trim it to exactly fit
        m0 = kept[0]
        c0 = m0.get("content") or ""
        others = head_tok + sum(_est_tokens(m.get("content") or "") for m in kept[1:])
        allowed = max(64, avail_tok - others)
        if _est_tokens(c0) > allowed:
            lo, hi = 1, len(c0)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _est_tokens(c0[:mid]) <= allowed:
                    lo = mid
                else:
                    hi = mid - 1
            kept[0] = dict(m0, content=c0[:lo] + "\n[…]")

    msgs2 = head + kept
    # ChatML: last turn must be user; assistant-only tails get a nudge
    if msgs2 and msgs2[-1].get("role") == "assistant":
        msgs2 = msgs2 + [{"role": "user",
                          "content": "Please continue and produce the final section content."}]
    return msgs2, budget
