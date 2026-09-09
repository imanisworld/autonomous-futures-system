"""Pure daily Strat setup detection for the options paper-evidence lane.

This module is options-only and does not alter the shared futures classifier or
execution code.  It evaluates reconstructed regular-session daily candles and
returns only mechanical price-action facts.  Contract selection, risk, market
context and alerting remain downstream concerns.

Supported V1 daily populations:
- 2-1-2 continuation
- 2-2-2 continuation / reversal
- 3-2 developing WATCH
- 3-2-2 continuation / reversal

The current session candle may be partial: that is intentional for a daily
setup because the actionable event is the current day crossing the previous
day's high or low.  All prior candles must be completed regular-session
candles supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
)

from .causal_bars import Bar

DAILY_TIMEFRAME = "1D"


@dataclass(frozen=True)
class DailySetupVerdict:
    status: str
    reason_code: str
    setup_type: str | None = None
    sequence: str | None = None
    direction: str | None = None
    entry_trigger: float | None = None
    invalidation: float | None = None
    previous_high: float | None = None
    previous_low: float | None = None
    two_back_type: str | None = None
    previous_type: str | None = None
    current_type: str | None = None

    @property
    def triggered(self) -> bool:
        return self.status == "TRIGGERED"

    @property
    def watching(self) -> bool:
        return self.status == "WATCH"


def _bar_type(current: Bar, previous: Bar) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


def _directional_fields(current_type: str, previous: Bar) -> tuple[str, float, float]:
    if current_type == TWO_UP:
        return "CALL", previous.high, previous.low
    return "PUT", previous.low, previous.high


def evaluate_daily_setup(
    prior_completed_daily: Sequence[Bar],
    current_session_candle: Bar | None,
) -> DailySetupVerdict:
    """Classify the current daily setup without any look-ahead.

    Three completed prior daily candles are required so the oldest candle in
    the three-candle setup can itself be classified relative to an earlier
    completed day.  ``current_session_candle`` is reconstructed only from bars
    whose source intervals already closed at the caller's information cutoff.
    """
    if current_session_candle is None:
        return DailySetupVerdict("INVALID", "daily_current_candle_missing")
    if len(prior_completed_daily) < 3:
        return DailySetupVerdict("INVALID", "daily_history_insufficient")

    three_back, two_back, previous = prior_completed_daily[-3:]
    two_back_type = _bar_type(two_back, three_back)
    previous_type = _bar_type(previous, two_back)
    current_type = _bar_type(current_session_candle, previous)

    common = {
        "previous_high": previous.high,
        "previous_low": previous.low,
        "two_back_type": two_back_type,
        "previous_type": previous_type,
        "current_type": current_type,
    }

    # 3-2 is deliberately recorded while the current daily candle has not yet
    # broken either side of the directional 2.  Once both sides break it is a
    # 3-2-3, not a 3-2-2, so it leaves this population.
    if two_back_type == OUTSIDE_BAR and previous_type in {TWO_UP, TWO_DOWN}:
        if current_type == INSIDE_BAR:
            return DailySetupVerdict(
                "WATCH",
                "daily_32_developing",
                setup_type="DAILY_32_DEVELOPING",
                sequence="strat_32_developing",
                **common,
            )
        if current_type in {TWO_UP, TWO_DOWN}:
            direction, entry, invalidation = _directional_fields(current_type, previous)
            continuation = current_type == previous_type
            subtype = "CONTINUATION" if continuation else "REVERSAL"
            return DailySetupVerdict(
                "TRIGGERED",
                f"daily_322_{subtype.lower()}",
                setup_type=f"DAILY_322_{subtype}",
                sequence=f"strat_322_{subtype.lower()}",
                direction=direction,
                entry_trigger=entry,
                invalidation=invalidation,
                **common,
            )
        return DailySetupVerdict("NO_TRADE", "daily_323_not_in_population", **common)

    # Daily 2-1-2 is intentionally continuation-only for V1, matching the
    # existing approved 30m population.  The reversal variant remains context,
    # not an evidence candidate, until explicitly versioned into the policy.
    if (
        two_back_type in {TWO_UP, TWO_DOWN}
        and previous_type == INSIDE_BAR
        and current_type in {TWO_UP, TWO_DOWN}
    ):
        if current_type != two_back_type:
            return DailySetupVerdict("NO_TRADE", "daily_212_reversal_not_in_v1", **common)
        direction, entry, invalidation = _directional_fields(current_type, previous)
        return DailySetupVerdict(
            "TRIGGERED",
            "daily_212_continuation",
            setup_type="DAILY_212_CONTINUATION",
            sequence="strat_212",
            direction=direction,
            entry_trigger=entry,
            invalidation=invalidation,
            **common,
        )

    # A 2-2-2 requires the first two completed setup candles to both be
    # directional.  The third/current 2 determines continuation vs reversal
    # relative to the immediately previous daily 2.
    if (
        two_back_type in {TWO_UP, TWO_DOWN}
        and previous_type in {TWO_UP, TWO_DOWN}
        and current_type in {TWO_UP, TWO_DOWN}
    ):
        direction, entry, invalidation = _directional_fields(current_type, previous)
        continuation = current_type == previous_type
        subtype = "CONTINUATION" if continuation else "REVERSAL"
        return DailySetupVerdict(
            "TRIGGERED",
            f"daily_222_{subtype.lower()}",
            setup_type=f"DAILY_222_{subtype}",
            sequence=f"strat_222_{subtype.lower()}",
            direction=direction,
            entry_trigger=entry,
            invalidation=invalidation,
            **common,
        )

    return DailySetupVerdict("NO_TRADE", "daily_sequence_not_in_v1", **common)
