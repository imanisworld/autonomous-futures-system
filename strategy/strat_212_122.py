"""Canonical executable state machine for genuine (non-proxy) Strat 2-1-2
continuation and 1-2-2 reversal patterns.

Genuine two-phase armed state machine, mirroring strategy/four_hr_retrigger.py
(#317):

  Phase 1 (ARM) — when the bar being processed completes a valid precursor
  (checked from ITS OWN current_bar_type/previous_bar_type only — no
  lookahead into the bar that will follow), THAT bar's own high/low become
  the fixed reference boundary and the state is stored as ARMED. No
  candidate is produced yet: an armed setup is a level to watch, not a trade.

  Phase 2 (RESOLVE) — on the NEXT bar only (persisted_state.status=="ARMED"
  from the prior bar's advance), that bar's own high/low are tested against
  the boundary that was already fixed in phase 1, before this bar began. The
  watch window is exactly one bar; whatever this bar's OHLC shows produces a
  final outcome, and the state resets.

This two-phase split is the causal property itself, not a bookkeeping
nicety: an order price computed from data that predates the bar it is
resolved against can never be a retroactive fill, regardless of when in the
pipeline the resolution decision happens to run.

2-1-2 arms on the inside bar (bar N): current_bar_type==inside AND its own
previous_bar_type is directional (2U/2D) — i.e. a directional bar was
immediately followed by an inside bar. Direction matches that prior
directional bar. Boundary = the inside bar's (bar N's) own high/low.

1-2-2 arms on the directional bar (bar N) that immediately follows an inside
bar: current_bar_type is directional AND its own previous_bar_type==inside.
Direction is the reversal of bar N's own direction (2D arms a LONG reversal,
2U arms a SHORT reversal). Boundary = that directional bar's (bar N's) own
high/low — it is the bar the next bar's reversal breaks through.

Resolution (phase 2) checks the watched bar's high/low against entry, stop,
and target together — not just entry-vs-stop:
  - neither boundary reached                       -> no trade (expired)
  - only the invalidation (stop) side reached       -> no trade (invalidated)
  - entry reached, causal fill outside the stop/    -> no trade
    target bracket (an extreme same-bar gap)           (GAP_INVALIDATED_BRACKET)
  - entry reached, neither stop nor target reached  -> OPEN (already-triggered
                                                        position, causal fill)
  - entry AND target reached, stop not reached      -> WIN, resolved same-bar
  - entry AND stop reached (target irrelevant: if    -> LOSS, resolved same-bar
    target was ALSO reached the ordering among all
    three is unknowable too — pessimistic LOSS
    covers that case as well)

A same-bar resolved outcome (WIN or LOSS) is evidence, not a live order: the
caller must journal it directly and must never submit it as a broker order —
see candidate["kind"] == "RESOLVED" and the webhook/runner.py /
replay/replay_engine.py consumption of this field.

candidate["entry"] is NEVER the raw structural trigger price — for OPEN,
WIN, and LOSS alike. The trade triggered somewhere inside the watched bar;
by the time this bar's close is being processed, it should already exist,
priced at what a genuinely-armed order would actually have achieved, not at
the structural level as though it were automatically fillable. This applies
identically to same-bar WIN/LOSS as it does to OPEN — the entry price is the
same fact regardless of what happens on the rest of that bar. Mirrors
execution/paper_broker.py's own existing "stop_market" causal entry
primitive (_activate_pending_stop_entry) exactly: if the bar's OPEN already
gapped through the trigger, the fill is the open (worse for the trader,
never better); otherwise it fills at the exact trigger level, reached
intrabar. Stop/target (the levels, and the exit price for a same-bar
WIN/LOSS) stay at their original structural values regardless of the gap —
only entry is gap-adjusted.

The causal fill is validated against the stop/target bracket IMMEDIATELY
after it is computed, before branching into LOSS/WIN/OPEN — again mirroring
_activate_pending_stop_entry, which rejects a fill outside its own
stop/target bracket rather than ever opening (or booking) a position on the
wrong side of it. A gap extreme enough to put the causal fill beyond target
(or, symmetrically, beyond the SHORT-side target) produces an internally
contradictory "WIN": entry priced worse than the very level being called the
win. Such a fill fails closed to GAP_INVALIDATED_BRACKET — no candidate —
rather than being reported as a resolved outcome. This check is unconditional
across all three outcome kinds, not scoped to OPEN alone.

Entry is offset one tick beyond the boundary (the level must be broken, not
merely touched). Stop is the exact opposite boundary of the reference bar —
no additional buffer. An earlier draft of this module added a 4-tick stop
buffer inherited from the pre-canonicalization proxy code; that buffer was
never established as canonical for the corrected formula and has been
removed. Do not reintroduce a stop buffer without a settled rule for it.

Target is a fixed 2R off the corrected stop, applied identically to both
patterns. This is an explicit VP implementation convention, not canonical
Strat doctrine — no external Strat source defines a deterministic target for
either pattern (checked: strat.trading's own 2-1-2 and 1-2-2 pages, and a
third-party comprehensive Strat guide, all confirm no established target
rule exists for these specific patterns).
"""

