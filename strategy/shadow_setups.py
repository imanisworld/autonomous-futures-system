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


@dataclass(frozen=True)
class ShadowOutcome:
    """Read-only resolution of a shadow candidate against forward price action.

    result is one of:
      WIN     — target reached after a fill
      LOSS    — stop reached after a fill
      NO_FILL — entry level never traded within the forward window
      OPEN    — filled but neither stop nor target reached by window end
    """

    result: str
    entry_filled: bool
    exit_reason: str | None
    exit_price: float | None
    pnl_ticks: float | None
    bars_to_fill: int | None
    bars_to_exit: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_shadow_candidate(
    candidate: ShadowSetupCandidate,
    forward_bars: list[tuple[float, float]],
    *,
    instrument: str,
    pessimistic_both_hit: bool = True,
) -> ShadowOutcome:
    """Resolve a shadow candidate against forward (high, low) bars — read-only.

    Models entry-fill realism: the entry is a resting level that only fills when
    a forward bar's range trades through it. This is deliberately stricter than
    the executable PaperBroker path (which assumes the entry always fills) because
    these are limit/stop entries that frequently never fill — measuring them as
    always-filled reproduces the unfillable-fill fiction this lane exists to avoid.

    On the bar that straddles both stop and target, intrabar order is unknowable;
    ``pessimistic_both_hit`` resolves it as the STOP (worst case). The same applies
    on the fill bar itself when it also straddles the stop.
    """
    tick = TICK_SIZE.get(instrument, 0.25)
    is_long = candidate.direction == "LONG"
    entry = candidate.entry
    stop = candidate.stop
    target = candidate.target

    fill_idx: int | None = None
    for i, (high, low) in enumerate(forward_bars):
        if low <= entry <= high:
            fill_idx = i
            break

    if fill_idx is None:
        return ShadowOutcome(
            result="NO_FILL",
            entry_filled=False,
            exit_reason="NO_FILL",
            exit_price=None,
            pnl_ticks=None,
            bars_to_fill=None,
            bars_to_exit=None,
        )

    for j in range(fill_idx + 1, len(forward_bars)):
        high, low = forward_bars[j]
        if is_long:
            target_hit = high >= target
            stop_hit = low <= stop
        else:
            target_hit = low <= target
            stop_hit = high >= stop

        if target_hit and stop_hit:
            won = not pessimistic_both_hit
        elif target_hit:
            won = True
        elif stop_hit:
            won = False
        else:
            continue

        exit_price = target if won else stop
        pnl_ticks = ((exit_price - entry) if is_long else (entry - exit_price)) / tick
        return ShadowOutcome(
            result="WIN" if won else "LOSS",
            entry_filled=True,
            exit_reason="TARGET_HIT" if won else "STOP_HIT",
            exit_price=round(exit_price, 4),
            pnl_ticks=round(pnl_ticks, 2),
            bars_to_fill=fill_idx + 1,
            bars_to_exit=j + 1,
        )

    return ShadowOutcome(
        result="OPEN",
        entry_filled=True,
        exit_reason="EOD_OPEN",
        exit_price=None,
        pnl_ticks=None,
        bars_to_fill=fill_idx + 1,
        bars_to_exit=None,
    )


TICK_SIZE = {
    "MNQ": 0.25,
    "MES": 0.25,
    "ES": 0.25,
    "NQ": 0.25,
    "MGC": 0.10,
    "MCL": 0.01,
}


RISK_MATRIX = {
    "strat_22_continuation_observed": ("B", 0.5),
    "strat_22_reversal_observed": ("B", 0.5),
    "strat_312_observed": ("B", 0.5),
    "strat_322_reversal_observed": ("B", 0.5),
    "strat_122_observed": ("B", 0.5),
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
        _missing_strat_family(state),
        _strat_122_pullback(state),
        _orb_false_break_fade(state),
        _overnight_sweep_reclaim(state),
        _gap_fill(state),
        _ema_pullback_trend(state),
    ]
    return [candidate for candidate in candidates if candidate is not None]


def _missing_strat_family(state: MarketState) -> ShadowSetupCandidate | None:
    """Journal PDF-defined Strat families that are not executable strategies."""
    strat = state.strat
    sequence = getattr(strat, "strat_sequence", None)
    direction = getattr(strat, "strat_direction", None)
    names = {
        "strat_22_continuation": "strat_22_continuation_observed",
        "strat_22_reversal": "strat_22_reversal_observed",
        "strat_312": "strat_312_observed",
        "strat_322_reversal": "strat_322_reversal_observed",
    }
    if sequence not in names or direction not in {"LONG", "SHORT"}:
        return None
    raw = state.raw if isinstance(state.raw, dict) else {}
    tick = _tick(state)
    previous_high = _raw_num(state, "previous_bar_high")
    previous_low = _raw_num(state, "previous_bar_low")
    if previous_high is None or previous_low is None:
        return None
    if direction == "LONG":
        entry, stop = previous_high + tick, previous_low - tick
        risk = entry - stop
        target = entry + (risk * 2)
    else:
        entry, stop = previous_low - tick, previous_high + tick
        risk = stop - entry
        target = entry - (risk * 2)
    if risk <= 0:
        return None
    return _candidate(
        strategy=names[sequence],
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        notes=(
            f"Shadow: PDF-defined {sequence}; trigger at prior-bar break, "
            "invalidation beyond the opposite side; evidence-only"
        ),
    )


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
        structural_risk = breakout_entry - structural_stop
        if structural_risk <= max_risk:
            return _candidate(
                strategy="strat_122_observed",
                direction=direction,
                entry=breakout_entry,
                stop=structural_stop,
                target=breakout_entry + (structural_risk * 2.0),
                notes=(
                    "Shadow: normal-width classified 1-2-2 structural bracket; "
                    "observe-only until resolved evidence earns promotion"
                ),
            )
        pullback_entry = structural_stop + max_risk
        target = pullback_entry + (max_risk * 2.0)
    else:
        breakout_entry = state.ohlc.low - tick
        structural_stop = state.ohlc.high + (tick * 4)
        structural_risk = structural_stop - breakout_entry
        if structural_risk <= max_risk:
            return _candidate(
                strategy="strat_122_observed",
                direction=direction,
                entry=breakout_entry,
                stop=structural_stop,
                target=breakout_entry - (structural_risk * 2.0),
                notes=(
                    "Shadow: normal-width classified 1-2-2 structural bracket; "
                    "observe-only until resolved evidence earns promotion"
                ),
            )
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
