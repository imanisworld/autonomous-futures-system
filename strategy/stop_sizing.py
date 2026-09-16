"""Shared stop-width sizing.

Single source of truth for the per-instrument stop-width multiplier, used by
BOTH the live path (`webhook.runner.process_alert`) and the replay path
(`replay.replay_engine.ReplayEngine`). These two engines duplicate the
decision -> risk -> resolve pipeline; keeping the stop math here means a change
can never silently diverge between live and backtest (a recurring failure mode).

Tick sizes come ONLY from ``config/futures_contracts.py``. An unknown root
raises instead of inheriting a 0.25 tick, so live and replay can never round a
new instrument's stop onto the wrong grid.
"""

from __future__ import annotations

from typing import Any, Mapping

from config.futures_contracts import round_to_tick as _round_to_tick

__all__ = ["apply_stop_multiplier", "round_to_tick"]


def round_to_tick(price: float, instrument: str) -> float:
    """Round to the contract tick grid; raises ``UnsupportedContractError`` on unknown roots."""
    return _round_to_tick(price, instrument)


def apply_stop_multiplier(
    setup: Any, instrument: str, multiplier_map: Mapping[str, float] | None
) -> float:
    """Widen ``setup.stop`` (entry->stop risk) by the per-instrument multiplier.

    Mutates ``setup`` in place so the journal records the actual stop used: the
    stop is tick-rounded and ``rr_ratio`` recomputed against the (fixed) target.
    Returns the multiplier ACTUALLY applied (``1.0`` = no change). No-op when the
    multiplier is unset/1.0, the stop is missing, or risk is non-positive.
    """
    if getattr(setup, "strategy", None) in ("strat_4hr_retrigger", "strat_212", "strat_122"):
        # The resolved rule assigns a fixed causal boundary at actual entry
        # (4HR: last completed 1H boundary; 212/122: the prior reference
        # bar's opposite side) and fixes it forever. Generic widening would
        # change the strategy — and for a strat_212/122 pre_resolved
        # (same-bar-both-sides) candidate, would desync setup.stop from the
        # already-fixed exit price the caller journals as the actual P&L.
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
