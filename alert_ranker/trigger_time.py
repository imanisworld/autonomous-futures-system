"""Pure trigger-time Strat observer for the options evidence lane.

This module separates setup formation from trigger detection. A completed
30-minute precursor arms fixed boundaries. Lower-timeframe bars may then prove
which boundary broke first during the immediately following 30-minute window.

It is intentionally research/advisory-only:
- no provider/network calls;
- no broker or order construction;
- no scanner/runtime activation;
- no mutation of the frozen cov-v0.1 observer evidence.

The purpose is to measure the actual Strat break instead of waiting for the
breakout 30-minute candle to finish and then applying the SIP-delay buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
)

from .causal_bars import MINUTE_30, Bar, Timeframe, bar_close

TRIGGER_TIMING_ID = "OPTIONS_STRAT_TRIGGER_TIMING"
TRIGGER_TIMING_VERSION = "trigger-v0.1"

BreakSide = Literal["HIGH", "LOW"]
TriggerStatus = Literal["TRIGGERED", "AMBIGUOUS", "NO_TRIGGER", "CANCELLED"]


@dataclass(frozen=True)
class ArmedStratTrigger:
    pattern: str
    armed_at: datetime
    watch_until: datetime
    boundary_high: float
    boundary_low: float
    reference_direction: str | None
    source_timeframe: str


@dataclass(frozen=True)
class TriggerResolution:
    status: TriggerStatus
    pattern: str
    family: str | None
    subtype: str | None
    direction: str | None
    break_side: BreakSide | None
    trigger_level: float | None
    invalidation_level: float | None
    trigger_bar_start: datetime | None
    trigger_bar_timeframe: str | None
    final_scenario: str
    opposite_side_broken_later: bool
    reason_code: str


def _bar_type(current: Bar, previous: Bar) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


def _color_direction(bar: Bar) -> str | None:
    if bar.close > bar.open:
        return TWO_UP
    if bar.close < bar.open:
        return TWO_DOWN
    return None


def arm_trigger_setup(
    completed_bars: Sequence[Bar],
    *,
    source_timeframe: Timeframe = MINUTE_30,
) -> ArmedStratTrigger | None:
    """Arm the next-bar trigger implied by the latest completed bar.

    The latest bar is the precursor/reference bar. Its high/low are frozen
    before the watched bar begins, so a later lower-timeframe cross cannot be
    retroactively manufactured.
    """

    if len(completed_bars) < 2:
        return None

    current = completed_bars[-1]
    previous = completed_bars[-2]
    current_type = _bar_type(current, previous)
    previous_type = None
    if len(completed_bars) >= 3:
        previous_type = _bar_type(previous, completed_bars[-3])

    pattern: str | None = None
    reference_direction: str | None = None

    if current_type == INSIDE_BAR:
        if previous_type in {TWO_UP, TWO_DOWN}:
            pattern = "212"
            reference_direction = previous_type
        elif previous_type == OUTSIDE_BAR:
            pattern = "312"
            reference_direction = _color_direction(previous)
    elif current_type in {TWO_UP, TWO_DOWN}:
        if previous_type in {TWO_UP, TWO_DOWN}:
            pattern = "222"
            reference_direction = current_type
        elif previous_type == OUTSIDE_BAR:
            pattern = "322"
            reference_direction = current_type
        elif previous_type == INSIDE_BAR:
            pattern = "122"
            reference_direction = current_type
    elif current_type == OUTSIDE_BAR:
        pattern = "32"
        reference_direction = _color_direction(current)

    if pattern is None:
        return None

    armed_at = bar_close(current, source_timeframe)
    return ArmedStratTrigger(
        pattern=pattern,
        armed_at=armed_at,
        watch_until=armed_at + source_timeframe.delta,
        boundary_high=float(current.high),
        boundary_low=float(current.low),
        reference_direction=reference_direction,
        source_timeframe=source_timeframe.name,
    )


def _direction_for_side(side: BreakSide) -> str:
    return TWO_UP if side == "HIGH" else TWO_DOWN


def _family_for_break(
    armed: ArmedStratTrigger,
    side: BreakSide,
) -> tuple[str | None, str | None, str | None]:
    current_direction = _direction_for_side(side)
    trade_direction = "LONG" if side == "HIGH" else "SHORT"
    reference = armed.reference_direction

    if armed.pattern == "212":
        subtype = (
            "CONTINUATION"
            if reference is not None and current_direction == reference
            else "REVERSAL"
        )
        return f"STRAT_212_{subtype}", subtype, trade_direction

    if armed.pattern == "312":
        subtype = None
        if reference is not None:
            subtype = "CONTINUATION" if current_direction == reference else "REVERSAL"
        return "STRAT_312", subtype, trade_direction

    if armed.pattern == "222":
        if reference is not None and current_direction == reference:
            return None, "CONTINUATION", None
        return "STRAT_222_REVERSAL", "REVERSAL", trade_direction

    if armed.pattern == "322":
        if reference is not None and current_direction == reference:
            return None, "CONTINUATION", None
        return "STRAT_322_REVERSAL", "REVERSAL", trade_direction

    if armed.pattern == "122":
        if reference is not None and current_direction == reference:
            return None, None, None
        return "OTHER:strat_122", "REVERSAL", trade_direction

    if armed.pattern == "32":
        subtype = None
        if reference is not None:
            subtype = "CONTINUATION" if current_direction == reference else "REVERSAL"
        family = f"STRAT_32_{subtype}" if subtype is not None else "STRAT_32"
        return family, subtype, trade_direction

    raise ValueError(f"unsupported armed pattern: {armed.pattern}")


def _final_scenario(armed: ArmedStratTrigger, bars: Sequence[Bar]) -> str:
    high = any(bar.high > armed.boundary_high for bar in bars)
    low = any(bar.low < armed.boundary_low for bar in bars)
    if high and low:
        return OUTSIDE_BAR
    if high:
        return TWO_UP
    if low:
        return TWO_DOWN
    return INSIDE_BAR


def resolve_trigger(
    armed: ArmedStratTrigger,
    lower_timeframe_bars: Sequence[Bar],
    *,
    lower_timeframe: Timeframe,
    watch_start: datetime | None = None,
    watch_until: datetime | None = None,
) -> TriggerResolution:
    """Resolve the first boundary break without inventing intrabar ordering.

    watch_start/watch_until may re-anchor the logical next canonical bar across
    a regular-session gap. Intraday callers can omit them.
    """

    start = armed.armed_at if watch_start is None else watch_start.astimezone(timezone.utc)
    end = armed.watch_until if watch_until is None else watch_until.astimezone(timezone.utc)
    if end <= start:
        raise ValueError("watch_until must be after watch_start")

    watched = sorted(
        (
            bar
            for bar in lower_timeframe_bars
            if start <= bar.start_utc < end
        ),
        key=lambda bar: bar.start_utc,
    )
    final_scenario = _final_scenario(armed, watched)

    for index, bar in enumerate(watched):
        high_break = bar.high > armed.boundary_high
        low_break = bar.low < armed.boundary_low

        if not high_break and not low_break:
            continue

        if high_break and low_break:
            return TriggerResolution(
                status="AMBIGUOUS",
                pattern=armed.pattern,
                family=None,
                subtype=None,
                direction=None,
                break_side=None,
                trigger_level=None,
                invalidation_level=None,
                trigger_bar_start=bar.start_utc,
                trigger_bar_timeframe=lower_timeframe.name,
                final_scenario=final_scenario,
                opposite_side_broken_later=False,
                reason_code="both_boundaries_crossed_same_lower_bar",
            )

        side: BreakSide = "HIGH" if high_break else "LOW"
        family, subtype, direction = _family_for_break(armed, side)

        if family is None:
            cancellation_reason = None
            if armed.pattern == "122":
                cancellation_reason = "same_direction_break_precludes_122_reversal"
            elif armed.pattern == "222":
                cancellation_reason = "same_direction_222_is_run_context_not_entry"
            elif armed.pattern == "322":
                cancellation_reason = "same_direction_322_continuation_not_approved"
            if cancellation_reason is not None:
                return TriggerResolution(
                    status="CANCELLED",
                    pattern=armed.pattern,
                    family=None,
                    subtype=subtype,
                    direction=None,
                    break_side=side,
                    trigger_level=(
                        armed.boundary_high if side == "HIGH" else armed.boundary_low
                    ),
                    invalidation_level=None,
                    trigger_bar_start=bar.start_utc,
                    trigger_bar_timeframe=lower_timeframe.name,
                    final_scenario=final_scenario,
                    opposite_side_broken_later=final_scenario == OUTSIDE_BAR,
                    reason_code=cancellation_reason,
                )

        later = watched[index + 1 :]
        opposite_later = (
            any(item.low < armed.boundary_low for item in later)
            if side == "HIGH"
            else any(item.high > armed.boundary_high for item in later)
        )
        trigger = armed.boundary_high if side == "HIGH" else armed.boundary_low
        invalidation = armed.boundary_low if side == "HIGH" else armed.boundary_high
        return TriggerResolution(
            status="TRIGGERED",
            pattern=armed.pattern,
            family=family,
            subtype=subtype,
            direction=direction,
            break_side=side,
            trigger_level=trigger,
            invalidation_level=invalidation,
            trigger_bar_start=bar.start_utc,
            trigger_bar_timeframe=lower_timeframe.name,
            final_scenario=final_scenario,
            opposite_side_broken_later=opposite_later,
            reason_code="first_boundary_break",
        )

    return TriggerResolution(
        status="NO_TRIGGER",
        pattern=armed.pattern,
        family=None,
        subtype=None,
        direction=None,
        break_side=None,
        trigger_level=None,
        invalidation_level=None,
        trigger_bar_start=None,
        trigger_bar_timeframe=lower_timeframe.name,
        final_scenario=final_scenario,
        opposite_side_broken_later=False,
        reason_code="no_boundary_break_in_watch_window",
    )
