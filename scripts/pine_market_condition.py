"""Pine-exact market_condition reconstruction for offline replay evidence.

MEASUREMENT/COMPARISON ONLY. This module does not feed the live replay
decision path -- replay/replay_engine.py continues to read
candle.market_condition/trend_direction/trend_strength exactly as before,
unchanged, and DecisionEngine's gates are untouched. This module computes
a SEPARATE, additively-wired set of `reconstructed_*` candle fields (see
scripts/csv_to_replay.py / scripts/polygon_to_replay.py) reproducing what
Pine's actual market_condition formula (tradingview/risksentinel_context.pine)
would have classified each historical bar as, using only the same OHLCV
already available in replay data -- for COMPARING against the existing
replay market_condition (itself a measurement artifact:
derive_market_condition() + a Python trend_str blend, unrelated to Pine's
real ATR/rel-vol formula), not for replacing it.

Reproduces, from tradingview/risksentinel_context.pine, exactly:

  Directional trend for market_condition (lines ~229-233 of the Pine
  source) -- the FULL EMA-9/21/55 stack only, three states:
      close > ema9 > ema21 > ema55  -> UP
      close < ema9 < ema21 < ema55  -> DOWN
      otherwise                     -> SIDEWAYS
  Deliberately NOT context.trend.classify_trend()'s richer
  UP/DOWN x STRONG/MODERATE/WEAK states -- conflating the two is exactly
  the ALREADY-KNOWN, SEPARATE trend divergence
  (see project_pine_parity_audit_2026_07_24); this module reconstructs
  Pine's own narrower definition, not Python's.

  ta.sma(volume, 20) -- exact current-inclusive 20-bar window (bars
  idx-19..idx, 20 values). The existing (unrelated, untouched) replay
  avg_volume field uses range(max(0, i-20), i+1), which is 21 bars once
  warm -- a different, pre-existing measurement artifact this module does
  not fix or touch, only avoids reproducing.

  ta.atr(14) -- true range + Wilder/RMA smoothing. Pine's ta.tr(true)
  substitutes high-low for the very first bar in the whole series (no prior
  close exists), so the TR series is defined starting at bar 0, and RMA(14)
  seeds as the simple average of TR[0..13] -- available at the 14th bar
  (index 13, 0-indexed), not index 14. Every ATR afterward is
  (prior_atr * 13 + this_bar_true_range) / 14. NOT a simple moving average
  of true range, and NOT seeded from TR[1..14] (an earlier version of this
  module silently dropped bar 0's true range and was off by one bar --
  caught by operator review, since the paired "independent" cross-check test
  originally shared the exact same wrong assumption and could not catch it).

  Market condition bucket order (Pine source, MARKET CONDITION block):
      rel_vol < 0.40                                -> DEAD
      range_ratio < 0.40 or rel_vol < 0.60           -> CHOPPY
      pine_trend != SIDEWAYS and rel_vol >= 0.80     -> TRENDING
      otherwise                                       -> RANGE_BOUND
  Exactly these 4 buckets -- CONSOLIDATING/UNKNOWN (from the existing,
  untouched derive_market_condition() heuristic) can never be a valid
  RECONSTRUCTED output.

Initialization / pre-roll honesty (RECONSTRUCTED vs
RECONSTRUCTED_UNVALIDATED_INIT):

  EMA and RMA are recursive filters -- their value on any given bar depends
  on the history feeding them. csv_to_replay.py passes Pine's OWN exported
  EMA9/21/55 columns when present: those are genuinely Pine-exact (Pine
  computed them from its own full chart history), no initialization
  question. But ATR14 is ALWAYS self-computed by this module -- neither the
  TradingView CSV export nor the Polygon feed provides a raw ATR column --
  and polygon_to_replay.py's EMA9/21/55 are ALSO self-computed (SMA-seeded
  from whichever bar the download happens to start on). Neither pipeline
  has a proven pre-roll/convergence window against Pine's own longer
  history, so a self-computed value is NOT provably equal to what Pine
  would show at that timestamp, even though the formula is correct.

  reconstruct_bar() therefore reports TWO independent statuses:
    - trend_status: RECONSTRUCTED when ema_source="pine_native" (Pine's own
      numbers); RECONSTRUCTED_UNVALIDATED_INIT when ema_source=
      "self_computed" (Polygon).
    - condition_status: always RECONSTRUCTED_UNVALIDATED_INIT when a
      condition is produced, because market_condition depends on ATR, which
      is always self-computed in both pipelines right now.
  RECONSTRUCTED_UNVALIDATED_INIT still carries the reconstructed value (it
  is usable, directionally-correct-formula evidence) -- it is not an
  UNAVAILABLE_* status -- but it must not be presented as proven
  Pine-equivalent parity until a deterministic pre-roll history + an
  overlapping Pine comparison proves convergence before the measured
  window (a separate, not-yet-authorized task).
"""

