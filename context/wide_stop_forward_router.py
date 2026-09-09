"""Portfolio-aware router for the wide-stop PAPER forward collector.

The strategy detectors and fill mechanics remain in their existing modules.
This wrapper changes only family-level accounting/admission:
- max 2 simulated open positions across 4HR + 3-2-2;
- max 3 confirmed fills/day across the family, not per strategy ledger;
- mirror each resolved NET outcome into the combined portfolio journal.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
from context import wide_stop_portfolio as portfolio
from context.bar_history import _parse_dt
from risk.risk_engine import RiskEngine


def _mirror_outcome(log_dir, for_date, audit: dict[str, Any]) -> None:
    if not audit.get("valid_outcome"):
        return
    net = audit.get("net_pnl_dollars")
    exit_price = audit.get("exit_price")
    entry_price = audit.get("entry_price")
    if net is None or exit_price is None or entry_price is None:
        return
    portfolio.record_outcome(
        log_dir=log_dir,
        for_date=for_date,
        instrument=contract.INSTRUMENT,
        session=str(audit.get("session") or "new_york"),
        strategy=str(audit.get("strategy") or audit.get("member_strategy") or "wide_stop"),
        result=str(audit.get("outcome_result") or "BREAKEVEN"),
        entry_price=float(entry_price),
        exit_price=float(exit_price),
        exit_reason=str(audit.get("exit_reason") or "UNKNOWN"),
        pnl_ticks=float(audit.get("pnl_ticks") or 0.0),
        net_pnl_dollars=float(net),
        contracts=contract.CONTRACTS,
        signal_timestamp=str(audit.get("signal_timestamp") or "") or None,
        paper_order_id=audit.get("paper_order_id"),
    )


def _block(
    *, cfg, ledger, strategy, candidate, key, log_dir, for_date, lane_result, failed_rule, reason
):
    audit = collector._record_block(
        cfg=cfg,
        ledger=ledger,
        strategy=strategy,
        candidate=candidate,
        key=key,
        log_dir=log_dir,
        for_date=for_date,
        lane_result=lane_result,
        failed_rule=failed_rule,
        reason=reason,
    )
    audit["portfolio"] = portfolio.admission_snapshot(log_dir, for_date or date.today())
    return audit


def _process_locked(
    *, payload, cfg, bars_5m: list[dict], log_dir: str | Path, for_date: Optional[date]
) -> list[dict[str, Any]]:
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    if current_ts is None:
        return []
    day = collector._trading_date(current_ts, for_date)
    bar = {
        "high": float(payload.high),
        "low": float(payload.low),
        "close": float(payload.close),
    }
    events: list[dict[str, Any]] = []

    # Resolve first so a position that closes on this bar frees capacity before
    # a new signal on the same completed bar is considered.
    for ledger in contract.LEDGERS.values():
        resolved = collector._resolve_one_position(
            cfg=cfg,
            ledger=ledger,
            log_dir=log_dir,
            for_date=for_date,
            current_ts=current_ts,
            bar=bar,
        )
        if resolved is not None:
            resolved["strategy"] = (
                contract.LEDGERS[ledger.name].fill_eligible[0]
                if ledger.fill_eligible
                else "wide_stop"
            )
            _mirror_outcome(log_dir, for_date, resolved)
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
        lane_state = collector._load_state(log_dir, ledger)
        collector._reset_day_counter(lane_state, day)
        key = collector._candidate_key(strategy, candidate)
        if key in lane_state["seen"]:
            continue
        collector._mark_seen(lane_state, key)

        if decision is None or decision.decision != "TRADE" or decision.setup is None:
            failed = (
                (decision.failed_gates or ["SIGNAL_GATE_REJECTED"])[0]
                if decision is not None
                else "SIGNAL_GATE_REJECTED"
            )
            audit = _block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="REJECTED_UPSTREAM",
                failed_rule=failed,
                reason=(
                    decision.reason
                    if decision is not None
                    else "isolated signal evaluation unavailable"
                ),
            )
            collector._save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        snap = portfolio.admission_snapshot(log_dir, day)
        if snap["filled_trades_today"] >= portfolio.MAX_FILLS_PER_DAY:
            audit = _block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="BLOCKED_MAX_TRADES",
                failed_rule="portfolio_max_trades_per_day",
                reason=(
                    f"wide-stop portfolio already has {portfolio.MAX_FILLS_PER_DAY} "
                    "confirmed fills today"
                ),
            )
            collector._save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        if snap["open_positions"] >= portfolio.MAX_OPEN_POSITIONS:
            audit = _block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="BLOCKED_OPEN_POSITION",
                failed_rule="portfolio_max_open_positions",
                reason=(
                    f"wide-stop portfolio already has {portfolio.MAX_OPEN_POSITIONS} "
                    "simulated positions open"
                ),
            )
            collector._save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        # One position per strategy ledger still holds underneath the portfolio cap.
        if lane_state.get("position") is not None:
            audit = _block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="BLOCKED_OPEN_POSITION",
                failed_rule="ledger_max_open_positions",
                reason="this strategy ledger already has an open position",
            )
            collector._save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        setup = collector._trade_setup(market_state, decision)
        lane_daily = collector._lane_daily_state(cfg, ledger, log_dir, for_date)
        global_risk = RiskEngine(
            config=cfg,
            schedule_mode=getattr(cfg, "schedule_mode", "current"),
        ).validate(setup, lane_daily)
        audit = collector.observe_candidate(
            cfg=cfg,
            setup=setup,
            global_risk_result=global_risk,
            log_dir=log_dir,
            for_date=for_date,
            market_price=float(payload.close),
            schedule_mode=getattr(cfg, "schedule_mode", "current"),
            candidate_key=key,
        )
        if audit is None:
            collector._save_state(log_dir, ledger, lane_state)
            continue
        audit["collector"] = "wide_stop_forward_v2"
        audit["collector_event"] = "CANDIDATE"
        audit["strategy"] = strategy
        if audit.get("fill_status") == "OPEN" and audit.get("fill_price") is not None:
            lane_state["position"] = collector._position_record(
                strategy=strategy,
                setup=setup,
                audit=audit,
                candidate_key=key,
                day=day,
            )
            lane_state["filled_count"] = int(lane_state.get("filled_count") or 0) + 1
            lane_state["filled_date"] = day.isoformat()
            collector._save_state(log_dir, ledger, lane_state)
            portfolio.register_fill(log_dir, day)
        else:
            collector._save_state(log_dir, ledger, lane_state)
        audit["portfolio"] = portfolio.admission_snapshot(log_dir, day)
        events.append(audit)

    return events


def process_paper_five_min_bar(
    *, payload, cfg, bars_5m: list[dict], log_dir: str | Path, for_date: Optional[date] = None
) -> list[dict[str, Any]]:
    """Paper evidence path with shared family limits."""
    if not contract.evaluate(cfg).active:
        return []
    if collector._root(getattr(payload, "ticker", None)) != contract.INSTRUMENT:
        return []
    if collector._epoch(cfg) is None:
        raise ValueError("wide-stop paper collector active without a valid epoch start")
    with collector._collector_lock(log_dir):
        return _process_locked(
            payload=payload,
            cfg=cfg,
            bars_5m=bars_5m,
            log_dir=log_dir,
            for_date=for_date,
        )
