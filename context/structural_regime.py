"""Causal, observation-only structural market-regime classification.

This module deliberately has no knowledge of strategy routing, risk, brokers,
or the Pine/proxy ``market_condition`` label.  It classifies MES and MNQ with
the same swing-structure algorithm; instrument tick size only scales the noise
tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from context.bar_history import _parse_dt

TREND_UP = "STRUCTURAL_TREND_UP"
TREND_DOWN = "STRUCTURAL_TREND_DOWN"
RANGE_ACTIVE = "STRUCTURAL_RANGE_ACTIVE"
RANGE_DEAD = "STRUCTURAL_RANGE_DEAD"
TRANSITION = "STRUCTURAL_TRANSITION"
INSUFFICIENT = "INSUFFICIENT_DATA"

_TICK_SIZE = {"MES": 0.25, "MNQ": 0.25}


@dataclass(frozen=True)
class StructuralRegime:
    condition: str
    direction: str | None
    reason: str
    inputs: dict[str, Any]
    gate_authoritative: bool = False

    def to_dict(self, *, current_market_condition: str | None = None) -> dict[str, Any]:
        expected = None
        if self.condition in {TREND_UP, TREND_DOWN}:
            expected = {"TRENDING"}
        elif self.condition == RANGE_ACTIVE:
            expected = {"RANGE_BOUND"}
        elif self.condition == RANGE_DEAD:
            expected = {"CHOPPY", "DEAD"}
        return {
            "structural_market_condition": self.condition,
            "structural_direction": self.direction,
            "structural_mismatch": (
                None if expected is None else current_market_condition not in expected
            ),
            "structural_reason": self.reason,
            "structural_inputs": self.inputs,
            "structural_gate_authoritative": False,
        }


def classify_structural_regime(
    bars: list[dict[str, Any]],
    *,
    instrument: str,
    pivot_width: int = 2,
    lookback: int = 64,
) -> StructuralRegime:
    """Classify completed bars using confirmed pivots only.

    A pivot at index ``i`` is invisible until ``pivot_width`` bars to its right
    are present, which keeps every emitted classification causal.
    """
    clean = _clean_contiguous_tail(bars[-lookback:])
    tick = _TICK_SIZE.get(instrument.upper(), 0.25)
    base = {
        "pivot_width": pivot_width,
        "lookback_bars": len(clean),
        "swing_highs": [],
        "swing_lows": [],
        "median_true_range": None,
        "equality_tolerance": None,
        "range_high": None,
        "range_low": None,
        "touches_high": 0,
        "touches_low": 0,
        "rotations": 0,
        "overlap_ratio": None,
        "directional_efficiency": None,
        "active_range_trigger": None,
        "bar_gap_detected": len(clean) < len(bars[-lookback:]),
    }
    if len(clean) < max(10, (pivot_width * 2) + 4):
        return StructuralRegime(INSUFFICIENT, None, "not enough contiguous completed bars", base)

    true_ranges = _true_ranges(clean[-20:])
    if not true_ranges:
        return StructuralRegime(INSUFFICIENT, None, "true range unavailable", base)
    median_tr = median(true_ranges)
    tolerance = max(4 * tick, 0.20 * median_tr)
    highs, lows = _confirmed_pivots(clean, pivot_width)
    last_highs, last_lows = highs[-3:], lows[-3:]
    base.update(
        {
            "swing_highs": [round(item[1], 6) for item in last_highs],
            "swing_lows": [round(item[1], 6) for item in last_lows],
            "median_true_range": round(median_tr, 6),
            "equality_tolerance": round(tolerance, 6),
            "overlap_ratio": round(_overlap_ratio(clean[-20:]), 4),
            "directional_efficiency": round(_directional_efficiency(clean[-20:]), 4),
        }
    )
    if len(last_highs) < 3 or len(last_lows) < 3:
        return StructuralRegime(INSUFFICIENT, None, "fewer than three confirmed swing highs or lows", base)

    hs = [item[1] for item in last_highs]
    ls = [item[1] for item in last_lows]
    close = float(clean[-1]["close"])
    rising = _strictly_rising(hs, tolerance) and _strictly_rising(ls, tolerance)
    falling = _strictly_falling(hs, tolerance) and _strictly_falling(ls, tolerance)
    prior_swing_low, prior_swing_high = ls[-2], hs[-2]
    if rising and close >= prior_swing_low - tolerance:
        return StructuralRegime(TREND_UP, "UP", "three rising confirmed swing highs and lows", base)
    if falling and close <= prior_swing_high + tolerance:
        return StructuralRegime(TREND_DOWN, "DOWN", "three falling confirmed swing highs and lows", base)

    range_high = median(hs)
    range_low = median(ls)
    touches_high = sum(abs(value - range_high) <= tolerance for _, value, _ in highs[-6:])
    touches_low = sum(abs(value - range_low) <= tolerance for _, value, _ in lows[-6:])
    rotations = _rotations(highs[-6:], lows[-6:])
    width = range_high - range_low
    trigger = _active_trigger(clean[-1], range_high, range_low, tolerance)
    base.update(
        {
            "range_high": round(range_high, 6),
            "range_low": round(range_low, 6),
            "touches_high": touches_high,
            "touches_low": touches_low,
            "rotations": rotations,
            "active_range_trigger": trigger,
        }
    )
    stable = max(hs) - min(hs) <= 2 * tolerance and max(ls) - min(ls) <= 2 * tolerance
    if stable and touches_high >= 2 and touches_low >= 2 and rotations >= 3 and width >= 3 * median_tr and trigger:
        return StructuralRegime(RANGE_ACTIVE, None, "stable range with repeated rotations and an active boundary event", base)

    dead_flags = sum(
        (
            width < 2 * median_tr,
            base["overlap_ratio"] >= 0.65,
            base["directional_efficiency"] < 0.20,
            not stable,
        )
    )
    if dead_flags >= 2 and not trigger:
        return StructuralRegime(RANGE_DEAD, None, "overlapping low-efficiency price action without an active boundary event", base)
    return StructuralRegime(TRANSITION, None, "mixed or broken swing sequence", base)


def observe_structured_range_candidates(
    regime: StructuralRegime, bars: list[dict[str, Any]], *, instrument: str
) -> list[dict[str, Any]]:
    """Return additive observations only; never executable candidates."""
    if regime.condition != RANGE_ACTIVE or not bars:
        return []
    trigger = regime.inputs.get("active_range_trigger")
    direction = "LONG" if trigger in {"SWEEP_RECLAIM_LOW", "REJECT_LOW"} else "SHORT"
    strategy = "structured_sweep_reclaim_observed" if trigger and "SWEEP" in trigger else "structured_range_rejection_observed"
    return [
        {
            "strategy": strategy,
            "instrument": instrument,
            "direction": direction,
            "structural_market_condition": RANGE_ACTIVE,
            "range_high": regime.inputs.get("range_high"),
            "range_low": regime.inputs.get("range_low"),
            "trigger": trigger,
            "observation_only": True,
            "selected": False,
            "attempted": False,
            "risk_evaluated": False,
            "broker_evaluated": False,
        }
    ]


def _clean_contiguous_tail(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for bar in bars:
        try:
            stamp = _parse_dt(str(bar.get("ts") or ""))
            clean.append({**bar, "_dt": stamp, "open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]), "close": float(bar["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    clean = [bar for bar in clean if bar["_dt"] is not None]
    clean.sort(key=lambda bar: bar["_dt"])
    last_gap = -1
    for idx in range(1, len(clean)):
        if (clean[idx]["_dt"] - clean[idx - 1]["_dt"]).total_seconds() > 45 * 60:
            last_gap = idx - 1
    return clean[last_gap + 1 :]


def _confirmed_pivots(bars: list[dict[str, Any]], width: int):
    highs, lows = [], []
    for idx in range(width, len(bars) - width):
        window = bars[idx - width : idx + width + 1]
        high, low = bars[idx]["high"], bars[idx]["low"]
        if high == max(bar["high"] for bar in window) and sum(bar["high"] == high for bar in window) == 1:
            highs.append((idx, high, bars[idx].get("ts")))
        if low == min(bar["low"] for bar in window) and sum(bar["low"] == low for bar in window) == 1:
            lows.append((idx, low, bars[idx].get("ts")))
    return highs, lows


def _true_ranges(bars):
    out = []
    for idx, bar in enumerate(bars):
        previous = bars[idx - 1]["close"] if idx else bar["open"]
        out.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous), abs(bar["low"] - previous)))
    return [value for value in out if value > 0]


def _strictly_rising(values, tolerance):
    return values[0] + tolerance < values[1] and values[1] + tolerance < values[2]


def _strictly_falling(values, tolerance):
    return values[0] - tolerance > values[1] and values[1] - tolerance > values[2]


def _rotations(highs, lows):
    events = sorted([(idx, "H") for idx, *_ in highs] + [(idx, "L") for idx, *_ in lows])
    collapsed = []
    for _, kind in events:
        if not collapsed or collapsed[-1] != kind:
            collapsed.append(kind)
    return max(0, len(collapsed) - 1)


def _active_trigger(bar, high, low, tolerance):
    if bar["low"] < low - tolerance and bar["close"] > low:
        return "SWEEP_RECLAIM_LOW"
    if bar["high"] > high + tolerance and bar["close"] < high:
        return "SWEEP_RECLAIM_HIGH"
    if abs(bar["low"] - low) <= tolerance and bar["close"] > low + tolerance:
        return "REJECT_LOW"
    if abs(bar["high"] - high) <= tolerance and bar["close"] < high - tolerance:
        return "REJECT_HIGH"
    return None


def _overlap_ratio(bars):
    ratios = []
    for left, right in zip(bars, bars[1:]):
        overlap = max(0.0, min(left["high"], right["high"]) - max(left["low"], right["low"]))
        span = max(left["high"], right["high"]) - min(left["low"], right["low"])
        ratios.append(overlap / span if span else 1.0)
    return sum(ratios) / len(ratios) if ratios else 0.0


def _directional_efficiency(bars):
    closes = [bar["close"] for bar in bars]
    travel = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / travel if travel else 0.0
