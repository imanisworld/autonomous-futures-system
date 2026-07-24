"""Shared stop-width sizing.

Single source of truth for the per-instrument stop-width multiplier, used by
BOTH the live path (`webhook.runner.process_alert`) and the replay path
(`replay.replay_engine.ReplayEngine`). These two engines duplicate the
decision -> risk -> resolve pipeline; keeping the stop math here means a change
can never silently diverge between live and backtest (a recurring failure mode).

The tick table matches `execution.paper_broker.TICK_SIZE` and the former
`webhook.runner._TICK_SIZE_BY_ROOT` (verified identical), so this is a faithful
extraction — the resulting stop is byte-identical to the prior inline blocks.
"""

from __future__ import annotations

from typing import Any, Mapping

# Canonical tick size per instrument root.
_TICK_SIZE: dict[str, float] = {
    "MNQ": 0.25,
    "MES": 0.25,
    "ES": 0.25,
    "NQ": 0.25,
    "MGC": 0.1,
    "MCL": 0.01,
}


def _root(instrument: str) -> str:
    return (instrument or "").upper().rstrip("!1234567890HMUZ")


def round_to_tick(price: float, instrument: str) -> float:
    tick = _TICK_SIZE.get(_root(instrument), 0.25)
    return round(round(price / tick) * tick, 4)


def apply_stop_multiplier(
    setup: Any, instrument: str, multiplier_map: Mapping[str, float] | None
) -> float:
    """Widen ``setup.stop`` (entry->stop risk) by the per-instrument multiplier.

    Mutates ``setup`` in place so the journal records the actual stop used: the
    stop is tick-rounded and ``rr_ratio`` recomputed against the (fixed) target.
    Returns the multiplier ACTUALLY applied (``1.0`` = no change). No-op when the
    multiplier is unset/1.0, the stop is missing, or risk is non-positive.
    """
    if getattr(setup, "strategy", None) == "strat_4hr_retrigger":
        # The resolved rule assigns the last completed 1H boundary at actual
        # entry and fixes it forever. Generic widening would change the strategy.
        return 1.0
    mult = (multiplier_map or {}).get(instrument, 1.0)
    if not mult or mult == 1.0 or getattr(setup, "stop", None) is None:
        return 1.0
    risk = abs(setup.entry - setup.stop)
    if risk <= 0:
        return 1.0
    raw = (
        setup.entry - mult * risk
        if setup.direction == "LONG"
        else setup.entry + mult * risk
    )
    setup.stop = round_to_tick(raw, instrument)
    new_risk = abs(setup.entry - setup.stop)
    if new_risk > 0:
        setup.rr_ratio = round(abs(setup.target - setup.entry) / new_risk, 2)
    return mult