from __future__ import annotations

from typing import Optional, Sequence

RECONSTRUCTED = "RECONSTRUCTED"
RECONSTRUCTED_UNVALIDATED_INIT = "RECONSTRUCTED_UNVALIDATED_INIT"
UNAVAILABLE_WARMUP = "UNAVAILABLE_WARMUP"
UNAVAILABLE_SYNTHETIC_VOLUME = "UNAVAILABLE_SYNTHETIC_VOLUME"

_DEAD_REL_VOL = 0.40
_CHOPPY_RANGE_RATIO = 0.40
_CHOPPY_REL_VOL = 0.60
_TRENDING_REL_VOL = 0.80


def pine_trend_direction(
    close: Optional[float],
    ema9: Optional[float],
    ema21: Optional[float],
    ema55: Optional[float],
) -> Optional[str]:
    """Pine's own 3-state directional trend for market_condition (NOT
    context.trend.classify_trend()). None only when an input is missing."""
    if None in (close, ema9, ema21, ema55):
        return None
    if close > ema9 > ema21 > ema55:
        return "UP"
    if close < ema9 < ema21 < ema55:
        return "DOWN"
    return "SIDEWAYS"


def true_range(high: float, low: float, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr14_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[Optional[float]]:
    """Wilder/RMA ATR(14) for a full bar series, Pine ta.atr(14)-equivalent.

    Pine's ta.tr(true) substitutes high-low for bar 0 (no prior close
    exists yet), so the true-range series is defined starting at bar 0, not
    bar 1. RMA(14) seeds as the simple average of TR[0..13] (14 values),
    available at index 13 (0-indexed) -- the 14th bar -- NOT index 14.
    From index 14 onward, Wilder-smoothed: NOT a simple moving average of
    true range.
    """
    n = len(highs)
    out: list[Optional[float]] = [None] * n
    if n < 14:
        return out
    trs: list[float] = [0.0] * n
    trs[0] = true_range(highs[0], lows[0], None)
    for i in range(1, n):
        trs[i] = true_range(highs[i], lows[i], closes[i - 1])
    atr = sum(trs[0:14]) / 14.0
    out[13] = atr
    for i in range(14, n):
        atr = (atr * 13.0 + trs[i]) / 14.0
        out[i] = atr
    return out


def sma_series(values: Sequence[float], period: int) -> list[Optional[float]]:
    """Simple moving average, exact current-inclusive `period`-bar window.

    out[i] is None until index `period - 1` (the first bar with a full
    `period`-bar window behind it, inclusive of itself) -- no partial-window
    substitution, no off-by-one (the window is exactly `period` bars, not
    `period + 1`).
    """
    n = len(values)
    out: list[Optional[float]] = [None] * n
    running = 0.0
    for i in range(n):
        running += values[i]
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def reconstruct_market_condition(
    *, pine_trend: Optional[str], rel_vol: float, range_ratio: float
) -> str:
    """Pine's exact 4-bucket market_condition logic, in its own bucket
    order. Only ever returns one of DEAD/CHOPPY/TRENDING/RANGE_BOUND --
    structurally cannot produce CONSOLIDATING/UNKNOWN. Callers must ensure
    rel_vol/range_ratio are already valid (non-None, from real data) --
    availability gating is reconstruct_bar()'s responsibility, not this
    function's.
    """
    if rel_vol < _DEAD_REL_VOL:
        return "DEAD"
    if range_ratio < _CHOPPY_RANGE_RATIO or rel_vol < _CHOPPY_REL_VOL:
        return "CHOPPY"
    if pine_trend != "SIDEWAYS" and rel_vol >= _TRENDING_REL_VOL:
        return "TRENDING"
    return "RANGE_BOUND"


def reconstruct_bar(
    *,
    close: Optional[float],
    ema9: Optional[float],
    ema21: Optional[float],
    ema55: Optional[float],
    ema_source: str,
    high: float,
    low: float,
    atr14: Optional[float],
    rel_vol: Optional[float],
    volume_is_synthetic: bool,
) -> tuple[Optional[str], str, Optional[str], str]:
    """Reconstruct one bar's Pine-equivalent trend direction + market_condition.

    Returns (trend, trend_status, market_condition, condition_status).
    Trend and market_condition have genuinely independent provenance (see
    module docstring) and so carry INDEPENDENT statuses -- do not assume
    they're null together: a bar can have a fully Pine-exact trend
    (ema_source="pine_native") while its market_condition still carries
    RECONSTRUCTED_UNVALIDATED_INIT (ATR is always self-computed). Each
    status is one of RECONSTRUCTED / RECONSTRUCTED_UNVALIDATED_INIT /
    UNAVAILABLE_WARMUP / UNAVAILABLE_SYNTHETIC_VOLUME (the last is
    condition-only -- see below). A None value always pairs with an
    UNAVAILABLE_* status for that same output; a non-None value always
    pairs with a RECONSTRUCTED* status for that same output.

    ema_source: "pine_native" (Pine's own exported EMA9/21/55 columns -- no
    initialization question, genuinely Pine-exact) or "self_computed" (this
    pipeline's own recursive EMA, pre-roll/convergence against Pine's own
    longer history NOT proven).

    Synthetic-volume check happens FIRST and unconditionally for
    market_condition: a bar with fabricated volume (e.g. a TradingView CSV
    export missing its Volume column, defaulted to 1 -- see
    scripts/csv_to_replay.py's volume_synthetic detection) cannot support a
    Pine-equivalent relative-volume calculation at all, regardless of
    whether ATR warmup happens to be complete. It does not affect trend,
    which does not depend on volume.
    """
    if None in (close, ema9, ema21, ema55):
        trend, trend_status = None, UNAVAILABLE_WARMUP
    else:
        trend = pine_trend_direction(close, ema9, ema21, ema55)
        trend_status = (
            RECONSTRUCTED if ema_source == "pine_native" else RECONSTRUCTED_UNVALIDATED_INIT
        )

    if volume_is_synthetic:
        condition, condition_status = None, UNAVAILABLE_SYNTHETIC_VOLUME
    elif atr14 is None or atr14 <= 0 or rel_vol is None or trend is None:
        condition, condition_status = None, UNAVAILABLE_WARMUP
    else:
        range_ratio = (high - low) / atr14
        condition = reconstruct_market_condition(
            pine_trend=trend, rel_vol=rel_vol, range_ratio=range_ratio
        )
        # ATR14 is always self-computed in both pipelines right now (no
        # Pine-exported ATR column exists anywhere) -- market_condition can
        # never currently claim the plain, fully-proven RECONSTRUCTED tier.
        condition_status = RECONSTRUCTED_UNVALIDATED_INIT

    return trend, trend_status, condition, condition_status
