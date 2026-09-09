"""Tradovate-DEMO execution for the isolated 4HR / 3-2-2 MNQ campaign.

Safety is intentionally stricter than PaperBroker:
- Tradovate demo only; live is rejected before submission;
- exact account pin, 8-tick IOC, one MNQ contract;
- account must be readable/flat with no working orders before entry;
- one MNQ broker position at a time because Tradovate nets by contract;
- shared max three daily slots and $450 combined day-strategy open risk;
- daily slot + pending intent are persisted BEFORE broker submission, so a
  crash/ambiguous submit fails closed instead of allowing another trade;
- post-fill stop/R:R/dollar-risk/slippage validation is mandatory;
- EOD flatten is allowed only when the account contains our position and our
  known bracket children. Unexpected account state blocks broad cancel/flatten;
- Daily 2-2 is not imported and can never reach this route.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from adaptive.execution_gate import order_placement_allowed
from context import wide_stop_execution as execution
from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
from context import wide_stop_portfolio as portfolio
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
    WORKING_ORDER_STATUSES,
    _list_orders,
    _list_positions,
    _order_status,
    _position_qty,
)
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from risk.risk_engine import RiskEngine

DEMO_STATE_FILENAME = "demo_collector_state.json"
_AMBIGUOUS_NO_FILL_REASONS = {
    "TRADOVATE_ORDER_ERROR",
    "TRADOVATE_NO_ORDER_ID",
    "ENTRY_UNCONFIRMED",
    "SUBMIT_AMBIGUOUS_UNRECONCILED",
    "DUPLICATE_CLIENT_ORDER_ID",
}


def _state_path(log_dir: str | Path, ledger: contract.Ledger) -> Path:
    return contract.journal_dir(log_dir, ledger) / DEMO_STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {"position": None, "pending": None, "seen": []}


def _load_state(log_dir: str | Path, ledger: contract.Ledger) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(log_dir, ledger).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    return {
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
        "pending": raw.get("pending") if isinstance(raw.get("pending"), dict) else None,
        "seen": [str(v) for v in list(raw.get("seen") or [])[-collector.MAX_SEEN:]],
    }


def _save_state(log_dir: str | Path, ledger: contract.Ledger, state: dict[str, Any]) -> None:
    path = _state_path(log_dir, ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _mark_seen(state: dict[str, Any], key: str) -> None:
    if key not in state["seen"]:
        state["seen"].append(key)
        state["seen"] = state["seen"][-collector.MAX_SEEN:]


def _broker_factory() -> TradovateBroker:
    cfg = TradovateConfig.from_env()
    if cfg.env != "demo":
        raise ValueError("wide-stop demo route refuses TRADOVATE_ENV other than demo")
    if cfg.expected_account_id is None:
        raise ValueError("wide-stop demo route requires TRADOVATE_EXPECTED_ACCOUNT_ID")
    broker = TradovateBroker(config=cfg)
    if broker.is_live:
        raise ValueError("wide-stop demo route resolved a live broker")
    return broker


def _client_order_id(candidate_key: str) -> str:
    digest = hashlib.sha256(candidate_key.encode("utf-8")).hexdigest()[:32]
    return f"ws-{digest}"


def _account_entry_gate(broker) -> tuple[bool, str]:
    """Require a definitively flat/readable account before a new demo entry."""
    try:
        positions = _list_positions(broker)
        orders = _list_orders(broker)
    except Exception as exc:
        return False, f"broker_state_unreadable:{type(exc).__name__}"
    if any(abs(_position_qty(row)) > 0 for row in positions):
        return False, "broker_account_not_flat"
    if any(_order_status(row) in WORKING_ORDER_STATUSES for row in orders):
        return False, "broker_working_orders_present"
    return True, "broker_account_flat"


def _audit_base(cfg, ledger, strategy, candidate, key) -> dict[str, Any]:
    audit = collector._base_audit(cfg, ledger, strategy, candidate, key)
    audit.update(
        execution_route=execution.DEMO_ROUTE,
        external_broker=True,
        broker_environment="demo",
        live_trading_authorized=False,
    )
    return audit


def _journal_block(
    *, cfg, ledger, strategy, candidate, key, log_dir, for_date, failed_rule, reason
) -> dict[str, Any]:
    audit = _audit_base(cfg, ledger, strategy, candidate, key)
    audit.update(
        collector_event="CANDIDATE",
        lane_result="BLOCKED_DEMO",
        lane_failed_rule=failed_rule,
        lane_reason=reason,
        fill_status=None,
        portfolio=portfolio.admission_snapshot(log_dir, for_date or date.today()),
    )
    collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _actual_entry_happened(fill: Fill) -> bool:
    if fill.result == "OPEN":
        return True
    post = (fill.execution_audit or {}).get("post_fill_validation") or {}
    if post.get("actual_entry") is not None:
        return True
    return fill.pnl_dollars is not None and fill.exit_reason not in {
        "ENTRY_NOT_FILLED", "TRADOVATE_REJECTED", "BROKER_NOT_READY",
        "TRADOVATE_AUTH_FAILED", "ACCOUNT_MISMATCH", "ACCOUNT_UNRESOLVED",
    }


def _definitive_no_fill(fill: Fill) -> bool:
    if _actual_entry_happened(fill):
        return False
    reason = str(fill.exit_reason or "")
    return fill.result == "CANCELLED" and reason not in _AMBIGUOUS_NO_FILL_REASONS


def _pending_record(strategy, setup, key, day) -> dict[str, Any]:
    return {
        "candidate_key": key,
        "strategy": strategy,
        "instrument": setup.instrument,
        "session": setup.session,
        "direction": setup.direction,
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


def _demo_position(strategy, setup, fill, broker, key, day) -> dict[str, Any]:
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


def _net_fill(fill: Fill) -> tuple[float, str]:
    gross = round(float(fill.pnl_dollars or 0.0), 2)
    net = round(gross - float(contract.COMMISSION_ROUND_TRIP), 2)
    result = "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"
    return net, result


def _record_resolved(
    *, cfg, ledger, log_dir, for_date, position, fill: Fill, valid_outcome: bool, reason_override=None
) -> dict[str, Any]:
    net, economic_result = _net_fill(fill)
    journal = collector._lane_journal(log_dir, ledger)
    journal.log_outcome(
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        result=economic_result,
        entry_price=float(fill.entry_price),
        exit_price=float(fill.exit_price),
        exit_reason=str(reason_override or fill.exit_reason or "UNKNOWN"),
        pnl_ticks=float(fill.pnl_ticks or 0.0),
        pnl_dollars=net,
        contracts=int(fill.contracts or 1),
        for_date=for_date,
        strategy=str(position.get("strategy") or ""),
        signal_timestamp=str(position.get("entry_time") or ""),
        client_order_id=position.get("client_order_id"),
    )
    portfolio.record_outcome(
        log_dir=log_dir,
        for_date=for_date,
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        strategy=str(position.get("strategy") or ""),
        result=economic_result,
        entry_price=float(fill.entry_price),
        exit_price=float(fill.exit_price),
        exit_reason=str(reason_override or fill.exit_reason or "UNKNOWN"),
        pnl_ticks=float(fill.pnl_ticks or 0.0),
        net_pnl_dollars=net,
        contracts=int(fill.contracts or 1),
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
        collector="wide_stop_demo_v2",
        collector_event="OUTCOME",
        execution_route=execution.DEMO_ROUTE,
        broker_result=fill.result,
        outcome_result=economic_result,
        exit_reason=str(reason_override or fill.exit_reason or "UNKNOWN"),
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        pnl_ticks=fill.pnl_ticks,
        gross_pnl_dollars=round(float(fill.pnl_dollars or 0.0), 2),
        commission_round_trip=contract.COMMISSION_ROUND_TRIP,
        net_pnl_dollars=net,
        valid_outcome=bool(valid_outcome),
        promotion_path=False,
    )
    collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _pending_reconcile(
    *, cfg, ledger, log_dir, for_date, broker_factory: Callable[[], Any]
) -> Optional[dict[str, Any]]:
    """Recover a position after a crash between broker submit and local save.

    Confirmed open MNQ state can be reconstructed. Confirmed flat state is NOT
    enough to prove a no-fill (the trade could have opened and closed while the
    process was down), so the reservation stays and the system remains blocked.
    """
    state = _load_state(log_dir, ledger)
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return None
    broker = broker_factory()
    if getattr(broker, "is_live", True):
        raise ValueError("wide-stop pending reconciler refuses a live broker")
    _restore_broker(pending, broker)
    try:
        confirmed, broker_position = broker.get_position_snapshot()
    except Exception:
        confirmed, broker_position = False, None
    if not confirmed:
        return None
    if broker_position is None:
        # Flat is ambiguous after downtime; do not release the daily slot.
        return None
    if collector._root(getattr(broker_position, "instrument", None)) != contract.INSTRUMENT:
        return None
    if str(getattr(broker_position, "direction", "")).upper() != str(pending["direction"]).upper():
        return None
    if int(getattr(broker_position, "quantity", 0) or 0) != contract.CONTRACTS:
        return None

    recovered = dict(pending)
    recovered["entry"] = float(broker_position.entry_price)
    state["position"] = recovered
    state["pending"] = None
    _save_state(log_dir, ledger, state)
    day = date.fromisoformat(str(recovered["trading_date"]))
    portfolio.confirm_daily_slot(log_dir, day, str(recovered["candidate_key"]))

    audit = contract.evaluate(cfg).audit(
        instrument=contract.INSTRUMENT,
        strategy=str(recovered.get("strategy") or ""),
        stop_ticks=abs(float(recovered["planned_entry"]) - float(recovered["stop"])) / 0.25,
        rr_ratio=float(recovered.get("rr_ratio") or 0.0),
    )
    audit.update(
        collector="wide_stop_demo_v2",
        collector_event="RECOVERY",
        execution_route=execution.DEMO_ROUTE,
        lane_result="RECOVERED_PENDING_POSITION",
        entry_price=float(broker_position.entry_price),
        valid_outcome=False,
        promotion_path=False,
    )
    collector._journal(log_dir, ledger, audit, for_date)
    return audit


def _eod_exclusive_gate(broker, position: dict[str, Any]) -> tuple[bool, str]:
    """Permit broad broker flatten only when every working order is ours."""
    ids = position.get("broker_order_ids")
    if not isinstance(ids, dict) or not ids:
        return False, "missing_broker_order_ids_for_eod_flatten"
    allowed_ids = {ids.get("target"), ids.get("stop")}
    allowed_ids.discard(None)
    if not allowed_ids:
        return False, "missing_protective_order_ids_for_eod_flatten"
    try:
        positions = _list_positions(broker)
        orders = _list_orders(broker)
    except Exception as exc:
        return False, f"eod_broker_state_unreadable:{type(exc).__name__}"
    open_rows = [row for row in positions if abs(_position_qty(row)) > 0]
    if len(open_rows) != 1:
        return False, f"eod_expected_one_position_got_{len(open_rows)}"
    working_ids = {
        row.get("id")
        for row in orders
        if _order_status(row) in WORKING_ORDER_STATUSES and row.get("id") is not None
    }
    unexpected = working_ids - allowed_ids
    if unexpected:
        return False, "eod_unexpected_working_orders_present"
    return True, "eod_account_exclusive"


def _resolve_demo_position(
    *, cfg, ledger, log_dir, for_date, payload, broker_factory: Callable[[], Any]
) -> Optional[dict[str, Any]]:
    state = _load_state(log_dir, ledger)
    position = state.get("position")
    if not isinstance(position, dict):
        return None
    broker = broker_factory()
    if getattr(broker, "is_live", True):
        raise ValueError("wide-stop demo resolver refuses a live broker")
    _restore_broker(position, broker)

    fill = broker.resolve_position()
    current_ts = _parse_dt(str(payload.timestamp))
    if fill is None and current_ts is not None and (
        is_exact_eod_bar(current_ts, "5m") or is_after_eod_close(current_ts)
    ):
        broker_position = broker.get_position()
        agree, reason = positions_agree(position, broker_position)
        if not agree:
            audit = contract.evaluate(cfg).audit(
                instrument=contract.INSTRUMENT,
                strategy=str(position.get("strategy") or ""),
                stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
                rr_ratio=float(position.get("rr_ratio") or 0.0),
            )
            audit.update(
                collector="wide_stop_demo_v2",
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
                collector="wide_stop_demo_v2",
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
                collector="wide_stop_demo_v2",
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
    else:
        fallback = False

    if fill is None:
        return None
    audit = _record_resolved(
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
    _save_state(log_dir, ledger, state)
    return audit


def process_demo_five_min_bar(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    log_dir: str | Path,
    for_date: Optional[date] = None,
    broker_factory: Optional[Callable[[], Any]] = None,
) -> list[dict[str, Any]]:
    """Evaluate canonical day candidates and route qualifying entries to DEMO."""
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

    factory = broker_factory or _broker_factory
    day = collector._trading_date(current_ts, for_date)
    events: list[dict[str, Any]] = []

    with collector._collector_lock(log_dir):
        # Recover crash-window submissions before doing anything new.
        for ledger in contract.LEDGERS.values():
            recovered = _pending_reconcile(
                cfg=cfg, ledger=ledger, log_dir=log_dir, for_date=for_date,
                broker_factory=factory,
            )
            if recovered is not None:
                events.append(recovered)

        # Resolve the one possible MNQ demo position first.
        for ledger in contract.LEDGERS.values():
            resolved = _resolve_demo_position(
                cfg=cfg, ledger=ledger, log_dir=log_dir, for_date=for_date,
                payload=payload, broker_factory=factory,
            )
            if resolved is not None:
                resolved["portfolio"] = portfolio.admission_snapshot(log_dir, day)
                events.append(resolved)

        # Any unresolved external reservation means submission outcome is not
        # known. Missing trades is allowed; guessing another order is not.
        if portfolio.reserved_count(log_dir, day, route=execution.DEMO_ROUTE) > 0:
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
            demo_state = _load_state(log_dir, ledger)
            key = collector._candidate_key(strategy, candidate)
            if key in demo_state["seen"]:
                continue
            _mark_seen(demo_state, key)

            if decision is None or decision.decision != "TRADE" or decision.setup is None:
                failed = (
                    (decision.failed_gates or ["SIGNAL_GATE_REJECTED"])[0]
                    if decision is not None else "SIGNAL_GATE_REJECTED"
                )
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date, failed_rule=failed,
                    reason=(decision.reason if decision is not None else "signal evaluation unavailable"),
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            snap = portfolio.admission_snapshot(log_dir, day)
            if snap["daily_slots_used"] >= portfolio.MAX_FILLS_PER_DAY:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="portfolio_max_trades_per_day",
                    reason=f"wide-stop family already uses {portfolio.MAX_FILLS_PER_DAY} daily slots",
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            # Both demo-eligible strategies are MNQ. Never pretend two netted MNQ
            # broker positions are independent strategy positions.
            if portfolio.same_instrument_open(log_dir, contract.INSTRUMENT):
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="demo_same_instrument_position_open",
                    reason="MNQ demo position already open; second MNQ signal not submitted",
                )
                _save_state(log_dir, ledger, demo_state)
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
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule=str(global_risk.failed_rule or "global_risk"),
                    reason=str(global_risk.reason or "global risk rejected"),
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            lane_cfg = contract.lane_config(cfg, ledger)
            lane_result = RiskEngine(
                config=lane_cfg,
                schedule_mode=getattr(cfg, "schedule_mode", "current"),
            ).validate(setup, lane_daily)
            if not lane_result.approved:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule=str(lane_result.failed_rule or "lane_risk"),
                    reason=str(lane_result.reason or "lane risk rejected"),
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            proposed_risk = portfolio.risk_dollars(
                instrument=setup.instrument, entry=setup.entry, stop=setup.stop,
                contracts=contract.CONTRACTS,
            )
            risk_ok, combined = portfolio.proposed_risk_allowed(log_dir, proposed_risk)
            if not risk_ok:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="portfolio_combined_open_risk",
                    reason=(
                        f"combined planned risk ${combined:.2f} exceeds "
                        f"${portfolio.MAX_COMBINED_OPEN_RISK_DOLLARS:.2f}"
                    ),
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            allowed, placement_reason = order_placement_allowed(
                schedule_mode=getattr(cfg, "schedule_mode", "current"),
                session=setup.session,
                live_trading_enabled=False,
                paper_eligible_sessions=getattr(cfg, "paper_eligible_sessions", ()),
                demo_execution_hold_sessions=getattr(cfg, "demo_execution_hold_sessions", ()),
                broker_is_paper=False,
            )
            if not allowed:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="execution_gate", reason=placement_reason,
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            broker = factory()
            if getattr(broker, "is_live", True):
                raise ValueError("wide-stop demo route refuses a live broker")
            account_ok, account_reason = _account_entry_gate(broker)
            if not account_ok:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="demo_account_exclusive_gate", reason=account_reason,
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            reserved, reserve_reason = portfolio.reserve_daily_slot(
                log_dir, day, key, route=execution.DEMO_ROUTE
            )
            if not reserved:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="portfolio_daily_slot", reason=reserve_reason,
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            # Persist intent before the external call. On crash this state plus
            # the reserved slot makes the next run reconcile/block, never retry.
            demo_state["pending"] = _pending_record(strategy, setup, key, day)
            _save_state(log_dir, ledger, demo_state)

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
            )

            # Deliberately no broad exception cleanup here. If submission raises,
            # outcome is ambiguous; pending+reservation remain fail-closed.
            fill = broker.execute_bracket(order)
            actual_fill = _actual_entry_happened(fill)
            if actual_fill:
                portfolio.confirm_daily_slot(log_dir, day, key)
            elif _definitive_no_fill(fill):
                portfolio.release_daily_slot(log_dir, day, key)

            audit = _audit_base(cfg, ledger, strategy, candidate, key)
            audit.update(
                collector="wide_stop_demo_v2",
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
                demo_state["position"] = _demo_position(strategy, setup, fill, broker, key, day)
                demo_state["pending"] = None
            elif actual_fill and fill.pnl_dollars is not None and fill.exit_price is not None:
                synthetic_position = dict(demo_state["pending"] or {})
                synthetic_position["entry"] = float(fill.entry_price)
                outcome = _record_resolved(
                    cfg=cfg, ledger=ledger, log_dir=log_dir, for_date=for_date,
                    position=synthetic_position, fill=fill, valid_outcome=False,
                )
                audit["post_fill_outcome"] = outcome
                demo_state["pending"] = None
            elif _definitive_no_fill(fill):
                demo_state["pending"] = None
            # Ambiguous result intentionally keeps pending + reserved slot.

            _save_state(log_dir, ledger, demo_state)
            audit["portfolio"] = portfolio.admission_snapshot(log_dir, day)
            collector._journal(log_dir, ledger, audit, for_date)
            events.append(audit)

    return events
