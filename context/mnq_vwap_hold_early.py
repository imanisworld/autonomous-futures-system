"""MNQ vwap_hold early-signal detector (Phase 1, upstream-timing lane).

The moderate-detachment entry-refresh lane (context/mnq_entry_refresh.py,
PR #266) only recovers a stale 15-minute entry AFTER the fact, within
max_detachment_r. It structurally cannot help the extreme-latency incidents
found in docs/mnq-entry-refresh-study-2026-07-13.md (2.4R-45R detachment,
REJECTED_TARGET_PASSED) — those signals arrived correctly on the 15-minute
bar but the market had already moved past the target by the time that bar
closed. The only way to catch that class earlier is to look at it earlier:
a confirmed 5-minute vwap_hold alert, evaluated 10-14 minutes before the
corresponding 15-minute bar would close.

This module does NOT reimplement vwap_hold's entry/stop/target math or its
trend/regime/volume gates. It re-runs the REAL strategy.signal_engine
.DecisionEngine.evaluate() pipeline against a MarketState built from the
5-minute alert payload — the exact same gates (session window, market
condition/regime classification, trend direction, Strat confirmation, R:R)
the 15-minute pipeline enforces, byte for byte. Two isolation measures keep
this reuse side-effect-free against the live 15-minute decision flow:

  1. enabled_concepts is narrowed to ["vwap_hold"] only in the scoped config
     copy, so no other strategy's evaluation code runs (in particular,
     orb_breakout's daily_state.orb_break_{long,short}_played mutation at
     signal_engine.py:751-759 never executes for this call).
  2. daily_state is a throwaway dataclasses.replace() copy, never the live
     object, so even an unanticipated mutation elsewhere is discarded.

Requires the 5-minute TradingView alert for vwap_hold to actually exist and
be flowing to the webhook (same schema as the 15-minute alert: Pine computes
vwap/trend/strat at 5-minute resolution and sends it, exactly as
context/five_min_feed.py already assumes for its retest-trigger lane). This
module and its config flag being ON does not by itself produce any evidence
until FIVE_MIN_FEED_ENABLED is also true AND that 5-minute alert exists.

VALID_MODES: "off" (default, no-op), "observe_only" (audit dict only, no
shadow position), "shadow" (also opens a hypothetical position tracked by
execution.vwap_hold_early_shadow). Scope is fixed to MNQ + vwap_hold only —
no MES, no other strategy, no live/demo execution mode, per explicit scope.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Optional

from context.five_min_feed import is_five_min
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine

VALID_MODES = ("off", "observe_only", "shadow")
DEFAULT_MODE = "off"
SCOPE_INSTRUMENT = "MNQ"
SCOPE_STRATEGY = "vwap_hold"


def vwap_hold_early_mode(cfg=None) -> str:
    """Fails closed to 'off' on any missing/invalid config."""
    value = str(getattr(cfg, "vwap_hold_early_mode", None) or "").strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def _root(instrument: str) -> str:
    return (instrument or "").upper().replace("1!", "").strip()


def is_vwap_hold_early_candidate(instrument: str, timeframe: object, cfg=None) -> bool:
    """True only for MNQ + a 5-minute bar, with the lane explicitly enabled."""
    if vwap_hold_early_mode(cfg) == "off":
        return False
    if _root(instrument) != SCOPE_INSTRUMENT:
        return False
    return is_five_min(timeframe)


def _scoped_config(cfg):
    """A throwaway config copy with only vwap_hold enabled — see module
    docstring point 1. dataclasses.replace requires cfg to be a dataclass
    instance (SystemConfig is); falls back to the original cfg (fail-closed
    to 'no isolation guarantee, so treat as unavailable' is handled by the
    caller's try/except) if that assumption ever breaks."""
    return dataclasses.replace(cfg, enabled_concepts=[SCOPE_STRATEGY])


def _scoped_daily_state(daily_state: DailyState) -> DailyState:
    """A throwaway DailyState copy — see module docstring point 2."""
    return dataclasses.replace(daily_state)


def detect_early_vwap_hold(
    state,
    daily_state: DailyState,
    cfg,
) -> Optional[dict[str, Any]]:
    """Run the real decision pipeline on a 5-minute MarketState, isolated.

    Returns an audit dict always (never None) once called — "signal_detected"
    is the field callers branch on. Returns None only if evaluate() itself
    raises (defensive; DecisionEngine.evaluate() is documented to never
    raise, so this should not happen in practice).
    """
    try:
        scoped_cfg = _scoped_config(cfg)
        scoped_daily = _scoped_daily_state(daily_state)
        engine = DecisionEngine(scoped_cfg, schedule_mode=getattr(cfg, "schedule_mode", None))
        decision = engine.evaluate(state, scoped_daily)
    except Exception:
        return None

    signal_detected = (
        decision.decision == "TRADE"
        and decision.setup is not None
        and decision.setup.strategy == SCOPE_STRATEGY
    )
    audit: dict[str, Any] = {
        "signal_detected": signal_detected,
        "decision": decision.decision,
        "reason": decision.reason,
        "failed_gates": decision.failed_gates,
        "market_condition": decision.market_condition,
        "regime": decision.regime,
    }
    if signal_detected:
        setup = decision.setup
        audit.update({
            "direction": setup.direction,
            "entry": setup.entry,
            "stop": setup.stop,
            "target": setup.target,
            "rr_ratio": setup.rr_ratio,
        })
    return audit
