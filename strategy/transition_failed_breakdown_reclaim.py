"""Shared detector contract for Transition failed-breakdown reclaim.

This module contains signal identity only. It places no orders and changes no
permissions. The objective price/volume geometry is shared by shadow observation
and the isolated 400t/30m research candidate. The legacy shadow wrapper retains
its historical market-condition label filter; the research variant uses geometry
directly because the preserved-corpus parity audit proved the label representation
is not replay-portable while the geometry is exactly portable.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.futures_contracts import tick_size as contract_tick_size
from context.market_context import MarketState

RESEARCH_STRATEGY = "transition_failed_breakdown_reclaim_400t_30m"
RESEARCH_STOP_TICKS = 400.0
RESEARCH_HOLD_BARS = 6
RESEARCH_IOC_TOLERANCE_TICKS = 32.0
RESEARCH_DUMMY_TARGET_TICKS = 100_000.0


@dataclass(frozen=True)
class TransitionSignal:
    direction: str
    entry: float
    reference_stop: float
    reference_target: float
    notes: str


def _num(bar: dict, key: str) -> float:
    return float(bar.get(key) or 0.0)


def _volume(bar: dict) -> float | None:
    value = bar.get("volume")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bars_with_current(state: MarketState, bars: list[dict]) -> list[dict]:
    current_ts = state.timestamp.isoformat()
    current = {
        "ts": current_ts,
        "open": state.ohlc.open,
        "high": state.ohlc.high,
        "low": state.ohlc.low,
        "close": state.ohlc.close,
        "volume": state.volume.current_bar if state.volume else None,
    }
    if not bars:
        return [current]
    last = bars[-1]
    if (
        str(last.get("ts") or "") == current_ts
        or (
            abs(_num(last, "open") - state.ohlc.open) < 1e-9
            and abs(_num(last, "high") - state.ohlc.high) < 1e-9
            and abs(_num(last, "low") - state.ohlc.low) < 1e-9
            and abs(_num(last, "close") - state.ohlc.close) < 1e-9
        )
    ):
        return bars
    return [*bars, current]


def detect_transition_geometry(
    state: MarketState, bars: list[dict]
) -> TransitionSignal | None:
    """Return the objective Transition price/volume geometry, or None."""
    seq = _bars_with_current(state, bars)
    if len(seq) < 8:
        return None

    range_bars = seq[-8:-2]
    sweep = seq[-2]
    hold = seq[-1]
    tick = contract_tick_size(state.instrument)

    range_high = max(_num(b, "high") for b in range_bars)
    range_low = min(_num(b, "low") for b in range_bars)
    range_width = range_high - range_low
    avg_range = sum(_num(b, "high") - _num(b, "low") for b in range_bars) / len(range_bars)
    min_width = max(tick * 12, state.ohlc.close * 0.00045)
    if range_width < min_width or avg_range <= 0:
        return None

    sweep_low = _num(sweep, "low")
    sweep_close = _num(sweep, "close")
    hold_low = _num(hold, "low")
    hold_close = _num(hold, "close")
    hold_open = _num(hold, "open")

    if not (
        sweep_low <= range_low - tick
        and sweep_close >= range_low + tick
        and hold_low > sweep_low + tick
        and hold_close >= range_low + tick
    ):
        return None

    max_entry_distance = max(range_width * 0.45, tick * 4)
    if hold_close > range_low + max_entry_distance:
        return None

    sweep_range = _num(sweep, "high") - sweep_low
    hold_range = _num(hold, "high") - hold_low
    recent_volumes = [v for v in (_volume(b) for b in range_bars) if v is not None]
    avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else None
    sweep_volume = _volume(sweep)
    hold_volume = _volume(hold)
    volume_expanded = (
        avg_volume is not None
        and max(sweep_volume or 0.0, hold_volume or 0.0) >= avg_volume * 1.25
    )
    range_expanded = max(sweep_range, hold_range) >= avg_range * 1.15
    if not (volume_expanded or range_expanded):
        return None

    entry = hold_close
    reference_stop = sweep_low - (tick * 2)
    if entry - reference_stop <= 0:
        return None
    midpoint = (range_high + range_low) / 2.0
    reference_target = midpoint if midpoint > entry + tick else range_high
    if reference_target <= entry:
        return None

    close_quality = "green hold" if hold_close >= hold_open else "inside hold"
    return TransitionSignal(
        direction="LONG",
        entry=entry,
        reference_stop=reference_stop,
        reference_target=reference_target,
        notes=(
            "failed breakdown reclaim from RANGE/CHOP transition; "
            f"prior_range={range_low:.2f}-{range_high:.2f}, sweep_low={sweep_low:.2f}, "
            f"{close_quality}, expansion={'volume' if volume_expanded else 'range'}"
        ),
    )

def detect_transition_failed_breakdown_reclaim(
    state: MarketState, bars: list[dict]
) -> TransitionSignal | None:
    """Legacy shadow contract: geometry plus its historical condition label."""
    condition = str(state.market_condition or "").upper()
    if condition not in {"RANGE_BOUND", "CHOPPY", "TRANSITION"}:
        return None
    return detect_transition_geometry(state, bars)
