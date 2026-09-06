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
from strategy.strat_classifier import TWO_DOWN, normalize_bar_type

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
        canonical_setup = engine._try_vwap_hold(state)
        decision = engine.evaluate(state, scoped_daily)
    except Exception:
        return None

    permission_only_shadow = (
        decision.setup is not None
        and decision.setup.strategy == SCOPE_STRATEGY
        and set(decision.failed_gates or []) == {"STRATEGY_NOT_PAPER_ELIGIBLE"}
    )
    signal_detected = (
        decision.setup is not None
        and decision.setup.strategy == SCOPE_STRATEGY
        and (decision.decision == "TRADE" or permission_only_shadow)
    )
    audit: dict[str, Any] = {
        "signal_detected": signal_detected,
        "decision": decision.decision,
        "reason": decision.reason,
        "failed_gates": decision.failed_gates,
        "market_condition": decision.market_condition,
        "regime": decision.regime,
        "shadow_eligibility_basis": (
            "EXECUTION_PERMISSION_BLOCK_ONLY"
            if permission_only_shadow
            else "CANONICAL_TRADE" if signal_detected else "NOT_ELIGIBLE"
        ),
        "diagnostics": _diagnostics(state, engine, canonical_setup),
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


def _diagnostics(state, engine: DecisionEngine, canonical_setup) -> dict[str, Any]:
    """Expose inputs/gates without changing the canonical predicate."""
    raw = state.raw if isinstance(state.raw, dict) else {}
    required = {
        "vwap": raw.get("vwap") is not None,
        "trend_direction": (
            raw.get("trend_direction") is not None
            or all(raw.get(name) is not None for name in ("ema_9", "ema_21", "ema_55"))
        ),
        "current_bar_type": raw.get("current_bar_type") is not None,
        "volume": raw.get("volume") is not None,
        "avg_volume": raw.get("avg_volume") is not None,
    }
    builder_failed_gates: list[str] = []
    if not (state.vwap and state.vwap.holding and state.vwap.price_vs_vwap == "below"):
        builder_failed_gates.append("VWAP_NOT_HOLDING_BELOW")
    if not (state.trend and state.trend.direction == "DOWN"):
        builder_failed_gates.append("TREND_NOT_DOWN")
    try:
        if engine._vwap_entry_out_of_range(state):
            builder_failed_gates.append("VWAP_ENTRY_OUT_OF_RANGE")
    except Exception:
        builder_failed_gates.append("VWAP_RANGE_CHECK_UNAVAILABLE")
    strat_type = getattr(getattr(state, "strat", None), "current_bar_type", None)
    if state.strat and normalize_bar_type(strat_type) != TWO_DOWN:
        builder_failed_gates.append("STRAT_NOT_TWO_DOWN")
    structure = {
        "bos_direction": raw.get("bos_direction"),
        "mss_direction": raw.get("mss_direction"),
        "market_structure": raw.get("market_structure"),
    }
    supplied_structure = any(structure.values())
    bearish_structure = (
        str(structure["bos_direction"] or "").lower() == "bearish"
        or str(structure["mss_direction"] or "").lower() == "bearish"
        or str(structure["market_structure"] or "").lower() in {"bearish_bos", "bearish_mss"}
    )
    if supplied_structure and not bearish_structure:
        builder_failed_gates.append("STRUCTURE_NOT_BEARISH")
    return {
        "payload_complete_for_canonical_evaluator": all(required.values()),
        "required_input_fields": required,
        "missing_required_input_fields": [name for name, present in required.items() if not present],
        "candidate_builder_result": "BUILT" if canonical_setup is not None else "NO_CANDIDATE",
        "candidate_builder_failed_gates": builder_failed_gates,
        "session": state.session,
        "trend_direction": getattr(state.trend, "direction", None),
        "trend_strength": getattr(state.trend, "strength", None),
        "market_condition": state.market_condition,
        "strat_current_bar_type": strat_type,
        "strat_normalized_bar_type": normalize_bar_type(strat_type),
        "volume_relative": getattr(state.volume, "relative", None),
        "volume_gate_at_least_1_2": (
            getattr(state.volume, "relative", None) is not None
            and state.volume.relative >= 1.2
        ),
        "vwap_holding": getattr(state.vwap, "holding", None),
        "price_vs_vwap": getattr(state.vwap, "price_vs_vwap", None),
        "structure_inputs": structure,
        "rr_ratio": getattr(canonical_setup, "rr_ratio", None),
    }
