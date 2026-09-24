"""Tradovate DEMO route for the isolated MNQ 4HR / 3-2-2 campaign.

No strategy formula is duplicated here. Candidate detection, setup construction,
and lane risk overlays come from the existing canonical wide-stop collector.
This module adds only the external-demo safety envelope:
- demo account only; live is refused;
- exact account pin + proof-pinned 8-tick IOC configuration;
- one MNQ contract and one MNQ broker position at a time;
- max three confirmed/reserved execution slots per day across 4HR + 3-2-2;
- max $450 combined day-strategy planned open risk;
- pending intent and slot are saved before broker submission;
- ambiguous submission survives restart and blocks another entry;
- actual-fill stop/R:R/dollar-risk/slippage validation is mandatory;
- EOD broad flatten is allowed only when the account/order state is provably
  exclusive to this demo position;
- Daily 2-2 is not imported and has no path to this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Callable, Optional

from adaptive.execution_gate import order_placement_allowed
from context import wide_stop_demo_state as demo_state
from context import wide_stop_execution as execution
from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
from context.bar_history import _parse_dt
from execution.broker_interface import BracketOrder, Fill, Position
from execution.day_only_exit import (
    BROKER_FLAT_FILL_PRICE_MISSING,
    EOD_BAR_MISSING,
    build_day_only_fill,
    is_after_eod_close,
    is_exact_eod_bar,
    positions_agree,
)
from execution.live_preflight import (
    TERMINAL_ORDER_STATUSES,
    _list_orders,
    _list_positions,
    _order_status,
    _position_qty,
)
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from risk.risk_engine import RiskEngine

_AMBIGUOUS_REASONS = {
    "TRADOVATE_ORDER_ERROR",
    "TRADOVATE_NO_ORDER_ID",
    "ENTRY_UNCONFIRMED",
    "CANCEL_UNCONFIRMED",
    "SUBMIT_AMBIGUOUS_UNRECONCILED",
    "DUPLICATE_CLIENT_ORDER_ID",
}


def _broker_factory() -> TradovateBroker:
    broker = TradovateBroker(config=TradovateConfig.from_env())
    if broker.config.env != "demo" or broker.is_live:
        raise ValueError("wide-stop demo route refuses a non-demo broker")
    if broker.config.expected_account_id is None:
        raise ValueError("wide-stop demo route requires TRADOVATE_EXPECTED_ACCOUNT_ID")
    return broker


def _client_order_id(candidate_key: str) -> str:
    return "ws-" + hashlib.sha256(str(candidate_key).encode("utf-8")).hexdigest()[:32]


def _audit_base(cfg, ledger, strategy, candidate, key) -> dict[str, Any]:
    audit = collector._base_audit(cfg, ledger, strategy, candidate, key)
    audit.update(
        collector="wide_stop_demo_v1",
        execution_route=execution.DEMO_ROUTE,
        external_broker=True,
        broker_environment="demo",
        live_trading_authorized=False,
        promotion_path=False,
    )
    return audit


def _journal_block(
    *, cfg, ledger, strategy, candidate, key, log_dir, for_date, state, failed_rule, reason
) -> dict[str, Any]:
    audit = _audit_base(cfg, ledger, strategy, candidate, key)
    audit.update(
        collector_event="CANDIDATE",
        lane_result="BLOCKED_DEMO",
        lane_failed_rule=str(failed_rule),
        lane_reason=str(reason),
        fill_status=None,
        demo_portfolio=demo_state.snapshot(state),
    )
    collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _account_entry_gate(broker) -> tuple[bool, str]:
    """Conservatively require the visible Tradovate account to be flat/clean."""
    try:
        positions = _list_positions(broker)
        orders = _list_orders(broker)
        if any(abs(_position_qty(row)) > 0 for row in positions):
            return False, "broker_account_not_flat"
        if any(_order_status(row) not in TERMINAL_ORDER_STATUSES for row in orders):
            return False, "broker_working_orders_present"
    except Exception as exc:
        return False, f"broker_state_unreadable:{type(exc).__name__}"
    return True, "broker_account_flat"


def _actual_entry_happened(fill: Fill) -> bool:
    if fill.result == "OPEN":
        return True
    post = (fill.execution_audit or {}).get("post_fill_validation") or {}
    if post.get("actual_entry") is not None:
        return True
    return fill.pnl_dollars is not None and str(fill.exit_reason or "") not in {
        "ENTRY_NOT_FILLED", "TRADOVATE_REJECTED", "BROKER_NOT_READY",
        "TRADOVATE_AUTH_FAILED", "ACCOUNT_MISMATCH", "ACCOUNT_UNRESOLVED",
        "ACCOUNT_BALANCE_LOOKUP_FAILED", "ACCOUNT_NONPOSITIVE_BALANCE",
        "EXECUTION_MODE_INVALID", "EXECUTION_MODE_MISCONFIGURED",
    }


def _definitive_no_fill(fill: Fill) -> bool:
    return (
        fill.result == "CANCELLED"
        and not _actual_entry_happened(fill)
        and str(fill.exit_reason or "") not in _AMBIGUOUS_REASONS
    )


def _pending_record(strategy, setup, key, day) -> dict[str, Any]:
    return {
        "candidate_key": str(key),
        "strategy": str(strategy),
        "instrument": str(setup.instrument),
        "session": str(setup.session),
        "direction": str(setup.direction),
        "planned_entry": float(setup.entry),
        "entry": float(setup.entry),
        "stop": float(setup.stop),
        "target": float(setup.target),
        "rr_ratio": float(setup.rr_ratio),
        "contracts": contract.CONTRACTS,
        "entry_time": (
            setup.entry_time.isoformat()
            if hasattr(setup.entry_time, "isoformat")
            else str(setup.entry_time)
        ),
        "client_order_id": _client_order_id(key),
        "broker_order_ids": {},
        "trading_date": day.isoformat(),
    }


def _position_from_fill(strategy, setup, key, day, fill, broker) -> dict[str, Any]:
    row = _pending_record(strategy, setup, key, day)
    row["entry"] = float(fill.entry_price)
    row["contracts"] = int(fill.contracts or contract.CONTRACTS)
    row["broker_order_ids"] = dict(getattr(broker, "_last_order_ids", {}) or {})
    return row


def _restore_broker(position: dict[str, Any], broker) -> None:
    broker._last_position = Position(
        instrument=str(position["instrument"]),
        direction=str(position["direction"]),
        entry_price=float(position.get("entry") or position["planned_entry"]),
        stop=float(position["stop"]),
        target=float(position["target"]),
        quantity=max(1, int(position.get("contracts") or 1)),
        open=True,
    )
    ids = position.get("broker_order_ids")
    broker._last_order_ids = dict(ids) if isinstance(ids, dict) and ids else None


def _record_outcome(
    *, cfg, ledger, log_dir, for_date, position, fill: Fill,
    valid_outcome: bool, reason_override: Optional[str] = None,
) -> dict[str, Any]:
    gross = round(float(fill.pnl_dollars or 0.0), 2)
    net = round(gross - float(contract.COMMISSION_ROUND_TRIP), 2)
    result = "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"
    exit_reason = str(reason_override or fill.exit_reason or "UNKNOWN")
    collector._lane_journal(log_dir, ledger).log_outcome(
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        result=result,
        entry_price=float(fill.entry_price),
        exit_price=float(fill.exit_price),
        exit_reason=exit_reason,
        pnl_ticks=float(fill.pnl_ticks or 0.0),
        pnl_dollars=net,
        contracts=int(fill.contracts or 1),
        for_date=for_date,
        strategy=str(position.get("strategy") or ""),
        signal_timestamp=str(position.get("entry_time") or ""),
        client_order_id=position.get("client_order_id"),
    )
    audit = contract.evaluate(cfg).audit(
        instrument=contract.INSTRUMENT,
        strategy=str(position.get("strategy") or ""),
        stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
        rr_ratio=float(position.get("rr_ratio") or 0.0),
    )
    audit.update(
        candidate_key=position.get("candidate_key"),
        strategy=position.get("strategy"),
        collector="wide_stop_demo_v1",
        collector_event="OUTCOME",
        execution_route=execution.DEMO_ROUTE,
        broker_result=fill.result,
        outcome_result=result,
        exit_reason=exit_reason,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        pnl_ticks=fill.pnl_ticks,
        gross_pnl_dollars=gross,
        commission_round_trip=contract.COMMISSION_ROUND_TRIP,
        net_pnl_dollars=net,
        valid_outcome=bool(valid_outcome),
        promotion_path=False,
    )
    collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _pending_reconcile(
    *, cfg, log_dir, for_date, day, state, broker_factory: Callable[[], Any]
) -> Optional[dict[str, Any]]:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return None
    broker = broker_factory()
    if getattr(broker, "is_live", True):
        raise ValueError("wide-stop pending reconciler refuses a live broker")
    _restore_broker(pending, broker)
    try:
        confirmed, position = broker.get_position_snapshot()
    except Exception:
        confirmed, position = False, None
    if not confirmed:
        return None
    # Flat after downtime is ambiguous: it can mean no-fill OR opened+closed.
    # Keep pending+reservation and block the day rather than guessing.
    if position is None:
        return None
    if collector._root(getattr(position, "instrument", None)) != contract.INSTRUMENT:
        return None
    if str(getattr(position, "direction", "")).upper() != str(pending["direction"]).upper():
        return None
    if int(getattr(position, "quantity", 0) or 0) != contract.CONTRACTS:
        return None
    pending = dict(pending)
    pending["entry"] = float(position.entry_price)
    # A recovered position has no bracket ids (they were never recorded), so
    # snapshot the account's working orders now: at EOD the flatten may cancel
    # exactly these and nothing that appeared later (FI-8). No snapshot when the
    # list is unreadable, so EOD stays blocked (fail closed).
    snapshot = _recovery_working_order_ids(broker)
    if snapshot is not None:
        pending["recovered_working_order_ids"] = snapshot
    state["position"] = pending
    state["pending"] = None
    demo_state.confirm_slot(state, str(pending["candidate_key"]), str(pending["strategy"]))
    demo_state.save_state(log_dir, state)
    ledger = contract.ledger_for(contract.INSTRUMENT, str(pending["strategy"]))
    audit = contract.evaluate(cfg).audit(
        instrument=contract.INSTRUMENT,
        strategy=str(pending["strategy"]),
        stop_ticks=abs(float(pending["planned_entry"]) - float(pending["stop"])) / 0.25,
        rr_ratio=float(pending.get("rr_ratio") or 0.0),
    )
    audit.update(
        collector="wide_stop_demo_v1",
        collector_event="RECOVERY",
        execution_route=execution.DEMO_ROUTE,
        lane_result="RECOVERED_PENDING_POSITION",
        entry_price=float(position.entry_price),
        valid_outcome=False,
        promotion_path=False,
        demo_portfolio=demo_state.snapshot(state),
    )
    if ledger is not None:
        collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _recovery_working_order_ids(broker) -> Optional[list]:
    """Ids of every non-terminal order on the account, or None if the order
    list is unreadable or a working row has no id."""
    try:
        working = [
            row for row in _list_orders(broker)
            if _order_status(row) not in TERMINAL_ORDER_STATUSES
        ]
    except Exception:
        return None
    ids = [row.get("id") for row in working]
    if any(oid is None for oid in ids):
        return None
    return sorted(ids, key=str)


def _eod_exclusive_gate(broker, position: dict[str, Any]) -> tuple[bool, str]:
    ids = position.get("broker_order_ids")
    recovered = position.get("recovered_working_order_ids")
    if isinstance(ids, dict) and ids:
        allowed_ids = {ids.get("target"), ids.get("stop")}
        allowed_ids.discard(None)
        if not allowed_ids:
            return False, "missing_protective_order_ids_for_eod_flatten"
    elif isinstance(recovered, list):
        # Restart-recovered position: only orders already working at recovery.
        allowed_ids = set(recovered)
    else:
        return False, "missing_broker_order_ids_for_eod_flatten"
    try:
        positions = _list_positions(broker)
        orders = _list_orders(broker)
        open_rows = [row for row in positions if abs(_position_qty(row)) > 0]
        working_ids = {
            row.get("id")
            for row in orders
            if _order_status(row) not in TERMINAL_ORDER_STATUSES and row.get("id") is not None
        }
    except Exception as exc:
        return False, f"eod_broker_state_unreadable:{type(exc).__name__}"
    if len(open_rows) != 1:
        return False, f"eod_expected_one_position_got_{len(open_rows)}"
    if working_ids - allowed_ids:
        return False, "eod_unexpected_working_orders_present"
    return True, "eod_account_exclusive"


def _resolve_position(
    *, cfg, log_dir, for_date, state, payload, broker_factory: Callable[[], Any]
) -> Optional[dict[str, Any]]:
    position = state.get("position")
    if not isinstance(position, dict):
        return None
    ledger = contract.ledger_for(contract.INSTRUMENT, str(position.get("strategy") or ""))
    if ledger is None:
        raise ValueError("demo position has no approved wide-stop ledger")
    broker = broker_factory()
    if getattr(broker, "is_live", True):
        raise ValueError("wide-stop demo resolver refuses a live broker")
    _restore_broker(position, broker)
    fill = broker.resolve_position()
    current_ts = _parse_dt(str(payload.timestamp))
    fallback = False
    if fill is None and current_ts is not None and (
        is_exact_eod_bar(current_ts, "5m") or is_after_eod_close(current_ts)
    ):
        confirmed, broker_position = broker.get_position_snapshot()
        if not confirmed:
            return None
        agree, reason = positions_agree(position, broker_position)
        if not agree:
            audit = contract.evaluate(cfg).audit(
                instrument=contract.INSTRUMENT,
                strategy=str(position.get("strategy") or ""),
                stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
                rr_ratio=float(position.get("rr_ratio") or 0.0),
            )
            audit.update(
                collector="wide_stop_demo_v1",
                collector_event="OUTCOME",
                execution_route=execution.DEMO_ROUTE,
                lane_result="UNRESOLVED_EOD_BROKER_MISMATCH",
                lane_reason=reason,
                valid_outcome=False,
                net_pnl_dollars=None,
            )
            collector._journal(log_dir, ledger, audit, for_date)
            return audit
        exclusive, exclusive_reason = _eod_exclusive_gate(broker, position)
        if not exclusive:
            audit = contract.evaluate(cfg).audit(
                instrument=contract.INSTRUMENT,
                strategy=str(position.get("strategy") or ""),
                stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
                rr_ratio=float(position.get("rr_ratio") or 0.0),
            )
            audit.update(
                collector="wide_stop_demo_v1",
                collector_event="OUTCOME",
                execution_route=execution.DEMO_ROUTE,
                lane_result="UNRESOLVED_EOD_ACCOUNT_NOT_EXCLUSIVE",
                lane_reason=exclusive_reason,
                valid_outcome=False,
                net_pnl_dollars=None,
            )
            collector._journal(log_dir, ledger, audit, for_date)
            return audit
        flat = broker.flatten_position()
        actual_exit = flat.get("close_fill_price") if isinstance(flat, dict) else None
        if not (isinstance(flat, dict) and flat.get("flat_confirmed") and actual_exit is not None):
            audit = contract.evaluate(cfg).audit(
                instrument=contract.INSTRUMENT,
                strategy=str(position.get("strategy") or ""),
                stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
                rr_ratio=float(position.get("rr_ratio") or 0.0),
            )
            audit.update(
                collector="wide_stop_demo_v1",
                collector_event="OUTCOME",
                execution_route=execution.DEMO_ROUTE,
                lane_result="UNRESOLVED_EOD_FLATTEN",
                exit_reason=BROKER_FLAT_FILL_PRICE_MISSING,
                valid_outcome=False,
                net_pnl_dollars=None,
            )
            collector._journal(log_dir, ledger, audit, for_date)
            return audit
        fill = build_day_only_fill(position, float(actual_exit))
        fallback = is_after_eod_close(current_ts) and not is_exact_eod_bar(current_ts, "5m")
    if fill is None:
        return None
    audit = _record_outcome(
        cfg=cfg,
        ledger=ledger,
        log_dir=log_dir,
        for_date=for_date,
        position=position,
        fill=fill,
        valid_outcome=not fallback,
        reason_override=EOD_BAR_MISSING if fallback else None,
    )
    state["position"] = None
    demo_state.save_state(log_dir, state)
    return audit



def _demo_exit_config_errors() -> list[str]:
    """Exit-only DEMO safety checks.

    Deliberately independent of the entry arm/route and FIVE_MIN_FEED_ENABLED:
    an already-open DEMO position must remain closable after the entry lane is
    disarmed or the feed fails. This path can only reduce exposure.
    """
    errors: list[str] = []
    if str(os.getenv("BROKER", "paper")).strip().lower() != "tradovate":
        errors.append("broker_not_tradovate")
    if str(os.getenv("TRADOVATE_ENV", "")).strip().lower() != "demo":
        errors.append("tradovate_env_not_demo")
    if str(os.getenv("LIVE_TRADING_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        errors.append("live_trading_enabled")
    account_pin = str(os.getenv("TRADOVATE_EXPECTED_ACCOUNT_ID", "")).strip()
    if not account_pin or not account_pin.lstrip("-").isdigit():
        errors.append("tradovate_expected_account_id_missing_or_invalid")
    return errors


def _stored_demo_day(log_dir: str | Path) -> Optional[date]:
    """Return the persisted demo trading date without accepting malformed state."""
    path = demo_state.state_path(log_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise demo_state.DemoStateError(f"demo state unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise demo_state.DemoStateError("demo state must be a JSON object")
    token = str(raw.get("trading_date") or "")
    try:
        return date.fromisoformat(token)
    except ValueError as exc:
        raise demo_state.DemoStateError(f"invalid demo trading_date: {token!r}") from exc


def run_demo_eod_fallback(
    *,
    cfg,
    log_dir: str | Path,
    now: Optional[datetime] = None,
    broker_factory: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    """Independent 16:00+ ET safety fallback for an existing DEMO position.

    This function never creates an entry. It only reconciles an already-durable
    pending/position record and, when broker state is exclusive and agrees with
    that record, lets the existing EOD resolver flatten it. A post-16:00 flatten
    is recorded as EOD_BAR_MISSING / invalid evidence.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("wide-stop demo EOD fallback requires a timezone-aware timestamp")
    if not is_after_eod_close(current):
        return {"ok": False, "action": "FAIL_CLOSED", "reason": "BEFORE_EOD_CLOSE"}

    root = Path(log_dir)
    stored_day = _stored_demo_day(root)
    if stored_day is None:
        return {"ok": True, "action": "NO_ACTION", "reason": "NO_DEMO_STATE"}

    with collector._collector_lock(root):
        state = demo_state.load_state(root, stored_day)
        if not isinstance(state.get("pending"), dict) and not isinstance(state.get("position"), dict):
            return {"ok": True, "action": "NO_ACTION", "reason": "NO_OPEN_DEMO_POSITION"}

        errors = _demo_exit_config_errors()
        if errors:
            return {
                "ok": False,
                "action": "FAIL_CLOSED",
                "reason": "DEMO_EXIT_CONFIG_BLOCKED",
                "errors": errors,
            }

        factory = broker_factory or _broker_factory
        broker = factory()
        if getattr(broker, "is_live", True):
            raise ValueError("wide-stop demo EOD fallback refuses a live broker")
        same_broker = lambda: broker
        recovered = _pending_reconcile(
            cfg=cfg,
            log_dir=root,
            for_date=stored_day,
            day=stored_day,
            state=state,
            broker_factory=same_broker,
        )
        if isinstance(state.get("pending"), dict):
            return {
                "ok": False,
                "action": "FAIL_CLOSED",
                "reason": "PENDING_SUBMISSION_UNRESOLVED",
                "recovery": recovered,
                "demo_portfolio": demo_state.snapshot(state),
            }

        if not isinstance(state.get("position"), dict):
            return {
                "ok": True,
                "action": "NO_ACTION",
                "reason": "NO_OPEN_DEMO_POSITION",
                "recovery": recovered,
            }

        synthetic = SimpleNamespace(timestamp=current.isoformat())
        outcome = _resolve_position(
            cfg=cfg,
            log_dir=root,
            for_date=stored_day,
            state=state,
            payload=synthetic,
            broker_factory=same_broker,
        )
        if outcome is None:
            return {
                "ok": False,
                "action": "FAIL_CLOSED",
                "reason": "DEMO_POSITION_UNRESOLVED",
                "demo_portfolio": demo_state.snapshot(state),
            }
        lane_result = str(outcome.get("lane_result") or "")
        if lane_result.startswith("UNRESOLVED_"):
            return {
                "ok": False,
                "action": "FAIL_CLOSED",
                "reason": lane_result,
                "outcome": outcome,
                "demo_portfolio": demo_state.snapshot(state),
            }
        return {
            "ok": True,
            "action": "DEMO_POSITION_RESOLVED",
            "reason": str(outcome.get("exit_reason") or outcome.get("outcome_result") or "RESOLVED"),
            "outcome": outcome,
            "recovery": recovered,
            "demo_portfolio": demo_state.snapshot(state),
        }