from __future__ import annotations

from typing import Optional

from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, normalize_bar_type

STRAT_212 = "strat_212"
STRAT_122 = "strat_122"

# VP implementation convention — not canonical Strat doctrine. See module
# docstring: no external Strat source defines a target rule for these
# patterns. Subject to empirical validation once evidence is regenerated.
_TARGET_R = 2.0


def advance_strat_212_122(
    *,
    current_bar_type: Optional[str],
    previous_bar_type: Optional[str],
    current_open: float,
    current_high: float,
    current_low: float,
    tick_size: float,
    trading_date: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, Optional[dict]]:
    """Advance one bar of the two-phase armed Strat 2-1-2 / 1-2-2 detector.

    ``current_bar_type`` / ``previous_bar_type`` describe the bar CURRENTLY
    being processed (its own type, and the type of the bar immediately
    before it) — the same fields Pine/replay already attach to every bar.
    ``current_open`` / ``current_high`` / ``current_low`` are that same
    bar's own OHLC (open is only used when resolving an OPEN outcome's
    gap-aware fill price — see module docstring).

    Returns (next_state, candidate). ``candidate`` is never produced on the
    same bar an arm forms — only on the watch bar that resolves it. See
    module docstring for the full outcome table.
    """
    previous = dict(persisted_state or {})
    if previous.get("status") == "ARMED" and previous.get("trading_date") == trading_date:
        return _resolve(
            previous,
            current_open=current_open,
            current_high=current_high,
            current_low=current_low,
        )

    return _maybe_arm(
        current_bar_type=normalize_bar_type(current_bar_type),
        previous_bar_type=normalize_bar_type(previous_bar_type),
        current_high=current_high,
        current_low=current_low,
        tick_size=tick_size,
        trading_date=trading_date,
    )


def _maybe_arm(
    *,
    current_bar_type: Optional[str],
    previous_bar_type: Optional[str],
    current_high: float,
    current_low: float,
    tick_size: float,
    trading_date: str,
) -> tuple[dict, None]:
    pattern: Optional[str] = None
    direction: Optional[str] = None
    if current_bar_type == INSIDE_BAR and previous_bar_type in (TWO_UP, TWO_DOWN):
        # 2-1-2: this bar is the inside bar; direction matches the
        # directional bar that just preceded it.
        pattern = STRAT_212
        direction = "LONG" if previous_bar_type == TWO_UP else "SHORT"
    elif current_bar_type in (TWO_UP, TWO_DOWN) and previous_bar_type == INSIDE_BAR:
        # 1-2-2: this bar is the directional bar following an inside bar;
        # the pattern arms a reversal of THIS bar's own direction.
        pattern = STRAT_122
        direction = "LONG" if current_bar_type == TWO_DOWN else "SHORT"

    if pattern is None or direction is None:
        return {"trading_date": trading_date, "status": "IDLE"}, None

    boundary_high = float(current_high)
    boundary_low = float(current_low)
    if direction == "LONG":
        entry_price = boundary_high + tick_size
        stop_price = boundary_low
    else:
        entry_price = boundary_low - tick_size
        stop_price = boundary_high
    risk = abs(entry_price - stop_price)
    target_price = (
        entry_price + (risk * _TARGET_R)
        if direction == "LONG"
        else entry_price - (risk * _TARGET_R)
    )

    return {
        "trading_date": trading_date,
        "status": "ARMED",
        "pattern": pattern,
        "direction": direction,
        "boundary_high": boundary_high,
        "boundary_low": boundary_low,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
    }, None


