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
from risk.risk_engine import DailyState, RiskEngine, RiskResult, TradeSetup
from strategy.signal_engine import DecisionEngine, DecisionOutput
from strategy.confluence_scorer import score_setup
from config.settings import SystemConfig
from adaptive.opportunity_tracker import (
    OpportunityCandidate, OpportunityStore, SETUP_BLOCKED, RISK_REJECTED,
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
    state: MarketState,
    shadow: DecisionOutput,
    current: DecisionOutput,
    risk_result: Optional[RiskResult] = None,
) -> OpportunityCandidate:
    setup = shadow.setup
    source_bar_id = state.timestamp.isoformat()
    timeframe = getattr(getattr(state, "ohlc", None), "timeframe", None) or "15"
    # risk_result is the schedule-bypassed RiskEngine verdict: None/APPROVED means
    # the setup would ALSO clear shared risk in the always-on world; a failed_rule
    # means it would still be risk-blocked there (recorded, not hidden).
    risk_failed_rule = (
        risk_result.failed_rule
        if (risk_result is not None and risk_result.result != "APPROVED")
        else None
    )
    # The current-path block was schedule-only by construction (shadow trades,
    # current doesn't). But if the schedule-BYPASSED RiskEngine would ALSO reject
    # it, the honest label is RISK_REJECTED (a non-schedule risk failure), not a
    # clean schedule miss — per the tracker contract.
    block_type = RISK_REJECTED if risk_failed_rule else SETUP_BLOCKED
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
        risk_failed_rule=risk_failed_rule,
        market_condition=shadow.market_condition,
        block_type=block_type,
        multi_gate=bool(len(current.failed_gates or []) > 1 or risk_failed_rule),
        snapshots=_snapshots(state, shadow),
        status="PENDING",
        expires_at=None,
    )


def _shadow_risk_result(
    state: MarketState, shadow: DecisionOutput, config: SystemConfig,
    daily_state: DailyState,
) -> Optional[RiskResult]:
    """Validate the shadow setup through a schedule-BYPASSED RiskEngine so we know
    whether it would also clear the shared risk gates in the always-on world.
    Read-only; never executes."""
    s = shadow.setup
    # Score confluence exactly as the runner does — otherwise the RiskEngine's
    # min_confluence_grade gate would falsely reject otherwise-valid candidates.
    confluence = score_setup(state, s)
    trade_setup = TradeSetup(
        direction=s.direction, entry=s.entry, stop=s.stop, target=s.target,
        rr_ratio=s.rr_ratio, strategy=s.strategy, instrument=state.instrument,
        session=state.session, entry_time=state.timestamp, contracts=1,
        notes=getattr(s, "notes", None), confluence_grade=confluence.grade,
    )
    return RiskEngine(config, schedule_mode="always_on_shadow").validate(
        trade_setup, daily_state
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

    # Enrich with the schedule-bypassed risk verdict (capacity/account gates stay
    # shared) — distinguishes a clean miss from one risk would also have stopped.
    risk_result = _shadow_risk_result(state, shadow, config, daily_state)

    candidate = _candidate_from(state, shadow, current, risk_result)
    if store is not None:
        store.record_candidate(candidate)
    return candidate
