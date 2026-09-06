"""Shared mechanical price levels for options strategy proof.

The Strat entry rule is not a tunable input: for the current 2-1-2 authority a
CALL triggers on the inside bar's high and invalidates at its low; a PUT
triggers on the inside bar's low and invalidates at its high. Keeping that
mapping here lets the causal alert adapter and the canonical plan-proof bridge
consume one authority instead of each copying the rule.

Pure only: no market fetch, broker, alert, storage, config, or execution I/O.
"""

from __future__ import annotations

from typing import Literal

from .strat_212 import Strat212Bars


def strat_212_mechanical_levels(
    bars: Strat212Bars, direction: Literal["CALL", "PUT"]
) -> tuple[float, float]:
    """Return ``(entry_trigger, underlying_invalidation)`` from the inside bar."""

    if direction == "CALL":
        return bars.previous_high, bars.previous_low
    if direction == "PUT":
        return bars.previous_low, bars.previous_high
    raise ValueError("direction must be CALL or PUT")