def process_demo_five_min_bar(
    *, payload, cfg, bars_5m: list[dict], log_dir: str | Path,
    for_date: Optional[date] = None,
    broker_factory: Optional[Callable[[], Any]] = None,
) -> list[dict[str, Any]]:
    """Process one completed MNQ 5-minute bar through Tradovate DEMO."""
    errors = execution.demo_config_errors(cfg)
    if errors:
        raise ValueError("wide-stop demo configuration blocked: " + ",".join(errors))
    if not contract.evaluate(cfg).active:
        return []
    if collector._root(getattr(payload, "ticker", None)) != contract.INSTRUMENT:
        return []
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    if current_ts is None:
        return []
    if collector._epoch(cfg) is None:
        raise ValueError("wide-stop demo route active without a valid accounting epoch")
    day = collector._trading_date(current_ts, for_date)
    factory = broker_factory or _broker_factory
    events: list[dict[str, Any]] = []

    with collector._collector_lock(log_dir):
        state = demo_state.load_state(log_dir, day)

        recovered = _pending_reconcile(
            cfg=cfg, log_dir=log_dir, for_date=for_date, day=day,
            state=state, broker_factory=factory,
        )
        if recovered is not None:
            events.append(recovered)

        resolved = _resolve_position(
            cfg=cfg, log_dir=log_dir, for_date=for_date, state=state,
            payload=payload, broker_factory=factory,
        )
        if resolved is not None:
            resolved["demo_portfolio"] = demo_state.snapshot(state)
            events.append(resolved)

        # Any pending ambiguity blocks further external submissions.
        if isinstance(state.get("pending"), dict):
            return events

        for strategy in collector._NATIVE:
            decision, market_state, candidate = collector._evaluate_canonical_candidate(
                payload=payload, cfg=cfg, bars_5m=bars_5m, strategy=strategy,
            )
            if candidate is None:
                continue
            ledger = contract.ledger_for(contract.INSTRUMENT, strategy)
            if ledger is None or not contract.is_fill_eligible(contract.INSTRUMENT, strategy):
                continue
            key = collector._candidate_key(strategy, candidate)
            if key in state["seen"]:
                continue
            demo_state.mark_seen(state, key)

            if decision is None or decision.decision != "TRADE" or decision.setup is None:
                failed = (
                    (decision.failed_gates or ["SIGNAL_GATE_REJECTED"])[0]
                    if decision is not None else "SIGNAL_GATE_REJECTED"
                )
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule=failed,
                    reason=(decision.reason if decision is not None else "signal evaluation unavailable"),
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            if demo_state.slots_used(state) >= demo_state.MAX_FILLS_PER_DAY:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_max_trades_per_day",
                    reason="4HR/3-2-2 demo family already uses three daily slots",
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            if isinstance(state.get("position"), dict):
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_same_instrument_position_open",
                    reason="MNQ demo position already open; Tradovate nets by contract",
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            setup = collector._trade_setup(market_state, decision)
            lane_daily = collector._lane_daily_state(cfg, ledger, log_dir, for_date)
            global_risk = RiskEngine(
                config=cfg,
                schedule_mode=getattr(cfg, "schedule_mode", "current"),
            ).validate(setup, lane_daily)
            if not global_risk.approved and not contract.global_rejection_is_overridable(
                global_risk.failed_rule
            ):
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule=str(global_risk.failed_rule or "global_risk"),
                    reason=str(global_risk.reason or "global risk rejected"),
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            lane_result = RiskEngine(
                config=contract.lane_config(cfg, ledger),
                schedule_mode=getattr(cfg, "schedule_mode", "current"),
            ).validate(setup, lane_daily)
            if not lane_result.approved:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule=str(lane_result.failed_rule or "lane_risk"),
                    reason=str(lane_result.reason or "lane risk rejected"),
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            planned_risk = demo_state.risk_dollars(setup.entry, setup.stop, contract.CONTRACTS)
            if planned_risk > demo_state.MAX_COMBINED_OPEN_RISK_DOLLARS + 1e-9:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_combined_open_risk",
                    reason=(
                        f"planned risk ${planned_risk:.2f} exceeds "
                        f"${demo_state.MAX_COMBINED_OPEN_RISK_DOLLARS:.2f}"
                    ),
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            # Lane-local session allowlist first: it can only NARROW what the
            # global gate would permit, never widen it.
            if not execution.demo_session_allowed(setup.session):
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_session_not_allowed",
                    reason=(
                        f"session '{setup.session}' is not in the demo lane allowlist "
                        f"{list(execution.demo_sessions())}"
                    ),
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            # Still the single execution chokepoint — only the schedule mode is
            # lane-local, so the box-wide SCHEDULE_MODE neither arms nor blocks
            # this lane, and the operator's session hold still applies.
            allowed, placement_reason = order_placement_allowed(
                schedule_mode=execution.demo_lane_schedule_mode(),
                session=setup.session,
                live_trading_enabled=False,
                paper_eligible_sessions=getattr(cfg, "paper_eligible_sessions", ()),
                demo_execution_hold_sessions=getattr(cfg, "demo_execution_hold_sessions", ()),
                broker_is_paper=False,
            )
            if not allowed:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="execution_gate", reason=placement_reason,
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            broker = factory()
            if getattr(broker, "is_live", True):
                raise ValueError("wide-stop demo route refuses a live broker")
            account_ok, account_reason = _account_entry_gate(broker)
            if not account_ok:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_account_exclusive_gate", reason=account_reason,
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            reserved, reserve_reason = demo_state.reserve_slot(state, key, strategy)
            if not reserved:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate,
                    key=key, log_dir=log_dir, for_date=for_date, state=state,
                    failed_rule="demo_daily_slot", reason=reserve_reason,
                )
                demo_state.save_state(log_dir, state)
                events.append(audit)
                continue

            # Durable intent BEFORE the broker call. Any crash after this point
            # leaves a reservation/pending record and therefore blocks retry.
            state["pending"] = _pending_record(strategy, setup, key, day)
            demo_state.save_state(log_dir, state)

            order = BracketOrder(
                instrument=setup.instrument,
                direction=setup.direction,
                entry=setup.entry,
                stop=setup.stop,
                target=setup.target,
                rr_ratio=setup.rr_ratio,
                strategy=setup.strategy,
                contracts=contract.CONTRACTS,
                min_rr_ratio=float(ledger.min_rr_ratio),
                max_dollar_risk=float(ledger.worst_case_stop_dollars),
                max_stop_ticks=float(ledger.max_stop_ticks),
                max_slippage_ticks=execution.FROZEN_MNQ_IOC_TICKS,
                execution_model="anchored_structure",
                post_fill_validation_required=True,
                client_order_id=_client_order_id(key),
                # Pin the entry construction itself, independent of whatever
                # TRADOVATE_ENTRY_EXECUTION_MODE / ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ
                # this same process has set for another Tradovate strategy.
                entry_execution_mode_override=execution.DEMO_ENTRY_EXECUTION_MODE,
                entry_slippage_tolerance_ticks_override=execution.FROZEN_MNQ_IOC_TICKS,
                # Dated contract asserted by this bar's alert (#966, observe-only).
                contract_hint=getattr(payload, "contract_hint", None),
            )
            fill = broker.execute_bracket(order)
            actual_fill = _actual_entry_happened(fill)
            if actual_fill:
                demo_state.confirm_slot(state, key, strategy)
            elif _definitive_no_fill(fill):
                demo_state.release_slot(state, key)

            audit = _audit_base(cfg, ledger, strategy, candidate, key)
            audit.update(
                collector_event="CANDIDATE",
                lane_result=("APPROVED" if fill.result == "OPEN" else fill.result),
                fill_status=fill.result,
                fill_price=(fill.entry_price if actual_fill else None),
                fill_reason=fill.exit_reason,
                no_fill_reason=fill.no_fill_reason,
                client_order_id=order.client_order_id,
                broker_order_ids=dict(getattr(broker, "_last_order_ids", {}) or {}),
                execution_audit=fill.execution_audit,
            )

            if fill.result == "OPEN":
                state["position"] = _position_from_fill(strategy, setup, key, day, fill, broker)
                state["pending"] = None
            elif actual_fill and fill.pnl_dollars is not None and fill.exit_price is not None:
                position = dict(state["pending"] or {})
                position["entry"] = float(fill.entry_price)
                audit["post_fill_outcome"] = _record_outcome(
                    cfg=cfg, ledger=ledger, log_dir=log_dir, for_date=for_date,
                    position=position, fill=fill, valid_outcome=False,
                )
                state["pending"] = None
            elif _definitive_no_fill(fill):
                state["pending"] = None
            # Ambiguous CANCELLED result intentionally keeps pending+reservation.

            audit["demo_portfolio"] = demo_state.snapshot(state)
            collector._journal(log_dir, ledger, audit, for_date)
            demo_state.save_state(log_dir, state)
            events.append(audit)

    return events
