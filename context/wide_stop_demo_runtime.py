"""Tradovate-DEMO execution for the isolated 4HR / 3-2-2 evidence campaign.

This is deliberately stricter than paper simulation:
- demo only; live is rejected before broker submission;
- exact account pin + 8-tick IOC configuration required;
- account must be readable and flat with no working orders before each entry;
- one MNQ broker position at a time (Tradovate nets positions by contract);
- max three confirmed fills/day across 4HR + 3-2-2;
- canonical signals/brackets and all non-authorized risk gates are unchanged;
- actual broker order ids are persisted so resolution survives process restarts.
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


def _state_path(log_dir: str | Path, ledger: contract.Ledger) -> Path:
    return contract.journal_dir(log_dir, ledger) / DEMO_STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {"position": None, "seen": []}


def _load_state(log_dir: str | Path, ledger: contract.Ledger) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(log_dir, ledger).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    return {
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
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
    """External account must be definitively flat/readable before a new entry."""
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
    audit = fill.execution_audit or {}
    post = audit.get("post_fill_validation") or {}
    if post.get("actual_entry") is not None:
        return True
    return fill.pnl_dollars is not None and fill.exit_reason not in {
        "ENTRY_NOT_FILLED", "TRADOVATE_REJECTED", "TRADOVATE_ORDER_ERROR"
    }


def _demo_position(strategy, setup, fill, broker, key, day) -> dict[str, Any]:
    return {
        "candidate_key": key,
        "strategy": strategy,
        "instrument": setup.instrument,
        "session": setup.session,
        "direction": setup.direction,
        "planned_entry": float(setup.entry),
        "entry": float(fill.entry_price),
        "stop": float(setup.stop),
        "target": float(setup.target),
        "rr_ratio": float(setup.rr_ratio),
        "contracts": int(fill.contracts or contract.CONTRACTS),
        "entry_time": (
            setup.entry_time.isoformat()
            if hasattr(setup.entry_time, "isoformat")
            else str(setup.entry_time)
        ),
        "client_order_id": _client_order_id(key),
        "broker_order_ids": dict(getattr(broker, "_last_order_ids", {}) or {}),
        "trading_date": day.isoformat(),
    }


def _restore_broker(position: dict[str, Any], broker) -> None:
    broker._last_position = Position(
        instrument=str(position["instrument"]),
        direction=str(position["direction"]),
        entry_price=float(position["entry"]),
        stop=float(position["stop"]),
        target=float(position["target"]),
        quantity=max(1, int(position.get("contracts") or 1)),
        open=True,
    )
    ids = position.get("broker_order_ids")
    broker._last_order_ids = dict(ids) if isinstance(ids, dict) else None


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
        stop_ticks=(
            abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25
        ),
        rr_ratio=float(position.get("rr_ratio") or 0.0),
    )
    audit.update(
        candidate_key=position.get("candidate_key"),
        strategy=position.get("strategy"),
        collector="wide_stop_demo_v1",
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
    if fill is None and current_ts is not None and is_exact_eod_bar(current_ts, "5m"):
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

    # Safety fallback: if the exact 15:55 event was missed, flatten at the first
    # later bar but mark the strategy outcome invalid. Economic P&L still belongs
    # in portfolio drawdown because the demo account actually experienced it.
    fallback = False
    if fill is None and current_ts is not None and is_after_eod_close(current_ts):
        broker_position = broker.get_position()
        agree, reason = positions_agree(position, broker_position)
        if not agree:
            return None
        flat = broker.flatten_position()
        actual_exit = flat.get("close_fill_price") if isinstance(flat, dict) else None
        if isinstance(flat, dict) and flat.get("flat_confirmed") and actual_exit is not None:
            fill = build_day_only_fill(position, float(actual_exit))
            fallback = True

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
    """Evaluate canonical candidates and route qualifying entries to Tradovate DEMO."""
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
        # Resolve the one possible MNQ demo position first.
        for ledger in contract.LEDGERS.values():
            resolved = _resolve_demo_position(
                cfg=cfg,
                ledger=ledger,
                log_dir=log_dir,
                for_date=for_date,
                payload=payload,
                broker_factory=factory,
            )
            if resolved is not None:
                resolved["portfolio"] = portfolio.admission_snapshot(log_dir, day)
                events.append(resolved)

        for strategy in collector._NATIVE:
            decision, market_state, candidate = collector._evaluate_canonical_candidate(
                payload=payload,
                cfg=cfg,
                bars_5m=bars_5m,
                strategy=strategy,
            )
            if candidate is None:
                continue
            ledger = contract.ledger_for(contract.INSTRUMENT, strategy)
            if ledger is None:
                continue
            demo_state = _load_state(log_dir, ledger)
            key = collector._candidate_key(strategy, candidate)
            if key in demo_state["seen"]:
                continue
            _mark_seen(demo_state, key)

            if decision is None or decision.decision != "TRADE" or decision.setup is None:
                failed = (
                    (decision.failed_gates or ["SIGNAL_GATE_REJECTED"])[0]
                    if decision is not None
                    else "SIGNAL_GATE_REJECTED"
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
            if snap["filled_trades_today"] >= portfolio.MAX_FILLS_PER_DAY:
                audit = _journal_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    failed_rule="portfolio_max_trades_per_day",
                    reason=f"wide-stop family already has {portfolio.MAX_FILLS_PER_DAY} fills today",
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            # Current family members are both MNQ. Tradovate exposes one net
            # contract position, so a second MNQ bracket cannot be treated as an
            # independent strategy position. Log and wait until the first closes.
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
                    failed_rule="execution_gate",
                    reason=placement_reason,
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
                    failed_rule="demo_account_exclusive_gate",
                    reason=account_reason,
                )
                _save_state(log_dir, ledger, demo_state)
                events.append(audit)
                continue

            order = BracketOrder(
                instrument=setup.instrument,
                direction=setup.direction,
                entry=setup.entry,
                stop=setup.stop,
                target=setup.target,
                rr_ratio=setup.rr_ratio,
                strategy=setup.strategy,
                contracts=contract.CONTRACTS,
                min_rr_ratio=0.0,  # planned family R:R gate already ran above
                max_slippage_ticks=execution.FROZEN_MNQ_IOC_TICKS,
                execution_model="anchored_structure",
                post_fill_validation_required=True,
                client_order_id=_client_order_id(key),
            )
            fill = broker.execute_bracket(order)
            actual_fill = _actual_entry_happened(fill)
            if actual_fill:
                portfolio.register_fill(log_dir, day)

            audit = _audit_base(cfg, ledger, strategy, candidate, key)
            audit.update(
                collector="wide_stop_demo_v1",
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
                demo_state["position"] = _demo_position(
                    strategy, setup, fill, broker, key, day
                )
            elif actual_fill and fill.pnl_dollars is not None and fill.exit_price is not None:
                # Post-fill invalidation may immediately flatten a filled demo order.
                synthetic_position = {
                    "candidate_key": key,
                    "strategy": strategy,
                    "instrument": setup.instrument,
                    "session": setup.session,
                    "direction": setup.direction,
                    "planned_entry": setup.entry,
                    "entry": fill.entry_price,
                    "stop": setup.stop,
                    "target": setup.target,
                    "rr_ratio": setup.rr_ratio,
                    "contracts": fill.contracts,
                    "entry_time": str(setup.entry_time),
                    "client_order_id": order.client_order_id,
                }
                outcome = _record_resolved(
                    cfg=cfg, ledger=ledger, log_dir=log_dir, for_date=for_date,
                    position=synthetic_position, fill=fill, valid_outcome=False,
                )
                audit["post_fill_outcome"] = outcome

            _save_state(log_dir, ledger, demo_state)
            audit["portfolio"] = portfolio.admission_snapshot(log_dir, day)
            collector._journal(log_dir, ledger, audit, for_date)
            events.append(audit)

    return events
