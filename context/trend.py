"""
context/trend.py

Single source of truth for trend classification.

The whole system gates entries on trend strength. Historically the live path
(Pine `trend_str`, an EMA9/EMA21 percent-separation metric) and the replay path
(an EMA-stack ordering metric) used DIFFERENT definitions — so the same bars
produced opposite verdicts, and the live STRONG threshold (EMA spread > 0.40%)
turned out to be mathematically unreachable on 15m index micros (max observed
~0.22% MES / ~0.40% MNQ). Result: live could never fire a trade.

This module defines ONE scale-free definition used by BOTH live (state_builder)
and replay (csv_to_replay): EMA stack ordering. It does not depend on a magic
percentage that drifts with instrument or timeframe, so live and replay agree by
construction and the gate can't silently break on a chart change.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Public direction/strength vocabulary (matches context.market_context.TrendData).
_UP, _DOWN, _SIDEWAYS = "UP", "DOWN", "SIDEWAYS"
_STRONG, _MODERATE, _WEAK = "STRONG", "MODERATE", "WEAK"


def classify_trend(
    close: float,
    ema9: Optional[float],
    ema21: Optional[float],
    ema55: Optional[float],
) -> Tuple[str, str]:
    """Return (direction, strength) from the EMA stack.

    Scale-free: depends only on the ORDERING of price and the 9/21/55 EMAs, not
    on any absolute separation, so it behaves identically across instruments and
    timeframes.

        close > ema9 > ema21 > ema55   → UP,  STRONG    (full bullish stack)
        close < ema9 < ema21 < ema55   → DOWN, STRONG   (full bearish stack)
        close > ema21 and ema9 > ema21 → UP,  MODERATE
        close < ema21 and ema9 < ema21 → DOWN, MODERATE
        close > ema21                  → UP,  WEAK
        close < ema21                  → DOWN, WEAK
        otherwise                      → SIDEWAYS, WEAK

    Returns (SIDEWAYS, WEAK) if any EMA is missing — callers should fall back to
    a payload-provided trend only when this returns the neutral default.
    """
    if ema9 is None or ema21 is None or ema55 is None:
        return _SIDEWAYS, _WEAK

    c, e9, e21, e55 = float(close), float(ema9), float(ema21), float(ema55)

    if c > e9 > e21 > e55:
        return _UP, _STRONG
    if c < e9 < e21 < e55:
        return _DOWN, _STRONG
    if c > e21 and e9 > e21:
        return _UP, _MODERATE
    if c < e21 and e9 < e21:
        return _DOWN, _MODERATE
    if c > e21:
        return _UP, _WEAK
    if c < e21:
        return _DOWN, _WEAK
    return _SIDEWAYS, _WEAK


def moderate_subtype(
    close: float,
    ema9: Optional[float],
    ema21: Optional[float],
    ema55: Optional[float],
) -> Optional[str]:
    """Sub-classify a MODERATE trend bar into PULLBACK vs EARLY.

    A bar is MODERATE (not STRONG) for exactly one of two reasons — the full
    stack ordering is broken in one of two distinct ways:

        PULLBACK — the 9/21/55 stack is still fully ordered (ema9>ema21>ema55 up,
                   or ema9<ema21<ema55 down) but price pulled back through ema9
                   (close on the ema21 side of ema9). A dip inside a *confirmed*
                   trend.

        EARLY    — price and ema9 lead, but the slow ema55 hasn't flipped yet
                   (ema21 not yet beyond ema55). The trend is *forming*, not
                   confirmed.

    Returns "PULLBACK", "EARLY", or None (bar is not MODERATE / inputs missing).
    """
    if ema9 is None or ema21 is None or ema55 is None:
        return None

    c, e9, e21, e55 = float(close), float(ema9), float(ema21), float(ema55)

    direction, strength = classify_trend(c, e9, e21, e55)
    if strength != _MODERATE:
        return None

    if direction == _UP:
        stack_intact = e9 > e21 > e55
        return "PULLBACK" if stack_intact else "EARLY"
    if direction == _DOWN:
        stack_intact = e9 < e21 < e55
        return "PULLBACK" if stack_intact else "EARLY"
    return None


def has_ema_inputs(
    ema9: Optional[float], ema21: Optional[float], ema55: Optional[float]
) -> bool:
    """True when all three EMAs are present (so classify_trend is authoritative)."""
    return ema9 is not None and ema21 is not None and ema55 is not None
