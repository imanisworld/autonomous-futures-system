"""Isolated paper research executor for the frozen Transition 400t/30m variant.

Default-off. No environment variable, runner hook, broker route, or deployment
path is defined here. Callers must invoke this module explicitly.

The executor intentionally preserves current global trend/confluence/session
semantics by using DecisionEngine only for canonical candidate construction and
RiskEngine for the existing risk checks. It does not add exemptions. Any such
exemption would be a separate policy decision.

This is NOT #644-qualified canonical replay yet because the full
DecisionEngine.evaluate early market-condition path and ReplayEngine timed-exit
path are not integrated. It is an executable research bridge only.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any

from config.futures_contracts import tick_size
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.signal_engine import DecisionEngine
from strategy.transition_failed_breakdown_reclaim import (
    RESEARCH_HOLD_BARS,
    RESEARCH_IOC_TOLERANCE_TICKS,
    RESEARCH_STRATEGY,
    RESEARCH_STOP_TICKS,
)

STARTING_BALANCE = 8_000.0
DAILY_LOSS_LIMIT = 400.0
MAX_DRAWDOWN_PERCENT = 0.20
CONTRACTS = 1
ENTRY_SLIPPAGE_TICKS = 1.0
EXIT_SLIPPAGE_TICKS = 1.0
COMMISSION_ROUND_TRIP = 1.48
INSTRUMENT = "MNQ"
MAX_TRADES_PER_DAY = 3


@dataclass(frozen=True)
class ResearchOpenResult:
    status: str
    reason: str | None
    candidate: dict[str, Any] | None
    risk: dict[str, Any] | None
    fill: dict[str, Any] | None
    broker: PaperBroker | None


@dataclass(frozen=True)
class ResearchResolution:
    status: str
    exit_reason: str | None
    gross_pnl_dollars: float | None
    net_pnl_dollars: float | None
    bars_consumed: int
    fill: dict[str, Any] | None


def isolated_config(cfg):
    """Return a copy with only the frozen lane risk envelope overlaid."""
    lane = copy.copy(cfg)
    lane.enabled_concepts = [RESEARCH_STRATEGY]
    stop_caps = dict(getattr(cfg, "max_stop_ticks", {}) or {})
    stop_caps[INSTRUMENT] = RESEARCH_STOP_TICKS
    lane.max_stop_ticks = stop_caps
    lane.max_daily_loss = DAILY_LOSS_LIMIT
    lane.max_drawdown_percent = MAX_DRAWDOWN_PERCENT
    lane.max_trades_per_day = min(
        MAX_TRADES_PER_DAY, int(getattr(cfg, "max_trades_per_day", MAX_TRADES_PER_DAY) or MAX_TRADES_PER_DAY)
    )
    lane.live_trading_enabled = False
    return lane


def canonical_candidate(state, cfg, daily_state: DailyState | None = None):
    """Build the sole enabled Transition research candidate via DecisionEngine."""
    lane = isolated_config(cfg)
    candidates = DecisionEngine(config=lane).collect_strategy_candidates(
        state, str(state.market_condition or ""), daily_state
    )
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0].setup.strategy != RESEARCH_STRATEGY:
        raise RuntimeError("isolated Transition research config produced unexpected candidates")
    return candidates[0]


def _trade_setup(state, candidate) -> TradeSetup:
    setup = candidate.setup
    return TradeSetup(
        direction=setup.direction,
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        rr_ratio=setup.rr_ratio,
        strategy=setup.strategy,
        instrument=state.instrument,
        session=state.session,
        contracts=CONTRACTS,
        confluence_grade=candidate.confluence_grade,
        notes=setup.notes,
        entry_time=setup.entry_time,
    )


def open_research_position(
    *, state, cfg, daily_state: DailyState, market_price: float | None = None
) -> ResearchOpenResult:
    """Canonical candidate -> real RiskEngine -> real PaperBroker IOC open."""
    candidate = canonical_candidate(state, cfg, daily_state)
    if candidate is None:
        return ResearchOpenResult("NO_CANDIDATE", "signal_absent", None, None, None, None)

    lane = isolated_config(cfg)
    setup = _trade_setup(state, candidate)
    risk = RiskEngine(config=lane, schedule_mode="always_on").validate(setup, daily_state)
    candidate_dict = {
        "strategy": setup.strategy,
        "direction": setup.direction,
        "entry": setup.entry,
        "stop": setup.stop,
        "target": setup.target,
        "rr_ratio": setup.rr_ratio,
        "confluence_grade": setup.confluence_grade,
    }
    risk_dict = asdict(risk)
    if not risk.approved:
        return ResearchOpenResult("RISK_REJECTED", risk.reason, candidate_dict, risk_dict, None, None)

    broker = PaperBroker(
        starting_balance=float(daily_state.account_balance or STARTING_BALANCE),
        slippage_ticks=ENTRY_SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: RESEARCH_IOC_TOLERANCE_TICKS},
    )
    fill = broker.execute_bracket(
        BracketOrder(
            instrument=INSTRUMENT,
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            rr_ratio=setup.rr_ratio,
            strategy=setup.strategy,
            contracts=CONTRACTS,
            min_rr_ratio=0.0,
            max_stop_ticks=RESEARCH_STOP_TICKS,
            post_fill_validation_required=False,
        ),
        market_price=float(market_price if market_price is not None else state.ohlc.close),
    )
    fill_dict = asdict(fill)
    if fill.result == "CANCELLED":
        return ResearchOpenResult(
            "NO_FILL", fill.exit_reason or "ENTRY_NOT_FILLED", candidate_dict, risk_dict, fill_dict, None
        )
    if fill.result != "OPEN":
        raise RuntimeError(f"unexpected Transition research PaperBroker result {fill.result!r}")
    return ResearchOpenResult("OPEN", None, candidate_dict, risk_dict, fill_dict, broker)


def resolve_six_available_5m_bars(
    broker: PaperBroker, bars: list[dict[str, Any]]
) -> ResearchResolution:
    """Resolve stop-first, otherwise time-close on the sixth available 5m bar."""
    if broker.get_position() is None:
        return ResearchResolution("NO_POSITION", None, None, None, 0, None)
    if len(bars) < RESEARCH_HOLD_BARS:
        return ResearchResolution("OPEN", "INSUFFICIENT_AVAILABLE_5M_BARS", None, None, len(bars), None)

    for idx, bar in enumerate(bars[:RESEARCH_HOLD_BARS], start=1):
        fill = broker.resolve_position(
            NextBarOHLC(
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
            )
        )
        if fill is not None:
            gross = float(fill.pnl_dollars)
            return ResearchResolution(
                "RESOLVED",
                fill.exit_reason,
                gross,
                round(gross - COMMISSION_ROUND_TRIP, 2),
                idx,
                asdict(fill),
            )

    pos = broker.get_position()
    if pos is None:
        raise RuntimeError("Transition research position disappeared before timed exit")
    tick = tick_size(pos.instrument)
    close = float(bars[RESEARCH_HOLD_BARS - 1]["close"])
    exit_price = close - EXIT_SLIPPAGE_TICKS * tick if pos.direction == "LONG" else close + EXIT_SLIPPAGE_TICKS * tick
    gross_sign = exit_price - pos.entry_price if pos.direction == "LONG" else pos.entry_price - exit_price
    result = "WIN" if gross_sign > 0 else ("LOSS" if gross_sign < 0 else "BREAKEVEN")
    fill = broker.force_resolve(result, exit_price)
    if fill is None:
        raise RuntimeError("Transition research timed exit failed to resolve paper position")
    gross = float(fill.pnl_dollars)
    payload = asdict(fill)
    payload["exit_reason"] = "TIME_30M"
    return ResearchResolution(
        "RESOLVED",
        "TIME_30M",
        gross,
        round(gross - COMMISSION_ROUND_TRIP, 2),
        RESEARCH_HOLD_BARS,
        payload,
    )
