"""Canonical executable state machine for genuine (non-proxy) Strat 2-1-2
continuation and 1-2-2 reversal patterns.

The module is deliberately pure: callers provide this bar's already-classified
bar-type context (identically available on live and replay via
previous_bar_type / two_bars_back_type / previous_bar_high / previous_bar_low
— see webhook/state_builder.py and replay/candle_loader.py) and receive an
optional candidate. Live runtime and replay use this same function.

Causality: the boundary that prices entry/stop is fixed from the immediately
PRIOR bar only (already closed before the bar being evaluated began). The bar
being evaluated is never used to derive its own trigger price — only to test
whether it crossed a boundary that was already fixed before it began. This is
why entry uses previous_bar_high/previous_bar_low, never this bar's own
high/low (that was the proven defect in the pre-canonicalization code).

Both patterns can only be classified once the evaluated bar itself has
closed (TradingView fires alerts bar-close-only — see the audit trail for
this repair), so "when do we learn it happened" and "what price would a real
resting order have achieved" are answered separately: the former is
necessarily post-close; the latter is proven causal by construction here.

Same-bar ambiguity: if the evaluated bar's range crosses BOTH the entry
boundary and the opposite (stop) boundary, OHLC data cannot establish which
was touched first. Per ruling, this resolves pessimistically as entry
triggered then immediately stopped (LOSS) — never silently dropped, and
never resolved optimistically. The caller must journal this directly (it is
not a live order to submit) — see candidate["kind"] == "RESOLVED" below.

Target is a fixed 2R off the corrected structural stop, applied identically
to both patterns. This is an explicit VP implementation convention, not
canonical Strat doctrine — no external Strat source defines a deterministic
target for either pattern (checked: strat.trading's own 2-1-2 and 1-2-2
pages, and a third-party comprehensive Strat guide, all confirm no
established target rule exists for these specific patterns).
"""

from __future__ import annotations

from typing import Optional

from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, normalize_bar_type

STRAT_212 = "strat_212"
STRAT_122 = "strat_122"

# "Agreed tick buffer" beyond the reference bar's opposite boundary for the
# stop — matches the magnitude already used elsewhere in this codebase for an
# analogous structural stop offset (e.g. the pre-canonicalization proxy code).
# The anchor bar was the proven defect, not this buffer's magnitude.
_STOP_BUFFER_TICKS = 4.0

# VP implementation convention — not canonical Strat doctrine. See module
# docstring: no external Strat source defines a target rule for these
# patterns. Subject to empirical validation once evidence is regenerated.
_TARGET_R = 2.0


def advance_strat_212_122(
    *,
    previous_bar_type: Optional[str],
    two_bars_back_type: Optional[str],
    previous_bar_high: Optional[float],
    previous_bar_low: Optional[float],
    current_high: float,
    current_low: float,
    tick_size: float,
    trading_date: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, Optional[dict]]:
    """Advance one bar of causal Strat 2-1-2 / 1-2-2 detection.

    ``persisted_state`` (the prior bar's returned state) is accepted and
    reconstructed for audit-trail continuity across a process restart,
    matching the shared state-machine shape used elsewhere in this codebase.
    It is not required for the trigger decision itself: the two-bar
    precursor (previous_bar_type / two_bars_back_type) and the boundary
    (previous_bar_high / previous_bar_low) are already given fresh on every
    bar by the same upstream classification both live and replay already
    rely on, so this function recomputes the precursor and boundary from
    THIS bar's own fields rather than trusting carried-over state that could
    silently go stale.

    Returns (next_state, candidate). ``candidate`` is None when nothing
    resolves this bar (no precursor, missing boundary data, the watched bar
    didn't reach either boundary, or it reached only the invalidation side).
    When present, candidate["kind"] is:
      - "OPEN": a normal tradeable setup (entry/stop/target) for the
        standard risk-validation/broker pipeline, exactly like any other
        strategy's candidate.
      - "RESOLVED": the entry boundary and the opposite (stop) boundary were
        BOTH crossed on the same watched bar. The caller must journal this
        directly (pessimistic LOSS) and must never submit it as a live
        order — see module docstring.
    """
    previous_type = normalize_bar_type(previous_bar_type)
    two_back_type = normalize_bar_type(two_bars_back_type)

    pattern: Optional[str] = None
    direction: Optional[str] = None
    if previous_type == INSIDE_BAR and two_back_type in (TWO_UP, TWO_DOWN):
        pattern = STRAT_212
        direction = "LONG" if two_back_type == TWO_UP else "SHORT"
    elif previous_type in (TWO_UP, TWO_DOWN) and two_back_type == INSIDE_BAR:
        pattern = STRAT_122
        direction = "LONG" if previous_type == TWO_DOWN else "SHORT"

    if pattern is None or direction is None:
        return {"trading_date": trading_date, "status": "NO_PRECURSOR"}, None

    if previous_bar_high is None or previous_bar_low is None:
        return (
            {
                "trading_date": trading_date,
                "status": "NO_CANDIDATE_MISSING_BOUNDARY_DATA",
                "pattern": pattern,
                "direction": direction,
            },
            None,
        )

    stop_buffer = tick_size * _STOP_BUFFER_TICKS
    if direction == "LONG":
        boundary_entry = float(previous_bar_high)
        boundary_stop = float(previous_bar_low)
        entry_price = boundary_entry + tick_size
        stop_price = boundary_stop - stop_buffer
        entry_crossed = current_high >= entry_price
        stop_crossed = current_low <= stop_price
    else:
        boundary_entry = float(previous_bar_low)
        boundary_stop = float(previous_bar_high)
        entry_price = boundary_entry - tick_size
        stop_price = boundary_stop + stop_buffer
        entry_crossed = current_low <= entry_price
        stop_crossed = current_high >= stop_price

    risk = abs(entry_price - stop_price)
    target_price = (
        entry_price + (risk * _TARGET_R)
        if direction == "LONG"
        else entry_price - (risk * _TARGET_R)
    )

    base_state = {
        "trading_date": trading_date,
        "pattern": pattern,
        "direction": direction,
        "boundary_entry": boundary_entry,
        "boundary_stop": boundary_stop,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
    }

    if not entry_crossed and not stop_crossed:
        # One-bar watch window only: the precursor does not carry forward to
        # a later bar. A real trader re-evaluates fresh next bar; so do we.
        return {**base_state, "status": "EXPIRED_NOT_TRIGGERED"}, None

    if not entry_crossed and stop_crossed:
        # The invalidation side broke first (or only) — the pattern never
        # triggered a position at all. Not a trade, not a loss; no candidate.
        return {**base_state, "status": "INVALIDATED_BEFORE_TRIGGER"}, None

    if entry_crossed and stop_crossed:
        candidate = {
            "kind": "RESOLVED",
            "pattern": pattern,
            "direction": direction,
            "entry": entry_price,
            "exit": stop_price,
            "target": target_price,
            "result": "LOSS",
            "exit_reason": "OUTSIDE_AFTER_TRIGGER",
        }
        return {**base_state, "status": "TRIGGERED_OUTSIDE_AFTER_TRIGGER"}, candidate

    candidate = {
        "kind": "OPEN",
        "pattern": pattern,
        "direction": direction,
        "entry": entry_price,
        "stop": stop_price,
        "target": target_price,
    }
    return {**base_state, "status": "TRIGGERED"}, candidate
