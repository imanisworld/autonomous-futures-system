"""
adaptive/shadow_runner.py

Read-only counterfactual generator for the shadow opportunity tracker (Phase 3).

It evaluates the SAME bar twice — once with schedule gates ENFORCED ("current")
and once with them BYPASSED ("always_on_shadow") — and, when the only difference
is the schedule (shadow would trade, current would not), records a SETUP_BLOCKED
OpportunityCandidate. It never submits an order and never mutates config; the two
engines differ ONLY in whether the session/window/cutoff gates are applied, so a
shadow-TRADE-but-current-no-trade is, by construction, a schedule-only miss.
"""
from __future__ import annotations

from typing import Optional

from context.market_context import MarketState
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine, DecisionOutput
from config.settings import SystemConfig
from adaptive.opportunity_tracker import (
    OpportunityCandidate, OpportunityStore, SETUP_BLOCKED,
)


def _snapshots(state: MarketState, shadow: DecisionOutput) -> dict:
    """Capture the decision context for reproducible later analysis."""
    def _d(obj, *attrs):
        out = {}
        for a in attrs:
            v = getattr(obj, a, None)
            if v is not None:
                out[a] = v
        return out

    snap: dict = {
        "regime": shadow.regime,
        "gex_status": shadow.gex_status,
        "signa_status": shadow.signa_status,
        "confidence_score": shadow.confidence_score,
        "market_condition": shadow.market_condition,
    }
    if state.trend:
        snap["trend"] = _d(state.trend, "direction", "strength", "ema_fast_above_slow")
    if state.vwap:
        snap["vwap"] = _d(state.vwap, "value", "price_vs_vwap", "reclaimed", "holding")
    if state.volume:
        snap["volume"] = _d(state.volume, "current_bar", "avg_bar", "relative")
    if state.orb:
        snap["orb"] = _d(state.orb, "high", "low", "status")
    return snap


def _candidate_from(
    state: MarketState, shadow: DecisionOutput, current: DecisionOutput
) -> OpportunityCandidate:
    setup = shadow.setup
    source_bar_id = state.timestamp.isoformat()
    timeframe = getattr(getattr(state, "ohlc", None), "timeframe", None) or "15"
    return OpportunityCandidate(
        candidate_id=OpportunityCandidate.make_id(
            state.instrument, source_bar_id, setup.strategy, setup.direction
        ),
        source_bar_id=source_bar_id,
        detected_at=state.timestamp.isoformat(),
        instrument=state.instrument,
        session=state.session,
        timeframe=str(timeframe),
        strategy=setup.strategy,
        direction=setup.direction,
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        # Context: what the CURRENT (enforced) path said when it blocked this bar.
        failed_gates=list(current.failed_gates or []),
        risk_failed_rule=None,
        market_condition=shadow.market_condition,
        block_type=SETUP_BLOCKED,   # shadow trades, current doesn't → schedule-only by construction
        multi_gate=len(current.failed_gates or []) > 1,
        snapshots=_snapshots(state, shadow),
        status="PENDING",
        expires_at=None,
    )


def evaluate_with_shadow(
    state: MarketState,
    daily_state: DailyState,
    config: SystemConfig,
    store: Optional[OpportunityStore] = None,
) -> Optional[OpportunityCandidate]:
    """Return a SETUP_BLOCKED candidate if this bar would have traded with the
    schedule removed but the current schedule blocked it; else None. Read-only.

    The two engines differ ONLY in schedule enforcement, so we always compare
    enforced-vs-bypassed regardless of the config's own schedule_mode.
    """
    current = DecisionEngine(config, schedule_mode="current").evaluate(state, daily_state)
    if current.decision == "TRADE":
        return None  # actually tradeable now — not a missed schedule opportunity

    shadow = DecisionEngine(config, schedule_mode="always_on_shadow").evaluate(state, daily_state)
    if shadow.decision != "TRADE" or shadow.setup is None:
        return None  # shadow also wouldn't trade → the block was NOT schedule-only

    candidate = _candidate_from(state, shadow, current)
    if store is not None:
        store.record_candidate(candidate)
    return candidate
