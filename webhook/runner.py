"""
webhook/runner.py

Core pipeline for processing a single TradingView bar-close alert.

Flow per bar:
  1. Restore any open position from today's journal and try to resolve it
     with this bar's OHLC (target/stop check).
  2. Check daily limits (max trades, loss lockout).
  3. Run the DecisionEngine → RiskEngine chain.
  4. If approved: execute a bracket order via the configured broker and log it.
  5. Return a structured result dict.

Broker selection is intentionally paper-only for Railway deployment.
Position resolution uses PaperBroker simulated fills and never routes orders to
IBKR or Tradovate.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config.settings import SystemConfig, load_config
from execution.broker_interface import BracketOrder, BrokerInterface
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.confluence_scorer import score_setup as _score_setup
from strategy.signal_engine import DecisionEngine
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state

logger = logging.getLogger(__name__)

# ── Tick values ($ per tick, 0.25-point ticks) ────────────────────────────────
_TICK_VALUES: dict[str, float] = {
    "MES": 1.25,   # $5/pt × 0.25pt/tick
    "ES":  12.50,  # $50/pt
    "MNQ": 0.50,   # $2/pt × 0.25pt/tick
    "NQ":  5.00,   # $20/pt
    "MGC": 1.00,   # $10/troy oz × 0.10pt/tick
    "MCL": 1.00,
}

# Maximum price deviation from entry before a position is considered stale
# (as a fraction of entry price). Equity index futures trade in a narrow
# daily range — a 5% move vs entry means the wrong chart/instrument is feeding
# the webhook. Commodity futures get a slightly wider 10% threshold.
_STALE_PRICE_MISMATCH_THRESHOLD: dict[str, float] = {
    "MES": 0.05,
    "ES":  0.05,
    "MNQ": 0.05,
    "NQ":  0.05,
    "MGC": 0.10,
    "MCL": 0.10,
}

def _tick_value_for(instrument: str) -> float:
    root = (instrument or "").upper().rstrip("!1234567890HMUZ")
    return _TICK_VALUES.get(root, 1.25)


def _make_broker(starting_balance: float = 1500.0) -> BrokerInterface:
    """Return the configured broker.

    BROKER env var controls selection:
      - "ibkr"  → IBKRBroker (paper Gateway on port 4003, is_live=False)
      - anything else (default) → PaperBroker (local simulation)

    LIVE_TRADING_ENABLED must never be set to true — the LiveTradingBlockedError
    guard in RiskEngine will raise at startup if it is.
    """
    broker_type = os.getenv("BROKER", "paper").strip().lower()
    if broker_type == "ibkr":
        from execution.ibkr_broker import IBKRBroker, IBKRConfig
        config = IBKRConfig.from_env()
        logger.info(
            "Using IBKRBroker (paper) → %s:%s", config.host, config.port
        )
        return IBKRBroker(config=config)
    return PaperBroker(starting_balance=starting_balance)


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

    # ── Step 0: Data-quality gate ─────────────────────────────────────────────
    quality_error = _check_payload_quality(payload, cfg)
    if quality_error:
        return {
            "timestamp": payload.timestamp,
            "instrument": payload.ticker,
            "session": None,
            "resolution": None,
            "decision": "BLOCKED_DATA_QUALITY",
            "risk": None,
            "fill": None,
            "context": None,
            "regime": None,
            "gex_status": None,
            "signa_status": None,
            "failed_gates": [quality_error],
            "confidence_score": None,
        }

    _maybe_enrich_payload_with_signa(payload, cfg)
    _maybe_sync_ibkr_position_on_startup(cfg)
    state = build_market_state(payload)
    journal = JournalLogger(log_dir=log_dir)
    today = for_date or date.today()
    daily_state = journal.get_daily_state(today)
    open_position_date = today
    open_pos = journal.get_open_position(today) if daily_state.has_open_position else None
    if open_pos is None:
        # Walk back up to 7 calendar days so Friday→Monday carry works across weekends.
        for days_back in range(1, 8):
            candidate = today - timedelta(days=days_back)
            candidate_state = journal.get_daily_state(candidate)
            if candidate_state.has_open_position:
                open_pos = journal.get_open_position(candidate)
                open_position_date = candidate
                daily_state.has_open_position = True
                break

    result: dict = {
        "timestamp": payload.timestamp,
        "instrument": state.instrument,
        "session": state.session,
        "resolution": None,
        "decision": None,
        "risk": None,
        "fill": None,
        "context": _market_state_context(state),
        "regime": None,
        "gex_status": None,
        "signa_status": None,
        "failed_gates": [],
        "confidence_score": None,
    }

    # ── Step 1: Resolve any open position ────────────────────────────────────
    # Paper mode: simulate using next-bar OHLC.
    # IBKR mode: query actual fills from the Gateway — the bracket child orders
    # (stop and target) are already live on IBKR's side.
    if daily_state.has_open_position:
        if open_pos and _position_is_complete(open_pos):
            broker_type = os.getenv("BROKER", "paper").strip().lower()
            if broker_type == "ibkr":
                from execution.ibkr_broker import IBKRBroker, IBKRConfig
                ibkr = IBKRBroker(config=IBKRConfig.from_env(), auto_connect=True)
                # Restore internal position state so resolve_position knows what to look for
                ibkr._last_position = ibkr._position_from_ib(
                    type("_P", (), {
                        "contract": type("_C", (), {"symbol": open_pos["instrument"] or state.instrument})(),
                        "position": (1 if open_pos["direction"] == "LONG" else -1) * int(open_pos.get("contracts", 1)),
                        "avgCost": float(open_pos["entry"]),
                    })()
                )
                ibkr._last_order_ids = []  # order IDs not persisted — rely on position check
                fill = ibkr.resolve_position()
            else:
                broker = PaperBroker(
                    starting_balance=journal.get_account_balance(
                        cfg.position_sizing.starting_balance, today
                    )
                )
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

            # ── Stale-position safety net (paper mode only) ───────────────────
            # If resolve_position returned None (stop/target not hit), check
            # whether the position has gone stale:
            #   (a) price-scale mismatch: current close differs from stored entry
            #       by more than 5% — prices are from wrong instrument/chart.
            #   (b) age timeout: position has been open for more than 8 hours —
            #       intraday paper positions should not survive a full session.
            # In either case, force-close at the current bar's close price so
            # the system never stays blocked by an unresolvable open position.
            # IBKR mode: skip — IBKR manages the bracket; None just means still open.
            if fill is None and broker_type != "ibkr":
                entry_price = float(open_pos["entry"])
                price_ratio = abs(payload.close - entry_price) / entry_price if entry_price else 1.0
                position_age_hours: float = 999.0
                open_pos_ts = open_pos.get("ts") or open_pos.get("opened_at")
                if open_pos_ts:
                    try:
                        opened_at = datetime.fromisoformat(str(open_pos_ts).replace("Z", "+00:00"))
                        position_age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    except (ValueError, TypeError):
                        pass

                instrument_root = (open_pos.get("instrument") or state.instrument or "").upper().rstrip("!1234567890HMUZ")
                mismatch_threshold = _STALE_PRICE_MISMATCH_THRESHOLD.get(instrument_root, 0.05)
                is_price_mismatch = price_ratio > mismatch_threshold
                is_timed_out = position_age_hours > 8.0
                is_stale = is_price_mismatch or is_timed_out

                if is_stale:
                    reason = "PRICE_MISMATCH" if is_price_mismatch else "SESSION_TIMEOUT"
                    direction = open_pos.get("direction", "LONG")
                    contracts = int(open_pos.get("contracts", 1))
                    # Compute realistic P&L at current close vs entry
                    tick_size = 0.25
                    tick_value = _tick_value_for(open_pos.get("instrument") or state.instrument)
                    raw_ticks = (payload.close - entry_price) / tick_size
                    signed_ticks = raw_ticks if direction == "LONG" else -raw_ticks
                    pnl_dollars = round(signed_ticks * tick_value * contracts, 2)
                    exit_result = "WIN" if signed_ticks > 0 else "LOSS" if signed_ticks < 0 else "BREAKEVEN"
                    logger.warning(
                        "Force-closing stale paper position: instrument=%s reason=%s "
                        "entry=%.2f current_close=%.2f age_hours=%.1f pnl=$%.2f",
                        open_pos.get("instrument"), reason, entry_price,
                        payload.close, position_age_hours, pnl_dollars,
                    )
                    journal.log_outcome(
                        instrument=open_pos.get("instrument") or state.instrument,
                        session=state.session,
                        result=exit_result,
                        entry_price=entry_price,
                        exit_price=payload.close,
                        exit_reason=f"FORCE_CLOSE_{reason}",
                        pnl_ticks=signed_ticks,
                        pnl_dollars=pnl_dollars,
                        contracts=contracts,
                        for_date=open_position_date,
                    )
                    result["resolution"] = f"FORCE_CLOSE_{reason}"
                    daily_state.has_open_position = False
                    daily_state.realized_pnl_dollars += pnl_dollars
                    if exit_result == "LOSS":
                        daily_state.consecutive_losses += 1
                        daily_state.last_loss_at = state.timestamp
                    elif exit_result in ("WIN", "BREAKEVEN"):
                        daily_state.consecutive_losses = 0

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
                    for_date=open_position_date,
                )
                result["resolution"] = fill.result
                if fill.result in {"WIN", "LOSS"}:
                    logger.info(
                        "%s: %s %sc P&L $%.2f",
                        fill.result, fill.instrument, fill.contracts, float(fill.pnl_dollars or 0),
                    )
                daily_state.has_open_position = False
                daily_state.realized_pnl_dollars += float(fill.pnl_dollars or 0.0)
                if fill.result == "LOSS":
                    daily_state.consecutive_losses += 1
                    daily_state.last_loss_at = state.timestamp
                elif fill.result in ("WIN", "BREAKEVEN"):
                    daily_state.consecutive_losses = 0

    # ── Step 2: Check hard daily capacity before evaluating a new signal ─────
    total_daily_capacity = cfg.max_trades_per_day + int(getattr(cfg, "bonus_trades_after_max", 0) or 0)
    if daily_state.trade_count >= total_daily_capacity:
        result["decision"] = "BLOCKED_MAX_TRADES"
        return result
    if daily_state.consecutive_losses >= cfg.max_consecutive_losses and not cfg.circuit_breaker_losses:
        result["decision"] = "BLOCKED_LOSS_LOCKOUT"
        return result
    if daily_state.has_open_position:
        result["decision"] = "BLOCKED_OPEN_POSITION"
        return result

    # ── Step 3: Decision engine ───────────────────────────────────────────────
    decision = DecisionEngine(config=cfg).evaluate(state, daily_state)
    result["decision"] = decision.decision
    result["regime"] = decision.regime
    result["gex_status"] = decision.gex_status
    result["signa_status"] = decision.signa_status
    result["failed_gates"] = decision.failed_gates
    result["confidence_score"] = decision.confidence_score

    if decision.decision != "TRADE" or decision.setup is None:
        journal_entry = decision.to_dict()
        journal_entry["context"] = _market_state_context(state)
        journal.log_decision(journal_entry, None, for_date=today)
        return result

    # ── Step 3b: Score confluence ─────────────────────────────────────────────
    confluence = _score_setup(state, decision.setup)
    result["confidence_score"] = confluence.score
    result["confluence"] = {
        "score": confluence.score,
        "grade": confluence.grade,
        "factors": confluence.factors,
        "penalties": confluence.penalties,
    }
    journal_entry = decision.to_dict()
    journal_entry["context"] = _market_state_context(state)
    journal_entry["confluence"] = result["confluence"]

    # ── Step 4: Risk validation ───────────────────────────────────────────────
    journal_balance = journal.get_account_balance(
        cfg.position_sizing.starting_balance, today
    )
    broker = _make_broker(starting_balance=journal_balance)
    account_balance = broker.get_account_balance()
    if account_balance is None:
        account_balance = journal_balance
    daily_state.account_balance = account_balance
    daily_state.account_peak_balance = journal.get_account_peak_balance(
        cfg.position_sizing.starting_balance, today
    )
    risk_engine = RiskEngine(config=cfg)
    contracts = risk_engine.recommended_contracts(state.instrument, account_balance)
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
        entry_time=state.timestamp,
        contracts=contracts,
        confluence_grade=confluence.grade,
    )
    risk_result = risk_engine.validate(trade_setup, daily_state)
    risk_dict = {
        "result": risk_result.result,
        "failed_rule": risk_result.failed_rule,
        "reason": risk_result.reason,
    }
    result["risk"] = risk_dict
    if not risk_result.approved:
        # Update journal entry decision before writing so the log reflects reality.
        journal_entry["decision"] = "RISK_REJECTED"
        journal_entry["reason"] = risk_result.reason or journal_entry.get("reason")
    journal.log_decision(journal_entry, risk_dict, for_date=today)

    if not risk_result.approved:
        result["decision"] = "RISK_REJECTED"
        if risk_result.failed_rule in {"circuit_breaker", "max_daily_loss", "max_drawdown"}:
            logger.warning("CIRCUIT_BREAKER: %s", risk_result.reason)
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
        contracts=contracts,
    )
    fill = broker.execute_bracket(order)
    if fill.result != "OPEN":
        # Broker rejected or failed to place the order — do NOT mark position open.
        logger.error(
            "Bracket order failed for %s %s: fill_result=%s",
            order.instrument, order.direction, fill.result,
        )
        logger.error("ORDER FAILED: %s %s — %s", order.instrument, order.direction, fill.result)
        result["decision"] = "BLOCKED_EXECUTION_FAILED"
        result["fill"] = {
            "status": fill.result,
            "instrument": state.instrument,
            "direction": decision.setup.direction,
            "entry": decision.setup.entry,
            "stop": decision.setup.stop,
            "target": decision.setup.target,
        }
        return result

    logger.info(
        "TRADE: %s %s %sc @ %s stop %s target %s",
        order.instrument, order.direction, order.contracts, order.entry, order.stop, order.target,
    )
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

_ibkr_synced_today: set[str] = set()  # dates already synced — one sync per calendar day


def _maybe_sync_ibkr_position_on_startup(cfg: SystemConfig) -> None:
    """Sync open position state from IBKR Gateway once per calendar day.

    Only runs when BROKER=ibkr. Logs a warning if IBKR reports a position
    that the journal doesn't know about (manual trade, overnight carry, etc.).
    This is informational only — it does not write to the journal.
    """
    if os.getenv("BROKER", "paper").strip().lower() != "ibkr":
        return
    today_str = date.today().isoformat()
    if today_str in _ibkr_synced_today:
        return
    _ibkr_synced_today.add(today_str)
    try:
        from execution.ibkr_broker import IBKRBroker, IBKRConfig
        ibkr = IBKRBroker(config=IBKRConfig.from_env(), auto_connect=True)
        pos = ibkr.sync_open_position()
        if pos:
            logger.info(
                "IBKR startup sync: open position found — %s %s qty=%s avg=%.2f. "
                "Verify this matches the journal open position.",
                pos.direction, pos.instrument, pos.quantity, pos.entry_price,
            )
    except Exception as exc:  # pragma: no cover - Gateway may not be reachable
        logger.warning("IBKR startup sync failed (Gateway not reachable?): %s", exc)


def _maybe_enrich_payload_with_signa(payload: AlertPayload, cfg: SystemConfig) -> None:
    """Best-effort Signa shadow enrichment. Never raises, never blocks."""
    if not getattr(cfg, "signa_api_enabled", False):
        return
    try:
        from sources.signa_client import enrich_payload_with_signa
        signal = enrich_payload_with_signa(payload, cfg)
        if signal and not signal.ok:
            logger.info("Signa enrichment skipped: %s", signal.error)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Signa enrichment failed: %s", exc)


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
        "gex": {
            "gex_flip": state.gex.gex_flip if state.gex else None,
            "call_wall": state.gex.call_wall if state.gex else None,
            "put_wall": state.gex.put_wall if state.gex else None,
            "hvl": state.gex.hvl if state.gex else None,
            "max_pain": state.gex.max_pain if state.gex else None,
            "ghost": state.gex.ghost if state.gex else None,
            "mid_upper": state.gex.mid_upper if state.gex else None,
            "mid_lower": state.gex.mid_lower if state.gex else None,
            "vol_trigger_up": state.gex.vol_trigger_up if state.gex else None,
            "vol_trigger_down": state.gex.vol_trigger_down if state.gex else None,
            "gex_regime": state.gex.gex_regime if state.gex else None,
            "delta_bias": state.gex.delta_bias if state.gex else None,
        },
        "signa": {
            "grade": state.signa.grade if state.signa else None,
            "score": state.signa.score if state.signa else None,
            "daily_direction": state.signa.daily_direction if state.signa else None,
            "weekly_direction": state.signa.weekly_direction if state.signa else None,
        },
        "icc": {
            "phase": state.icc.phase if state.icc else None,
            "entry_signal": state.icc.entry_signal if state.icc else None,
            "indication_type": state.icc.indication_type if state.icc else None,
            "indication_level": state.icc.indication_level if state.icc else None,
            "last_swing_high": state.icc.last_swing_high if state.icc else None,
            "last_swing_low": state.icc.last_swing_low if state.icc else None,
            "correction_high": state.icc.correction_high if state.icc else None,
            "correction_low": state.icc.correction_low if state.icc else None,
            "stop_loss": state.icc.stop_loss if state.icc else None,
            "tp1": state.icc.tp1 if state.icc else None,
            "tp2": state.icc.tp2 if state.icc else None,
            "htf_phase": state.icc.htf_phase if state.icc else None,
        },
    }


def _check_payload_quality(payload: AlertPayload, cfg: SystemConfig) -> Optional[str]:
    """
    Return a rejection reason string if the bar data is clearly bad, else None.
    Checks: contradictory OHLC (high < low), and stale bar timestamp.
    """
    if payload.high < payload.low:
        return f"Contradictory OHLC: high {payload.high} < low {payload.low}"

    max_staleness = int(getattr(cfg, "max_staleness_seconds", 300) or 0)
    if max_staleness > 0:
        try:
            from webhook.state_builder import parse_timestamp
            bar_ts = parse_timestamp(payload.timestamp)
            age_seconds = (datetime.now(timezone.utc) - bar_ts).total_seconds()
            if age_seconds > max_staleness:
                return f"Stale bar: {int(age_seconds)}s old (max {max_staleness}s)"
        except Exception:
            pass  # unparseable timestamp is caught later by state_builder

    return None


