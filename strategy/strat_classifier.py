"""
strategy/strat_classifier.py

Deterministic Strat candle and simple sequence classification.
This module is read-only signal context; it does not place trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


INSIDE_BAR = "inside_bar"
TWO_UP = "two_up"
TWO_DOWN = "two_down"
OUTSIDE_BAR = "outside_bar"


@dataclass(frozen=True)
class StratBar:
    high: float
    low: float


@dataclass(frozen=True)
class StratContext:
    current_bar_type: Optional[str] = None
    previous_bar_type: Optional[str] = None
    two_bars_back_type: Optional[str] = None
    strat_sequence: Optional[str] = None
    strat_trigger: Optional[str] = None
    strat_direction: Optional[str] = None


def classify_bar(current: StratBar, previous: StratBar) -> str:
    """Classify one candle relative to the prior candle."""
    breaks_high = current.high > previous.high
    breaks_low = current.low < previous.low

    if breaks_high and breaks_low:
        return OUTSIDE_BAR
    if not breaks_high and not breaks_low:
        return INSIDE_BAR
    if breaks_high:
        return TWO_UP
    return TWO_DOWN


def classify_sequence(
    two_bars_back_type: Optional[str],
    previous_bar_type: Optional[str],
    current_bar_type: Optional[str],
) -> StratContext:
    """Classify simple three-candle Strat sequences."""
    sequence = None
    trigger = None
    direction = None

    if current_bar_type in (TWO_UP, TWO_DOWN):
        if previous_bar_type == INSIDE_BAR and two_bars_back_type == current_bar_type:
            sequence = "strat_212"
            trigger = "continuation"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif (
            two_bars_back_type == INSIDE_BAR
            and previous_bar_type in (TWO_UP, TWO_DOWN)
            and previous_bar_type != current_bar_type
        ):
            sequence = "strat_122"
            trigger = "reversal"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif previous_bar_type == INSIDE_BAR:
            sequence = "strat_inside_break"
            trigger = "breakout"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif previous_bar_type == OUTSIDE_BAR:
            sequence = "strat_outside_continuation"
            trigger = "outside_bar_followthrough"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"

    return StratContext(
        current_bar_type=current_bar_type,
        previous_bar_type=previous_bar_type,
        two_bars_back_type=two_bars_back_type,
        strat_sequence=sequence,
        strat_trigger=trigger,
        strat_direction=direction,
    )


def classify_from_ohlc(
    current_high: float,
    current_low: float,
    previous_high: Optional[float] = None,
    previous_low: Optional[float] = None,
    two_bars_back_high: Optional[float] = None,
    two_bars_back_low: Optional[float] = None,
    two_bars_back_type: Optional[str] = None,
) -> StratContext:
    """
    Classify current candle and, when enough history exists, a simple
    three-candle Strat sequence.

    If ``two_bars_back_high`` and ``two_bars_back_low`` are provided, the
    previous bar type is recomputed from OHLC. ``two_bars_back_type`` still
    comes from the caller because classifying that candle requires a third
    prior candle that this helper does not receive.
    """
    if previous_high is None or previous_low is None:
        return StratContext()

    current_type = classify_bar(
        StratBar(high=current_high, low=current_low),
        StratBar(high=previous_high, low=previous_low),
    )

    previous_type = None
    if two_bars_back_high is not None and two_bars_back_low is not None:
        previous_type = classify_bar(
            StratBar(high=previous_high, low=previous_low),
            StratBar(high=two_bars_back_high, low=two_bars_back_low),
        )

    return classify_sequence(
        two_bars_back_type=two_bars_back_type,
        previous_bar_type=previous_type,
        current_bar_type=current_type,
    )