def _resolve(
    armed: dict, *, current_open: float, current_high: float, current_low: float
) -> tuple[dict, Optional[dict]]:
    direction = armed["direction"]
    pattern = armed["pattern"]
    entry_price = float(armed["entry_price"])
    stop_price = float(armed["stop_price"])
    target_price = float(armed["target_price"])

    if direction == "LONG":
        entry_touched = current_high >= entry_price
        stop_touched = current_low <= stop_price
        target_touched = current_high >= target_price
    else:
        entry_touched = current_low <= entry_price
        stop_touched = current_high >= stop_price
        target_touched = current_low <= target_price

    idle_state = {"trading_date": armed["trading_date"], "status": "IDLE"}

    if not entry_touched:
        # The invalidation side alone (or neither side) — no position was
        # ever opened. Not a trade either way; the one-bar watch window is
        # spent regardless (the precursor does not carry forward further).
        return idle_state, None

    # Entry triggered this bar (WIN, LOSS, or still-open) — price it at what
    # a genuinely-armed order would actually have achieved, not the
    # structural trigger level as though it were automatically fillable.
    # Gap-through-at-open is worse for the trader, never better, exactly
    # matching execution/paper_broker.py::_activate_pending_stop_entry's own
    # logic. This applies identically whether the bar also resolved via
    # target/stop this same bar or is still open — the trade's ENTRY price
    # is the same fact regardless of what happens afterward on this bar.
    if direction == "LONG":
        fill_entry = current_open if current_open >= entry_price else entry_price
        bracket_valid = stop_price < fill_entry < target_price
    else:
        fill_entry = current_open if current_open <= entry_price else entry_price
        bracket_valid = target_price < fill_entry < stop_price

    # Validated immediately, before branching into LOSS/WIN/OPEN — mirrors
    # _activate_pending_stop_entry, which rejects a fill outside its own
    # stop/target bracket rather than opening (or booking) a position on the
    # wrong side of it. A gap extreme enough to fill beyond target (or,
    # symmetrically, beyond target on the SHORT side) would otherwise
    # produce an internally contradictory "WIN": entry priced worse than the
    # level being called the win. Fail closed instead — no candidate.
    if not bracket_valid:
        return {**idle_state, "status": "GAP_INVALIDATED_BRACKET"}, None

    if stop_touched:
        # Entry and the opposite (stop) boundary both reached this bar. If
        # target was ALSO reached, the ordering among all three is
        # unknowable too — pessimistic LOSS covers that case as well, per
        # ruling: never resolve optimistically on an ambiguous same-bar path.
        candidate = {
            "kind": "RESOLVED",
            "pattern": pattern,
            "direction": direction,
            "entry": fill_entry,
            "stop": stop_price,
            "target": target_price,
            "exit": stop_price,
            "result": "LOSS",
            "exit_reason": "OUTSIDE_AFTER_TRIGGER",
        }
        return idle_state, candidate

    if target_touched:
        candidate = {
            "kind": "RESOLVED",
            "pattern": pattern,
            "direction": direction,
            "entry": fill_entry,
            "stop": stop_price,
            "target": target_price,
            "exit": target_price,
            "result": "WIN",
            "exit_reason": "TARGET_HIT_SAME_BAR",
        }
        return idle_state, candidate

    # Still open: bracket validity was already confirmed unconditionally
    # above, before the LOSS/WIN checks — this is the only remaining outcome.
    candidate = {
        "kind": "OPEN",
        "pattern": pattern,
        "direction": direction,
        "entry": fill_entry,
        "stop": stop_price,
        "target": target_price,
    }
    return idle_state, candidate
