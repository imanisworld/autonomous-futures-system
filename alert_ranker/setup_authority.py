"""Mechanical setup authority for the causal options lane.

Nothing here decides what a 2-1-2 is. This module adapts completed bars into
the shape :mod:`options_manager.scanner` already expects and returns that
scanner's own TRIGGERED/WATCH/INVALID/NO_TRADE verdict unchanged, so the
options lane and the futures lane cannot drift apart on what a setup is, and
this lane cannot quietly adopt a looser rule of its own.

The rule this enforces, stated plainly: **a candle type is not a setup.** An
ordinary 1, 2U, 2D or 3 candle is context. Only a TRIGGERED verdict is
actionable, and TRIGGERED requires the entry trigger, invalidation, targets,
market context and contract constraints all to be proven. This Phase 1 lane
supplies only the two mechanical levels that come straight out of the bars --
the previous candle's high and low -- and invents none of the rest. A genuine
2-1-2 therefore reports as INVALID with the strategy layer's own
``missing_target_1`` reason code: the sequence is recorded, and it cannot be
promoted into an alert. That is a structural property of this wiring, not a
convention that a later edit could quietly relax.

Only the 2-1-2 continuation path is evaluated, because it is the only
mechanical setup the strategy layer currently implements. A 3-1-2 or any other
sequence reports NO_TRADE here rather than being approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from options_manager.scanner import WatchlistRow, scan_watchlist_strat_212
from options_manager.strategies import Strat212Bars, strat_212_mechanical_levels
from strategy.strat_classifier import TWO_DOWN, TWO_UP, StratBar, classify_bar

from .causal_bars import Bar

__all__ = ["SetupVerdict", "evaluate_setup", "MINIMUM_BARS"]

# Three bars form the setup; a fourth, earlier bar is what makes the first of
# those three classifiable at all.
MINIMUM_BARS = 4

_DIRECTIONS = ("CALL", "PUT")

# Reason codes the strategy layer raises *before* it has recognised a
# directional 2-1-2, i.e. "there is no such sequence here", as distinct from
# "the sequence is real but its proof is incomplete".
_NO_SEQUENCE_REASONS = frozenset(
    {"sequence_not_212", "direction_mismatch", "setup_forming_not_triggered"}
)


@dataclass(frozen=True)
class SetupVerdict:
    """What the setup authority said, and whether it may be acted on."""

    status: str
    reason_code: str
    sequence_confirmed: bool = False
    direction: str | None = None
    entry_trigger: float | None = None
    invalidation: float | None = None

    @property
    def actionable(self) -> bool:
        """TRIGGERED is the only state that may progress toward an alert."""
        return self.status == "TRIGGERED"

    @property
    def suppression_reason(self) -> str:
        """Why this scan may not alert, or ``""`` when it may.

        Named rather than collapsed, so the shadow journal can separate "no
        setup existed" from "a setup existed and we could not prove it".
        """
        if self.actionable:
            return ""
        if self.status == "WATCH":
            return "setup_forming"
        if self.sequence_confirmed:
            return f"setup_proof_incomplete:{self.reason_code}"
        return f"no_setup:{self.reason_code}"


def _priority(verdict: SetupVerdict) -> int:
    if verdict.actionable:
        return 3
    if verdict.sequence_confirmed:
        return 2
    if verdict.status == "WATCH":
        return 1
    return 0


def evaluate_setup(
    bars: Sequence[Bar], *, ticker: str = "", timestamp: str = ""
) -> SetupVerdict:
    """Ask the shared strategy authority whether these bars form a setup.

    Pure: no I/O, no broker call, no alert. ``bars`` must be completed,
    regular-session, session-ordered bars on the canonical timeframe.
    """
    if len(bars) < MINIMUM_BARS:
        return SetupVerdict("INVALID", "insufficient_bars")

    current, previous, two_back, three_back = bars[-1], bars[-2], bars[-3], bars[-4]
    two_back_type = classify_bar(
        StratBar(high=two_back.high, low=two_back.low),
        StratBar(high=three_back.high, low=three_back.low),
    )
    if two_back_type not in (TWO_UP, TWO_DOWN):
        # A 2-1-2 opens on a directional bar. Anything else is a different
        # sequence, and this lane approximates no sequence it cannot evaluate.
        return SetupVerdict("NO_TRADE", "sequence_not_212")

    strat_bars = Strat212Bars(
        two_bars_back_type=two_back_type,
        two_bars_back_high=two_back.high,
        two_bars_back_low=two_back.low,
        previous_high=previous.high,
        previous_low=previous.low,
        current_high=current.high,
        current_low=current.low,
    )

    best: SetupVerdict | None = None
    for direction in _DIRECTIONS:
        # Mechanical price levels come from the shared options strategy helper;
        # this adapter does not maintain a second copy of the rule.
        entry, invalidation = strat_212_mechanical_levels(strat_bars, direction)
        report = scan_watchlist_strat_212(
            [
                WatchlistRow(
                    ticker=ticker,
                    timestamp=timestamp,
                    direction=direction,
                    bars=strat_bars,
                    entry_trigger=entry,
                    underlying_invalidation=invalidation,
                )
            ]
        )
        result = report.results[0]
        verdict = SetupVerdict(
            status=result.scan_status,
            reason_code=result.reason_code,
            sequence_confirmed=result.reason_code not in _NO_SEQUENCE_REASONS,
            direction=direction,
            entry_trigger=entry,
            invalidation=invalidation,
        )
        if best is None or _priority(verdict) > _priority(best):
            best = verdict

    assert best is not None  # _DIRECTIONS is never empty
    if _priority(best) == 0:
        # No direction produced anything; do not imply one was chosen.
        return SetupVerdict(best.status, best.reason_code)
    return best
