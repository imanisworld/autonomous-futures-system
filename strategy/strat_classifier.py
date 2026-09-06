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
STRAT_212_REVERSAL = "strat_212_reversal"


# Canonical bar types use the long string forms above. Upstream sources speak
# other dialects: Pine emits the canonical names, but Polygon/CSV replay candle
# data stores numeric Strat codes (1=inside, 2=directional, 3=outside) and some
# CSV tooling uses "2U"/"2D". classify_sequence compares against the canonical
# constants, so any non-canonical code must be normalized first or sequences that
# depend on two_bars_back_type (1-2-2, 2-1-2, 3-1-2, 3-2-2) silently collapse to
# plain 2-2. Bare "2" stays as-is: it carries no direction, so it cannot be
# resolved to two_up/two_down here.
_BAR_TYPE_ALIASES = {
    "1": INSIDE_BAR,
    "3": OUTSIDE_BAR,
    "2u": TWO_UP,
    "2d": TWO_DOWN,
}


def normalize_bar_type(bar_type: Optional[str]) -> Optional[str]:
    """Map known bar-type dialects to canonical constants; pass others through."""
    if bar_type is None:
        return None
    return _BAR_TYPE_ALIASES.get(bar_type.strip().lower(), bar_type)


def _is_212_reversal(
    two_bars_back_type: Optional[str],
    previous_bar_type: Optional[str],
    current_bar_type: Optional[str],
) -> bool:
    """True for 2D-1-2U or 2U-1-2D, the canonical 2-1-2 reversal."""
    t2b = normalize_bar_type(two_bars_back_type)
    prev = normalize_bar_type(previous_bar_type)
    cur = normalize_bar_type(current_bar_type)
    return bool(
        prev == INSIDE_BAR
        and t2b in (TWO_UP, TWO_DOWN)
        and cur in (TWO_UP, TWO_DOWN)
        and t2b != cur
    )


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

    def __post_init__(self) -> None:
        """Correct the legacy Pine label for an unambiguous 2-1-2 reversal.

        TradingView historically labeled every otherwise-unmatched 1 -> 2 break
        as ``strat_inside_break``. When the bar before the inside bar is a
        directional 2 and the new 2 breaks the opposite way, that is specifically
        a 2-1-2 reversal. Normalize that stale payload identity here so live and
        replay consumers share the same canonical sequence without creating an
        execution path for the newly distinguished pattern.
        """
        if self.strat_sequence != "strat_inside_break":
            return
        if not _is_212_reversal(
            self.two_bars_back_type,
            self.previous_bar_type,
            self.current_bar_type,
        ):
            return
        current = normalize_bar_type(self.current_bar_type)
        object.__setattr__(self, "strat_sequence", STRAT_212_REVERSAL)
        object.__setattr__(self, "strat_trigger", "reversal")
        object.__setattr__(
            self,
            "strat_direction",
            "LONG" if current == TWO_UP else "SHORT",
        )


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
    two_bars_back_type = normalize_bar_type(two_bars_back_type)
    previous_bar_type = normalize_bar_type(previous_bar_type)
    current_bar_type = normalize_bar_type(current_bar_type)

    sequence = None
    trigger = None
    direction = None

    if current_bar_type in (TWO_UP, TWO_DOWN):
        if previous_bar_type in (TWO_UP, TWO_DOWN):
            if two_bars_back_type == OUTSIDE_BAR and previous_bar_type != current_bar_type:
                sequence = "strat_322_reversal"
                trigger = "reversal"
            elif two_bars_back_type == INSIDE_BAR and previous_bar_type != current_bar_type:
                sequence = "strat_122"
                trigger = "reversal"
            elif previous_bar_type == current_bar_type:
                sequence = "strat_22_continuation"
                trigger = "continuation"
            else:
                sequence = "strat_22_reversal"
                trigger = "reversal"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif previous_bar_type == INSIDE_BAR and two_bars_back_type == OUTSIDE_BAR:
            sequence = "strat_312"
            trigger = "breakout"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif previous_bar_type == INSIDE_BAR and two_bars_back_type == current_bar_type:
            sequence = "strat_212"
            trigger = "continuation"
            direction = "LONG" if current_bar_type == TWO_UP else "SHORT"
        elif _is_212_reversal(two_bars_back_type, previous_bar_type, current_bar_type):
            sequence = STRAT_212_REVERSAL
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
