"""Claude spend for the current run: counted from every call's token usage, capped, and reported.

2026-11-10: a full writer day (ten avatar videos, with the rewrites the checks ask for) measured about 120,000 input and
150,000 output tokens, roughly $1.70 on claude-sonnet-5-5 at $2 / $10 per million tokens. Rewrites pay for output twice, so
a bad day costs more. CLAUDE_RUN_BUDGET_USD (default 4.0; 0 turns it off) is a hard stop for one run: once it is spent,
no further Claude call is made, the writers treat that like a failed call, and the day ships the videos it already has.

All Claude calls go through script_writer._stream_with_retries, which records each response's usage here and checks the
budget before the next call.
"""
from __future__ import annotations

import os
import threading

# model -> (USD per million input tokens, USD per million output tokens). Cache writes cost 1.25x input, reads 0.05x.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-1": (10.0, 50.0),
}
DEFAULT_PRICE = (10.0, 50.0)  # an unknown model is costed at the highest known rate, so the cap errs on the safe side
DEFAULT_BUDGET_USD = 4.0

_lock = threading.Lock()
_state = {"usd": 0.0, "calls": 0, "input": 0, "output": 0}


class BudgetExceeded(Exception):
    pass


def reset() -> None:
    with _lock:
        _state.update(usd=0.0, calls=0, input=0, output=0)


def budget_usd() -> float:
    try:
        return float(os.environ.get("CLAUDE_RUN_BUDGET_USD", DEFAULT_BUDGET_USD) or 0)
    except ValueError:
        return DEFAULT_BUDGET_USD


def cost_of(model: str | None, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float:
    p_in, p_out = PRICES.get(model or "", DEFAULT_PRICE)
    return (input_tokens * p_in + cache_write * p_in * 1.25 + cache_read * p_in * 0.05 + output_tokens * p_out) / 1e6


def record(model: str | None, usage) -> None:
    """Adds one response's usage (the SDK's usage object, or anything with the same attributes)."""
    if usage is None:
        return
    i, o = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
    cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    with _lock:
        _state["usd"] += cost_of(model, i, o, cw, cr)
        _state["calls"] += 1
        _state["input"] += i + cw + cr
        _state["output"] += o


def spent() -> float:
    with _lock:
        return _state["usd"]


def check() -> None:
    """Raises BudgetExceeded when this run has already spent its budget."""
    cap = budget_usd()
    if cap > 0 and spent() >= cap:
        raise BudgetExceeded(f"this run has used ${spent():.2f} of Claude, over its ${cap:.2f} limit "
                             "(CLAUDE_RUN_BUDGET_USD)")


def snapshot() -> dict:
    with _lock:
        return {"claude_usd": round(_state["usd"], 3), "claude_calls": _state["calls"],
                "claude_tokens_in": _state["input"], "claude_tokens_out": _state["output"]}


def summary() -> str:
    """"Claude about $1.73 (271k tokens, 24 calls)", or "" when nothing was spent."""
    s = snapshot()
    if not s["claude_calls"]:
        return ""
    total = s["claude_tokens_in"] + s["claude_tokens_out"]
    cap = budget_usd()
    tail = f", hit its ${cap:.2f} limit" if cap > 0 and s["claude_usd"] >= cap else ""
    return f"Claude about ${s['claude_usd']:.2f} ({total // 1000}k tokens, {s['claude_calls']} calls{tail})"
