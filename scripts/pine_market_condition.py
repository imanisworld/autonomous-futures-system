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

  ta.atr(14) -- true range + Wilder/RMA smoothing: first ATR = simple
  average of the first 14 true-range values; every ATR afterward is
  (prior_atr * 13 + this_bar_true_range) / 14. NOT a simple moving average
  of true range.

  Market condition bucket order (Pine source, MARKET CONDITION block):
      rel_vol < 0.40                                -> DEAD
      range_ratio < 0.40 or rel_vol < 0.60           -> CHOPPY
      pine_trend != SIDEWAYS and rel_vol >= 0.80     -> TRENDING
      otherwise                                       -> RANGE_BOUND
  Exactly these 4 buckets -- CONSOLIDATING/UNKNOWN (from the existing,
  untouched derive_market_condition() heuristic) can never be a valid
  RECONSTRUCTED output.
"""

from __future__ import annotations

from typing import Optional, Sequence

RECONSTRUCTED = "RECONSTRUCTED"
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

    out[i] is None until bar 14 (0-indexed) -- the first bar with 14 full
    true-range values behind it (TR[1]..TR[14], each needing bar i-1's
    close, so the series needs indices 0..14 present before the first ATR
    is available). From bar 15 onward, Wilder-smoothed: NOT a simple
    moving average of true range.
    """
    n = len(highs)
    out: list[Optional[float]] = [None] * n
    if n < 15:
        return out
    trs: list[float] = [0.0] * n
    for i in range(1, n):
        trs[i] = true_range(highs[i], lows[i], closes[i - 1])
    atr = sum(trs[1:15]) / 14.0
    out[14] = atr
    for i in range(15, n):
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
    high: float,
    low: float,
    atr14: Optional[float],
    rel_vol: Optional[float],
    volume_is_synthetic: bool,
) -> tuple[Optional[str], Optional[str], str]:
    """Reconstruct one bar's Pine-equivalent trend direction + market_condition.

    Returns (trend_direction_or_None, market_condition_or_None, status).
    status is one of RECONSTRUCTED / UNAVAILABLE_WARMUP /
    UNAVAILABLE_SYNTHETIC_VOLUME. Both outputs are None together whenever
    status != RECONSTRUCTED -- a bar's reconstruction is either fully valid
    Pine-equivalent evidence or not usable at all, never partially (e.g.
    never a real trend paired with a fabricated market_condition bucket).

    Synthetic-volume check happens FIRST and unconditionally: a bar with
    fabricated volume (e.g. a TradingView CSV export missing its Volume
    column, defaulted to 1 -- see scripts/csv_to_replay.py's
    is_synthetic_volume()) cannot support a Pine-equivalent relative-volume
    calculation at all, regardless of whether EMA/ATR warmup happens to be
    complete.
    """
    if volume_is_synthetic:
        return None, None, UNAVAILABLE_SYNTHETIC_VOLUME
    if atr14 is None or atr14 <= 0 or rel_vol is None:
        return None, None, UNAVAILABLE_WARMUP
    if None in (close, ema9, ema21, ema55):
        return None, None, UNAVAILABLE_WARMUP
    pine_trend = pine_trend_direction(close, ema9, ema21, ema55)
    range_ratio = (high - low) / atr14
    condition = reconstruct_market_condition(
        pine_trend=pine_trend, rel_vol=rel_vol, range_ratio=range_ratio
    )
    return pine_trend, condition, RECONSTRUCTED
