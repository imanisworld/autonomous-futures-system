"""Shadow-only futures setup detectors.

These detectors answer "would this setup have appeared here?" without changing
the executable DecisionEngine strategy stack. They are for replay/live-paper
observation only: no candidate from this module can place, queue, or approve an
order by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from context.market_context import MarketState
from risk.risk_engine import RiskEngine


@dataclass(frozen=True)
class ShadowSetupCandidate:
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float
    rr_ratio: float
    risk_tier: str
    size_multiplier: float
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TICK_SIZE = {
    "MNQ": 0.25,
    "MES": 0.25,
    "ES": 0.25,
    "NQ": 0.25,
    "MGC": 0.10,
    "MCL": 0.01,
}


RISK_MATRIX = {
    "strat_122_pullback": ("B", 0.5),
    "orb_false_break_fade": ("B", 0.5),
    "ovn_high_sweep_reclaim": ("B", 0.5),
    "ovn_low_sweep_reclaim": ("B", 0.5),
    "gap_fill": ("C", 0.25),
    "ema_pullback_trend": ("B", 0.75),
}

# Mirrors the hard RiskEngine backstop for the instruments currently traded.
# This is deliberately local to the observe-only detector: changing it cannot
# loosen executable risk limits.
STRAT_122_MAX_STOP_TICKS = {
    "MNQ": 120,
    "MES": 60,
    "NQ": 60,
    "ES": 60,
}


def evaluate_shadow_setups(state: MarketState) -> list[ShadowSetupCandidate]:
    """Return all shadow-only setup candidates visible on this bar."""
    candidates = [
        _strat_122_pullback(state),
        _orb_false_break_fade(state),
        _overnight_sweep_reclaim(state),
        _gap_fill(state),
        _ema_pullback_trend(state),
    ]
    return [candidate for candidate in candidates if candidate is not None]


def _strat_122_pullback(state: MarketState) -> ShadowSetupCandidate | None:
    """Observe a stop-aware alternative when a classified 1-2-2 bar is too wide.

    The executable 1-2-2 uses the completed reversal bar's far side as structural
    invalidation.  That is correct structurally, but a large reversal candle can
    exceed the instrument risk cap.  Instead of tightening that stop or raising
    the global cap, record the nearest pullback entry that preserves the
    structural stop and fits the existing cap.  This candidate is journal-only;
    a later resolver must prove that the limit would fill and perform well.
    """
    strat = state.strat
    if not (
        strat
        and strat.strat_sequence == "strat_122"
        and strat.strat_direction in {"LONG", "SHORT"}
    ):
        return None

    max_ticks = STRAT_122_MAX_STOP_TICKS.get(state.instrument)
    if not max_ticks:
        return None
    tick = _tick(state)
    max_risk = tick * max_ticks
    direction = str(strat.strat_direction)

    if direction == "LONG":
        breakout_entry = state.ohlc.high + tick
        structural_stop = state.ohlc.low - (tick * 4)
        if breakout_entry - structural_stop <= max_risk:
            return None
        pullback_entry = structural_stop + max_risk
        target = pullback_entry + (max_risk * 2.0)
    else:
        breakout_entry = state.ohlc.low - tick
        structural_stop = state.ohlc.high + (tick * 4)
        if structural_stop - breakout_entry <= max_risk:
            return None
        pullback_entry = structural_stop - max_risk
        target = pullback_entry - (max_risk * 2.0)

    return _candidate(
        strategy="strat_122_pullback",
        direction=direction,
        entry=pullback_entry,
        stop=structural_stop,
        target=target,
        notes=(
            "Shadow: classified 1-2-2 signal bar exceeded the executable stop "
            f"cap ({max_ticks} ticks); preserve structural stop and require a "
            "pullback limit fill before measuring the 2R alternative"
        ),
    )


def _tick(state: MarketState) -> float:
    return TICK_SIZE.get(state.instrument, 0.25)


def _candidate(
    *,
    strategy: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    notes: str,
) -> ShadowSetupCandidate | None:
    rr = RiskEngine.calculate_rr(direction, entry, stop, target)
    if rr <= 0:
        return None
    risk_tier, size_multiplier = RISK_MATRIX.get(strategy, ("C", 0.25))
    return ShadowSetupCandidate(
        strategy=strategy,
        direction=direction,
        entry=round(entry, 4),
        stop=round(stop, 4),
        target=round(target, 4),
        rr_ratio=rr,
        risk_tier=risk_tier,
        size_multiplier=size_multiplier,
        notes=notes,
    )


def _raw_num(state: MarketState, key: str) -> float | None:
    raw = state.raw if isinstance(state.raw, dict) else {}
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _orb_false_break_fade(state: MarketState) -> ShadowSetupCandidate | None:
    """Fade failed ORB breaks on either side of the range."""
    status = state.orb.status
    tick = _tick(state)
    if status == "rejected_high":
        entry = state.orb.high - (tick * 2)
        stop = max(state.ohlc.high + (tick * 2), state.orb.high + (tick * 6))
        risk = stop - entry
        return _candidate(
            strategy="orb_false_break_fade",
            direction="SHORT",
            entry=entry,
            stop=stop,
            target=entry - (risk * 2.5),
            notes="Shadow: failed ORB high break faded back inside range",
        )
    if status == "rejected_low":
        entry = state.orb.low + (tick * 2)
        stop = min(state.ohlc.low - (tick * 2), state.orb.low - (tick * 6))
        risk = entry - stop
        return _candidate(
            strategy="orb_false_break_fade",
            direction="LONG",
            entry=entry,
            stop=stop,
            target=entry + (risk * 2.5),
            notes="Shadow: failed ORB low break faded back inside range",
        )
    return None


def _overnight_sweep_reclaim(state: MarketState) -> ShadowSetupCandidate | None:
    """RTH reclaim after sweeping overnight high/low."""
    if state.session != "new_york":
        return None
    onh = _raw_num(state, "overnight_high")
    onl = _raw_num(state, "overnight_low")
    tick = _tick(state)
    if onh is not None and state.ohlc.high > onh and state.ohlc.close < onh:
        entry = min(state.ohlc.close, onh - (tick * 2))
        stop = state.ohlc.high + (tick * 2)
        risk = stop - entry
        return _candidate(
            strategy="ovn_high_sweep_reclaim",
            direction="SHORT",
            entry=entry,
            stop=stop,
            target=entry - (risk * 2.2),
            notes="Shadow: swept overnight high, reclaimed below it during RTH",
        )
    if onl is not None and state.ohlc.low < onl and state.ohlc.close > onl:
        entry = max(state.ohlc.close, onl + (tick * 2))
        stop = state.ohlc.low - (tick * 2)
        risk = entry - stop
        return _candidate(
            strategy="ovn_low_sweep_reclaim",
            direction="LONG",
            entry=entry,
            stop=stop,
            target=entry + (risk * 2.2),
            notes="Shadow: swept overnight low, reclaimed above it during RTH",
        )
    return None


def _gap_fill(state: MarketState) -> ShadowSetupCandidate | None:
    """RTH gap-fill candidate toward previous RTH close."""
    if state.session != "new_york":
        return None
    rth_open = _raw_num(state, "rth_open")
    if rth_open is None:
        rth_open = _raw_num(state, "session_open")
    if rth_open is None:
        return None
    previous_close = state.previous_day.close
    tick = _tick(state)
    gap = rth_open - previous_close
    min_gap_points = 8.0
    if abs(gap) < min_gap_points:
        return None

    if gap > 0 and state.ohlc.close < rth_open:
        entry = state.ohlc.close
        stop = max(state.ohlc.high + (tick * 2), rth_open + (tick * 4))
        return _candidate(
            strategy="gap_fill",
            direction="SHORT",
            entry=entry,
            stop=stop,
            target=previous_close,
            notes="Shadow: gap-up failed to extend, targeting previous close fill",
        )
    if gap < 0 and state.ohlc.close > rth_open:
        entry = state.ohlc.close
        stop = min(state.ohlc.low - (tick * 2), rth_open - (tick * 4))
        return _candidate(
            strategy="gap_fill",
            direction="LONG",
            entry=entry,
            stop=stop,
            target=previous_close,
            notes="Shadow: gap-down failed to extend, targeting previous close fill",
        )
    return None


def _ema_pullback_trend(state: MarketState) -> ShadowSetupCandidate | None:
    """Trend pullback into EMA9/21 zone with resumption close."""
    kl = state.key_levels
    if kl is None or None in (kl.ema_9, kl.ema_21, kl.ema_55):
        return None
    ema9 = float(kl.ema_9)
    ema21 = float(kl.ema_21)
    ema55 = float(kl.ema_55)
    tick = _tick(state)

    if ema9 > ema21 > ema55:
        touched_zone = state.ohlc.low <= ema9 and state.ohlc.low >= ema21 - (tick * 4)
        resumed = state.ohlc.close > ema9
        if touched_zone and resumed:
            entry = state.ohlc.close
            stop = min(state.ohlc.low - (tick * 2), ema21 - (tick * 4))
            risk = entry - stop
            return _candidate(
                strategy="ema_pullback_trend",
                direction="LONG",
                entry=entry,
                stop=stop,
                target=entry + (risk * 2.2),
                notes="Shadow: bullish EMA stack pullback into EMA9/21 zone resumed",
            )

    if ema9 < ema21 < ema55:
        touched_zone = state.ohlc.high >= ema9 and state.ohlc.high <= ema21 + (tick * 4)
        resumed = state.ohlc.close < ema9
        if touched_zone and resumed:
            entry = state.ohlc.close
            stop = max(state.ohlc.high + (tick * 2), ema21 + (tick * 4))
            risk = stop - entry
            return _candidate(
                strategy="ema_pullback_trend",
                direction="SHORT",
                entry=entry,
                stop=stop,
                target=entry - (risk * 2.2),
                notes="Shadow: bearish EMA stack pullback into EMA9/21 zone resumed",
            )

    return None
