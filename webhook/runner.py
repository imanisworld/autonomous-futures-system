"""
webhook/runner.py

Core pipeline for processing a single TradingView bar-close alert.

Flow per bar:
  1. Restore any open position from today's journal and try to resolve it
     with this bar's OHLC (target/stop check).
  2. Check daily limits (max trades, loss lockout).
  3. Run the DecisionEngine → RiskEngine chain.
  4. If approved: execute a bracket order via PaperBroker and log it.
  5. Return a structured result dict.

This is pure Python — FastAPI calls it; tests call it directly.
No live trading. No broker credentials. Paper only.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from config.settings import SystemConfig, load_config
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.signal_engine import DecisionEngine
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state


def process_alert(
    payload: AlertPayload,
    config: Optional[SystemConfig] = None,
    log_dir: str = "logs",
    for_date: Optional[date] = None,
) -> dict:
    """
    Run one bar-close alert through the paper-trading pipeline.

    Args:
        payload:  Validated AlertPayload from the webhook endpoint.
        config:   SystemConfig (loaded from risk_rules.yaml if not provided).
        log_dir:  Journal directory.
        for_date: Override date for testing; defaults to date.today().

    Returns:
        A dict with keys:
            timestamp, instrument, session,
            resolution  (WIN | LOSS | None),
            decision    (TRADE | NO_TRADE | BLOCKED_* | RISK_REJECTED),
            risk        ({result, failed_rule, reason} | None),
            fill        ({status, instrument, direction, entry, stop, target,
                          strategy} | None),
    """
    cfg = config or load_config()
    state = build_market_state(payload)
    journal = JournalLogger(log_dir=log_dir)
    today = for_date or date.today()
    daily_state = journal.get_daily_state(today)

    result: dict = {
        "timestamp": payload.timestamp,
        "instrument": state.instrument,
        "session": state.session,
        "resolution": None,
        "decision": None,
        "risk": None,
        "fill": None,
        "context": _market_state_context(state),
    }

    # ── Step 1: Resolve any open position with this bar's OHLC ───────────────
    if daily_state.has_open_position:
        open_pos = journal.get_open_position(today)
        if open_pos and _position_is_complete(open_pos):
            broker = PaperBroker()
            broker.restore_position(
                instrument=open_pos["instrument"] or state.instrument,
                direction=open_pos["direction"],
                entry=float(open_pos["entry"]),
                stop=float(open_pos["stop"]),
                target=float(open_pos["target"]),
                contracts=int(open_pos.get("contracts", 1)),
            )
            fill = broker.resolve_position(
                NextBarOHLC(high=payload.high, low=payload.low)
            )
            if fill is not None:
                journal.log_outcome(
                    instrument=fill.instrument,
                    session=state.session,
                    result=fill.result,
                    entry_price=fill.entry_price,
                    exit_price=fill.exit_price,
                    exit_reason=fill.exit_reason,
                    pnl_ticks=fill.pnl_ticks,
                    pnl_dollars=fill.pnl_dollars,
                    contracts=fill.contracts,
                    for_date=today,
                )
                result["resolution"] = fill.result
                daily_state.has_open_position = False
                if fill.result == "LOSS":
                    daily_state.consecutive_losses += 1
                elif fill.result == "WIN":
                    daily_state.consecutive_losses = 0

    # ── Step 2: Check daily limits before evaluating a new signal ────────────
    if daily_state.trade_count >= cfg.max_trades_per_day:
        result["decision"] = "BLOCKED_MAX_TRADES"
        return result
    if daily_state.consecutive_losses >= cfg.max_consecutive_losses:
        result["decision"] = "BLOCKED_LOSS_LOCKOUT"
        return result
    if daily_state.has_open_position:
        result["decision"] = "BLOCKED_OPEN_POSITION"
        return result

    # ── Step 3: Decision engine ───────────────────────────────────────────────
    decision = DecisionEngine(config=cfg).evaluate(state, daily_state)
    result["decision"] = decision.decision

    if decision.decision != "TRADE" or decision.setup is None:
        journal.log_decision(decision.to_dict(), None, for_date=today)
        return result

    # ── Step 4: Risk validation ───────────────────────────────────────────────
    trade_setup = TradeSetup(
        direction=decision.setup.direction,
        entry=decision.setup.entry,
        stop=decision.setup.stop,
        target=decision.setup.target,
        rr_ratio=decision.setup.rr_ratio,
        strategy=decision.setup.strategy,
        instrument=state.instrument,
        session=state.session,
        notes=decision.setup.notes,
    )
    risk_result = RiskEngine(config=cfg).validate(trade_setup, daily_state)
    risk_dict = {
        "result": risk_result.result,
        "failed_rule": risk_result.failed_rule,
        "reason": risk_result.reason,
    }
    result["risk"] = risk_dict
    journal.log_decision(decision.to_dict(), risk_dict, for_date=today)

    if not risk_result.approved:
        result["decision"] = "RISK_REJECTED"
        return result

    # ── Step 5: Execute bracket order ────────────────────────────────────────
    order = BracketOrder(
        instrument=state.instrument,
        direction=decision.setup.direction,
        entry=decision.setup.entry,
        stop=decision.setup.stop,
        target=decision.setup.target,
        rr_ratio=decision.setup.rr_ratio,
        strategy=decision.setup.strategy,
        notes=decision.setup.notes,
    )
    broker = PaperBroker()
    broker.execute_bracket(order)         # sets internal OPEN state
    daily_state.trade_count += 1
    daily_state.has_open_position = True

    result["fill"] = {
        "status": "OPEN",
        "instrument": state.instrument,
        "direction": decision.setup.direction,
        "entry": decision.setup.entry,
        "stop": decision.setup.stop,
        "target": decision.setup.target,
        "rr_ratio": decision.setup.rr_ratio,
        "strategy": decision.setup.strategy,
        "contracts": order.contracts,
    }
    return result


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _position_is_complete(pos: dict) -> bool:
    """All required keys present and non-None."""
    return all(pos.get(k) is not None for k in ("direction", "entry", "stop", "target"))


def _market_state_context(state) -> dict:
    """Public, JSON-safe snapshot of the market state derived from the alert."""
    return {
        "instrument": state.instrument,
        "session": state.session,
        "timestamp": state.timestamp.isoformat(),
        "close": state.ohlc.close,
        "timeframe": state.ohlc.timeframe,
        "vwap": {
            "value": state.vwap.value,
            "price_vs_vwap": state.vwap.price_vs_vwap,
            "reclaimed": state.vwap.reclaimed,
            "holding": state.vwap.holding,
        },
        "orb": {
            "high": state.orb.high,
            "low": state.orb.low,
            "status": state.orb.status,
        },
        "trend": {
            "direction": state.trend.direction if state.trend else None,
            "strength": state.trend.strength if state.trend else None,
        },
        "market_condition": state.market_condition,
        "previous_day": {
            "high": state.previous_day.high,
            "low": state.previous_day.low,
            "close": state.previous_day.close,
            "price_vs_pdh": state.previous_day.price_vs_pdh,
            "price_vs_pdl": state.previous_day.price_vs_pdl,
        },
        "volume": {
            "current_bar": state.volume.current_bar,
            "avg_bar": state.volume.avg_bar,
            "relative": state.volume.relative,
        },
        "strat": {
            "current_bar_type": state.strat.current_bar_type if state.strat else None,
            "previous_bar_type": state.strat.previous_bar_type if state.strat else None,
            "two_bars_back_type": state.strat.two_bars_back_type if state.strat else None,
            "strat_sequence": state.strat.strat_sequence if state.strat else None,
            "strat_trigger": state.strat.strat_trigger if state.strat else None,
            "strat_direction": state.strat.strat_direction if state.strat else None,
        },
    }
