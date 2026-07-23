"""research/detector_4hr_retrigger.py

Pure, stateless detector for the "4HR Re-Trigger" strategy, per
docs/strategy-rules/4HR_ReTrigger_Rules.md and
docs/strategy-rules/Detector_Specifications.md (Detector 1).

This module has no strategy-engine, risk, broker, or execution imports. It takes
only historical bars and returns a signal dict or None. It does not place, size,
or authorize a trade — it is research/reconciliation evidence only.

The existing `_try_strat_4hr_retrigger` in strategy/signal_engine.py is a
self-labeled "Phase 1 approximation" using different logic (NY-open ORB-high
reclaim) — it is NOT the strategy specified here and is deliberately not reused.

Known open items (see the accompanying blocker report, not resolved by this
module): the QQQ manual-sample count conflicts between
docs/strategy-rules/4HR_ReTrigger_Rules.md (n=7) and
docs/strategy-rules/Strategy_Inventory.md (n=29); the actual dated manual-sample
list (for either instrument) does not exist anywhere in this repository.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_REQUIRED_BAR_KEYS = ("ts", "open", "high", "low", "close")


def _et_dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _usable_bar(bar: Any) -> bool:
    """A bar is usable for exact-timestamp lookup only if it is a dict with all
    required keys and a timezone-AWARE ts. Never raises — an unusable bar is
    simply excluded from matching, so one malformed row can't crash a batch
    reconciliation run. Missing/malformed inputs degrade to 'bar not found',
    which the rest of this detector already treats as 'return None'."""
    if not isinstance(bar, dict):
        return False
    if not all(k in bar for k in _REQUIRED_BAR_KEYS):
        return False
    ts = bar.get("ts")
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        return False
    for k in ("open", "high", "low", "close"):
        if not isinstance(bar[k], (int, float)) or isinstance(bar[k], bool):
            return False
    return True


def _find_exact(bars: list, target: datetime) -> Optional[dict]:
    """Return the bar whose open ts is exactly `target` (same instant — aware
    datetimes compare by absolute instant regardless of tzinfo), or None."""
    for bar in bars:
        if _usable_bar(bar) and bar["ts"] == target:
            return bar
    return None


def _prior_4pm_reference_date(eval_date: date, instrument: str) -> date:
    """Step 1 date arithmetic, exactly per spec — no holiday calendar invented.
    A calendar date that turns out not to have a 4PM bar in bars_4h (e.g. a
    market holiday) correctly falls through to 'bar not found -> None' below,
    rather than this function guessing a substitute session."""
    if eval_date.weekday() == 0:  # Monday
        if instrument.upper() == "QQQ":
            return eval_date - timedelta(days=3)  # prior Friday
        return eval_date - timedelta(days=1)  # Sunday (MNQ/MES)
    return eval_date - timedelta(days=1)  # Tue-Fri: previous calendar day


def _classify_vs_prior(high: float, low: float, prior_high: float, prior_low: float) -> str:
    """The four spec categories partition every (high, low) pair exactly once:
    high vs prior_high and low vs prior_low are each a two-way split, giving a
    clean 2x2 partition — CALLS/INSIDE/OUTSIDE/PUTS never overlap and never
    leave a gap. See the accompanying report for the derivation."""
    high_broke = high > prior_high
    low_broke = low < prior_low
    if not high_broke and low_broke:
        return "CALLS"
    if not high_broke and not low_broke:
        return "INSIDE"
    if high_broke and low_broke:
        return "OUTSIDE"
    return "PUTS"


def detect_4hr_retrigger(
    bars_4h: list,
    bars_5m: list,
    bars_1h: list,
    eval_date: date,
    instrument: str,
) -> Optional[dict]:
    """Pure, stateless. No I/O, no lookahead beyond what the caller passes in.

    Returns None when no setup can be evaluated at all (missing bar, no 2-step
    pattern, inside/outside bar, no retrace found). Returns a dict with
    signal=False and an explicit invalidation reason only for the one case the
    spec singles out as informative: the setup fully formed but price was
    already through the trigger at 9:30 AM.
    """
    if not isinstance(bars_4h, list) or not isinstance(bars_5m, list) or not isinstance(bars_1h, list):
        raise TypeError("bars_4h, bars_5m, and bars_1h must each be a list")

    instrument = str(instrument).upper()

    # ── Step 1 — prior 4PM candle ────────────────────────────────────────────
    prior_date = _prior_4pm_reference_date(eval_date, instrument)
    prior_4pm = _find_exact(bars_4h, _et_dt(prior_date, 16, 0))
    if prior_4pm is None:
        return None

    # ── Step 2 — 4AM candle ──────────────────────────────────────────────────
    four_am = _find_exact(bars_4h, _et_dt(eval_date, 4, 0))
    if four_am is None:
        return None

    # ── Step 3 — classify the 4AM candle ─────────────────────────────────────
    classification = _classify_vs_prior(
        four_am["high"], four_am["low"], prior_4pm["high"], prior_4pm["low"]
    )
    if classification not in ("CALLS", "PUTS"):
        return None
    calls = classification == "CALLS"

    # ── Step 4 — 8AM candle ───────────────────────────────────────────────────
    eight_am = _find_exact(bars_4h, _et_dt(eval_date, 8, 0))
    if eight_am is None:
        return None

    # ── Step 5 — classify the 8AM candle ──────────────────────────────────────
    if calls:
        if not (eight_am["high"] > four_am["high"]):
            return None
    else:
        if not (eight_am["low"] < four_am["low"]):
            return None

    # ── Step 6 — confirm retrace via 5-minute bars ────────────────────────────
    window_start = _et_dt(eval_date, 8, 0)
    window_end = _et_dt(eval_date, 9, 30)
    retrace_candidates = sorted(
        (b for b in bars_5m if _usable_bar(b) and window_start <= b["ts"] < window_end),
        key=lambda b: b["ts"],
    )
    retrace_confirmed = False
    for bar in retrace_candidates:
        if calls and bar["close"] < four_am["high"]:
            retrace_confirmed = True
            break
        if not calls and bar["close"] > four_am["low"]:
            retrace_confirmed = True
            break
    if not retrace_confirmed:
        return None

    # ── Step 7 — check 9:30 AM state ──────────────────────────────────────────
    nine_thirty = _find_exact(bars_5m, _et_dt(eval_date, 9, 30))
    if nine_thirty is None:
        # Consistent with the spec's fail-closed default elsewhere: no data to
        # determine the 9:30 state means no signal, not an assumed-valid state.
        return None
    if calls:
        state_ok = nine_thirty["open"] < four_am["high"]
    else:
        state_ok = nine_thirty["open"] > four_am["low"]
    if not state_ok:
        return {"signal": False, "invalidation": "PRICE_THROUGH_TRIGGER_AT_OPEN"}

    # ── Step 8 — stop reference from 1-hour bars ──────────────────────────────
    prior_1h = sorted(
        (b for b in bars_1h if _usable_bar(b) and b["ts"] < window_end),
        key=lambda b: b["ts"],
    )
    if not prior_1h:
        return None
    stop_bar = prior_1h[-1]
    stop_price = stop_bar["low"] if calls else stop_bar["high"]

    # ── Step 9 — return signal ────────────────────────────────────────────────
    entry_trigger = four_am["high"] if calls else four_am["low"]
    target = prior_4pm["high"] if calls else prior_4pm["low"]
    return {
        "signal": True,
        "direction": "LONG" if calls else "SHORT",
        "entry_trigger": entry_trigger,
        "stop_reference": stop_price,
        "stop_reference_bar_ts": stop_bar["ts"],
        "target": target,
        "setup_bar_ts": eight_am["ts"],
        "entry_window_open": _et_dt(eval_date, 9, 30),
        "entry_window_close": _et_dt(eval_date, 11, 0),
        "reference_candle_high": prior_4pm["high"],
        "reference_candle_low": prior_4pm["low"],
        "invalidation": None,
    }
