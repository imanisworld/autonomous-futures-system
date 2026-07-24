"""Causal, one-bar-lookback VWAP failed-reclaim (rejection) detector.

Fixes a proven contradiction in the prior vwap_rejection entry condition
(documented in docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md,
PR #308): it required ``state.vwap.reclaimed == True`` AND
``state.vwap.price_vs_vwap == "below"`` on the SAME bar. ``reclaimed`` can
only be True on a bar where price closed above VWAP (a genuine crossover) —
so ``price_vs_vwap == "below"`` on that same bar is structurally impossible.
The condition could never be satisfied; vwap_rejection could never fire.

Canonical causal definition (operator-specified, mirrors the two-phase
arm/resolve pattern already used by strategy/strat_212_122.py and
strategy/four_hr_retrigger.py for the same class of problem — a fact that
can only be known relative to the PRIOR bar, not derivable from a single
bar's own snapshot):

    vwap_failed_reclaim[N] = vwap_reclaimed[N-1] and price_vs_vwap[N] == "below"

The bar immediately following a genuine VWAP reclaim, if it closes back
below VWAP, is a failed reclaim (rejection). This is NOT "price was above
VWAP at some point recently, then went below" — only the bar immediately
after the reclaim bar can be a rejection. Price sitting above VWAP for many
bars before eventually closing below is a plain trend change with no
reclaim attempt to reject, and must NOT be flagged.

By construction, ``vwap_failed_reclaim`` and the CURRENT bar's own
``vwap_reclaimed`` can never both be True on the same bar: ``vwap_reclaimed
== True`` this bar requires ``price_vs_vwap == "above"`` this bar (the same
invariant that made the old condition impossible), which makes
``price_vs_vwap == "below"`` — required for a failed reclaim — false on
that same bar. No extra guard needed to enforce this; it is asserted by the
input contract and covered by a regression test.

Both the live path (webhook/state_builder.py — Pine sends vwap_reclaimed
directly, ta.crossover(close, vwap_val)) and the replay path
(replay/replay_engine.py::_market_state_from_candle — derives
vwap_reclaimed from consecutive candles' price_vs_vwap) already populate
MarketState.vwap.reclaimed and MarketState.vwap.price_vs_vwap correctly
before DecisionEngine.evaluate() runs; this module only needs those two
already-shared fields, so live and replay consume the identical formula
with no additional per-path wiring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, time as _time
from typing import Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def vwap_trading_day(ts: datetime) -> str:
    """CME trading-day key for the VWAP session containing ``ts``.

    Resets at 18:00 ET — the same anchor Pine's ta.vwap and this system's
    own replay VWAP use (see scripts/csv_to_replay.py::vwap_day_range,
    confirmed empirically against real Pine VWAP output). Deliberately NOT
    the naive calendar date: a "previous bar was reclaimed" fact from the
    prior VWAP session has no meaning against a freshly-reset VWAP anchor,
    so this state's day-boundary reset must track the true VWAP reset, not
    a UTC-midnight or other arbitrary boundary.
    """
    et = ts.astimezone(_ET)
    day = et.date() if et.time() >= _time(18, 0) else (et - timedelta(days=1)).date()
    return day.isoformat()


def advance_vwap_reclaim_state(
    *,
    price_vs_vwap: str,
    vwap_reclaimed: bool,
    trading_date: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, bool]:
    """Advance the one-bar VWAP-reclaim memory; report this bar's failed-reclaim status.

    ``price_vs_vwap`` / ``vwap_reclaimed`` describe the bar CURRENTLY being
    processed. Returns (next_state, is_failed_reclaim_this_bar).
    """
    previous = persisted_state or {}
    previous_reclaimed = (
        bool(previous.get("previous_bar_reclaimed", False))
        if previous.get("trading_date") == trading_date
        else False
    )

    is_failed_reclaim = previous_reclaimed and price_vs_vwap == "below"

    next_state = {
        "trading_date": trading_date,
        "previous_bar_reclaimed": bool(vwap_reclaimed),
    }
    return next_state, is_failed_reclaim
