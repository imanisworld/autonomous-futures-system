"""Offline executable-fill event realism helpers for options evidence.

Pure functions only. No provider fetch, no broker call, no environment/config
read, no journal write, and no runtime activation.

This module models the structural pieces that paper_sim alone does not express:
- pessimistic same-bar stop/target resolution,
- gap-through-stop classification,
- and first-available executable retained-quote selection after an exit trigger.

Fee/slippage policy values remain caller supplied/frozen elsewhere; this module
does not choose them.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Sequence

from options_manager.quotes.replay import quote_record_from_json_line

ExitReason = Literal["NONE", "TARGET", "STOP", "STOP_GAP", "INVALID"]
QuoteChoiceStatus = Literal["FOUND", "NO_FILL", "INVALID_DATA"]


@dataclass(frozen=True)
class UnderlyingBar:
    open: float
    high: float
    low: float


@dataclass(frozen=True)
class ExitTriggerResult:
    reason: ExitReason
    same_bar_both_hit: bool = False
    detail: str = ""

    @property
    def triggered(self) -> bool:
        return self.reason in {"TARGET", "STOP", "STOP_GAP"}


@dataclass(frozen=True)
class ExecutableQuoteChoice:
    status: QuoteChoiceStatus
    reason_code: str
    index: int | None = None
    payload: bytes | None = None


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def resolve_exit_trigger(
    *,
    direction: str,
    bar: UnderlyingBar,
    stop_level: object,
    target_level: object,
) -> ExitTriggerResult:
    """Resolve one underlying bar with stop-first pessimism.

    CALL means the underlying thesis is bullish: stop below, target above.
    PUT means the underlying thesis is bearish: stop above, target below.

    A bar that opens through the stop is STOP_GAP. If both target and stop are
    touched in the same bar without a stop gap, STOP wins.
    """

    side = str(direction or "").upper()
    if side not in {"CALL", "PUT"}:
        return ExitTriggerResult("INVALID", detail="invalid_direction")

    values = (bar.open, bar.high, bar.low, stop_level, target_level)
    if not all(_finite_number(value) for value in values):
        return ExitTriggerResult("INVALID", detail="nonfinite_level_or_bar")
    if bar.high < bar.low:
        return ExitTriggerResult("INVALID", detail="bar_high_below_low")

    stop = float(stop_level)
    target = float(target_level)
    if stop <= 0 or target <= 0:
        return ExitTriggerResult("INVALID", detail="nonpositive_level")

    if side == "CALL":
        if float(bar.open) <= stop:
            return ExitTriggerResult("STOP_GAP", detail="open_through_stop")
        stop_hit = float(bar.low) <= stop
        target_hit = float(bar.high) >= target
    else:
        if float(bar.open) >= stop:
            return ExitTriggerResult("STOP_GAP", detail="open_through_stop")
        stop_hit = float(bar.high) >= stop
        target_hit = float(bar.low) <= target

    if stop_hit and target_hit:
        return ExitTriggerResult(
            "STOP",
            same_bar_both_hit=True,
            detail="same_bar_both_hit_stop_first",
        )
    if stop_hit:
        return ExitTriggerResult("STOP", detail="stop_touched")
    if target_hit:
        return ExitTriggerResult("TARGET", detail="target_touched")
    return ExitTriggerResult("NONE", detail="no_exit_level_touched")


def first_executable_retained_quote(
    payloads: Sequence[bytes],
) -> ExecutableQuoteChoice:
    """Choose the first retained quote that is actually executable.

    Non-OK retained rows are preserved evidence but are not fills. The function
    never reconstructs or substitutes a quote. Malformed frozen bytes fail
    closed as INVALID_DATA rather than being skipped.
    """

    if not payloads:
        return ExecutableQuoteChoice("NO_FILL", "no_quote_after_trigger")

    for index, payload in enumerate(payloads):
        try:
            record = quote_record_from_json_line(payload)
        except ValueError:
            return ExecutableQuoteChoice(
                "INVALID_DATA",
                "retained_quote_parse_failed",
                index=index,
            )
        if record.status == "OK":
            return ExecutableQuoteChoice(
                "FOUND",
                "first_executable_quote",
                index=index,
                payload=payload,
            )

    return ExecutableQuoteChoice("NO_FILL", "no_executable_quote_after_trigger")
