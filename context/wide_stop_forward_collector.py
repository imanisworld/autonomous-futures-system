"""Canonical forward collector for the isolated wide-stop hypothetical ledgers.

The active book may keep 4HR Re-Trigger and 3-2-2 parked while this observer
runs their existing canonical 5-minute state machines on an isolated config
copy. Hypothetical fills are persisted and resolved on later 5-minute bars via
PaperBroker. Nothing here can route to an external broker or mutate the real
book's decision, journal, balance, or risk state.

Shared family admission is enforced through ``wide_stop_portfolio``: at most two
PaperBroker positions, at most three daily execution slots total across 4HR and
3-2-2, and at most $450 combined planned open risk. The legacy per-ledger
three-fill counter remains as a secondary fail-closed guard.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from context import wide_stop_ledger_paper as contract
from context import wide_stop_portfolio as portfolio
from context.bar_history import _parse_dt
from context.wide_stop_ledger_runtime import (
    _epoch,
    _journal,
    _lane_broker,
    _lane_journal,
    observe_candidate,
)
from execution.day_only_exit import EOD_BAR_MISSING, is_after_eod_close, resolve_paper_eod
from execution.paper_broker import NextBarOHLC
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.confluence_scorer import score_setup as _score_setup
from strategy.four_hr_retrigger import advance_4hr_retrigger
from strategy.signal_engine import DecisionEngine
from strategy.strat_322_first_live import advance_strat_322_first_live

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ET = ZoneInfo("America/New_York")
MAX_SEEN = 500
MAX_FILLED_PER_DAY = 3
FOUR_HR = "strat_4hr_retrigger"
THREE_TWO_TWO = "strat_322_first_live"
_NATIVE = (FOUR_HR, THREE_TWO_TWO)
_LOCAL_LOCK = threading.Lock()


def _root(value: object) -> str:
    raw = str(value or "").upper().strip()
    for suffix in ("1!", "2!"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def _state_path(log_dir: str | Path, ledger: contract.Ledger) -> Path:
    return contract.journal_dir(log_dir, ledger) / "forward_collector_state.json"


def _empty_state() -> dict[str, Any]:
    return {"filled_date": None, "filled_count": 0, "position": None, "seen": []}


def _load_state(log_dir: str | Path, ledger: contract.Ledger) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(log_dir, ledger).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    return {
        "filled_date": raw.get("filled_date"),
        "filled_count": max(0, int(raw.get("filled_count") or 0)),
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
        "seen": [str(v) for v in list(raw.get("seen") or [])[-MAX_SEEN:]],
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


@contextmanager
def _collector_lock(log_dir: str | Path):
    """Serialize state/journal lifecycle across duplicate webhook workers."""
    path = Path(log_dir) / contract.JOURNAL_ROOT / ".forward_collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK:
        with path.open("a") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _trading_date(current_ts: datetime, for_date: Optional[date]) -> date:
    return for_date or current_ts.astimezone(ET).date()


def _reset_day_counter(state: dict[str, Any], day: date) -> None:
    key = day.isoformat()
    if state.get("filled_date") != key:
        state["filled_date"] = key
        state["filled_count"] = 0


def _candidate_key(strategy: str, candidate: dict[str, Any]) -> str:
    entry_time = candidate.get("entry_time")
    if hasattr(entry_time, "isoformat"):
        entry_time = entry_time.isoformat()
    return "|".join(
        [
            strategy,
            str(entry_time or ""),
            f"{float(candidate['entry']):.4f}",
            f"{float(candidate['stop']):.4f}",
            f"{float(candidate['target']):.4f}",
        ]
    )


def _candidate_metrics(candidate: dict[str, Any]) -> tuple[float, float]:
    entry = float(candidate["entry"])
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return risk / 0.25, reward / risk if risk > 0 else 0.0


def _base_audit(
    cfg,
    ledger: contract.Ledger,
    strategy: str,
    candidate: dict[str, Any],
    candidate_key: str,
) -> dict[str, Any]:
    stop_ticks, rr = _candidate_metrics(candidate)
    audit = contract.evaluate(cfg).audit(
        instrument=contract.INSTRUMENT,
        strategy=strategy,
        stop_ticks=round(stop_ticks, 1),
        rr_ratio=round(rr, 6),
    )
    audit.update(
        candidate_key=candidate_key,
        collector="wide_stop_forward_v1",
        canonical_detector=True,
        active_book_mutated=False,
        external_broker=False,
    )
    return audit


def _mark_seen(state: dict[str, Any], key: str) -> None:
    if key not in state["seen"]:
        state["seen"].append(key)
        state["seen"] = state["seen"][-MAX_SEEN:]


def _isolated_config(cfg, ledger: contract.Ledger, strategy: str):
    """Copy config; open only this parked member inside the observer."""
    isolated = contract.lane_config(cfg, ledger)
    isolated.enabled_concepts = [strategy]
    statuses = dict(getattr(cfg, "strategy_status", {}) or {})
    statuses[strategy] = "PAPER_ELIGIBLE"
    isolated.strategy_status = statuses
    disabled = {
        str(inst): list(values or [])
        for inst, values in dict(
            getattr(cfg, "disabled_concepts_per_instrument", {}) or {}
        ).items()
    }
    disabled[contract.INSTRUMENT] = [
        value for value in disabled.get(contract.INSTRUMENT, []) if value != strategy
    ]
    isolated.disabled_concepts_per_instrument = disabled
    return isolated


def _window_start(strategy: str) -> time:
    return time(9, 30) if strategy == FOUR_HR else time(10, 0)


def _in_detection_window(strategy: str, current_ts: datetime) -> bool:
    local = current_ts.astimezone(ET).time().replace(tzinfo=None)
    return _window_start(strategy) <= local < time(11, 0)


def _prior_machine_state(
    *,
    strategy: str,
    bars_5m: list[dict],
    current_ts: datetime,
    instrument: str,
) -> dict:
    """Reconstruct state immediately before current_ts using arrived bars only."""
    advance = advance_4hr_retrigger if strategy == FOUR_HR else advance_strat_322_first_live
    start = _window_start(strategy)
    day = current_ts.astimezone(ET).date()
    persisted: dict = {}
    ordered: list[tuple[datetime, dict]] = []
    for raw in bars_5m:
        parsed = _parse_dt(str(raw.get("ts") or raw.get("timestamp") or ""))
        if parsed is None:
            continue
        local = parsed.astimezone(ET)
        if local.date() != day or local.time().replace(tzinfo=None) < start:
            continue
        if parsed >= current_ts:
            continue
        ordered.append((parsed, raw))
    ordered.sort(key=lambda item: item[0])
    for ts, _raw in ordered:
        persisted, _candidate = advance(
            bars_5m=bars_5m,
            current_bar_ts=ts,
            instrument=instrument,
            persisted_state=persisted,
        )
    return persisted


def _evaluate_canonical_candidate(
    *, payload, cfg, bars_5m: list[dict], strategy: str
):
    """Run the parked strategy through its normal DecisionEngine signal gates."""
    from webhook.state_builder import build_market_state

    current_ts = _parse_dt(str(payload.timestamp))
    if current_ts is None or not _in_detection_window(strategy, current_ts):
        return None, None, None
    state = build_market_state(payload)
    if _root(state.instrument) != contract.INSTRUMENT:
        return None, None, None
    state.canonical_4hr_only = True
    state.bar_history_5m = list(bars_5m)

    ledger = contract.ledger_for(contract.INSTRUMENT, strategy)
    if ledger is None:
        return None, None, None
    isolated = _isolated_config(cfg, ledger, strategy)
    daily = DailyState()
    prior = _prior_machine_state(
        strategy=strategy,
        bars_5m=bars_5m,
        current_ts=current_ts,
        instrument=contract.INSTRUMENT,
    )
    if strategy == FOUR_HR:
        daily.four_hr_retrigger_state[contract.INSTRUMENT] = prior
    else:
        daily.strat_322_first_live_state[contract.INSTRUMENT] = prior

    decision = DecisionEngine(config=isolated).evaluate(state, daily)
    candidate = (
        state.four_hr_retrigger_candidate
        if strategy == FOUR_HR
        else state.strat_322_first_live_candidate
    )
    return decision, state, candidate


def _lane_daily_state(cfg, ledger: contract.Ledger, log_dir, for_date):
    epoch = _epoch(cfg)
    if epoch is None:
        raise ValueError("wide-stop forward collector active without a valid epoch start")
    journal = _lane_journal(log_dir, ledger)
    balance, peak = journal.get_account_state_since(ledger.starting_balance, epoch, for_date)
    daily = journal.get_daily_state(for_date)
    daily.account_balance = balance
    daily.account_peak_balance = peak
    return daily


def _trade_setup(state, decision) -> TradeSetup:
    setup = decision.setup
    confluence = _score_setup(state, setup)
    return TradeSetup(
        direction=setup.direction,
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        rr_ratio=setup.rr_ratio,
        strategy=setup.strategy,
        instrument=state.instrument,
        session=state.session,
        notes=setup.notes,
        entry_time=setup.entry_time,
        contracts=contract.CONTRACTS,
        confluence_grade=confluence.grade,
    )


def _record_block(
    *, cfg, ledger, strategy, candidate, key, log_dir, for_date, lane_result, failed_rule, reason
) -> dict[str, Any]:
    audit = _base_audit(cfg, ledger, strategy, candidate, key)
    audit.update(
        collector_event="CANDIDATE",
        lane_result=lane_result,
        lane_failed_rule=failed_rule,
        lane_reason=reason,
        fill_status=None,
        portfolio=portfolio.admission_snapshot(log_dir, for_date or date.today()),
    )
    _journal(log_dir, ledger, audit, for_date)
    return audit


def _position_record(
    *, strategy: str, setup: TradeSetup, audit: dict[str, Any], candidate_key: str, day: date
) -> dict[str, Any]:
    return {
        "candidate_key": candidate_key,
        "strategy": strategy,
        "instrument": setup.instrument,
        "session": setup.session,
        "direction": setup.direction,
        "planned_entry": float(setup.entry),
        "entry": float(audit["fill_price"]),
        "stop": float(setup.stop),
        "target": float(setup.target),
        "rr_ratio": float(setup.rr_ratio),
        "contracts": contract.CONTRACTS,
        "entry_time": (
            setup.entry_time.isoformat()
            if hasattr(setup.entry_time, "isoformat")
            else str(setup.entry_time)
        ),
        "paper_order_id": audit.get("fill_paper_order_id"),
        "trading_date": day.isoformat(),
    }


def _economic_result(net_pnl: float) -> str:
    if net_pnl > 0:
        return "WIN"
    if net_pnl < 0:
        return "LOSS"
    return "BREAKEVEN"


def _resolve_one_position(
    *, cfg, ledger: contract.Ledger, log_dir, for_date, current_ts: datetime, bar: dict[str, float]
) -> Optional[dict[str, Any]]:
    state = _load_state(log_dir, ledger)
    position = state.get("position")
    if not isinstance(position, dict):
        return None
    entry_time = _parse_dt(str(position.get("entry_time") or ""))
    if entry_time is None:
        state["position"] = None
        _save_state(log_dir, ledger, state)
        return None
    if current_ts < entry_time:
        return None

    current_day = current_ts.astimezone(ET).date()
    entry_day = entry_time.astimezone(ET).date()
    if current_day > entry_day or is_after_eod_close(current_ts):
        audit = contract.evaluate(cfg).audit(
            instrument=contract.INSTRUMENT,
            strategy=str(position.get("strategy") or ""),
            stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
            rr_ratio=float(position.get("rr_ratio") or 0.0),
        )
        audit.update(
            candidate_key=position.get("candidate_key"),
            collector="wide_stop_forward_v1",
            collector_event="OUTCOME",
            lane_result="UNRESOLVED_EOD_MISSING",
            outcome_result="UNRESOLVED",
            exit_reason=EOD_BAR_MISSING,
            gross_pnl_dollars=None,
            commission_round_trip=contract.COMMISSION_ROUND_TRIP,
            net_pnl_dollars=None,
            valid_outcome=False,
            promotion_path=False,
        )
        _journal(log_dir, ledger, audit, for_date)
        state["position"] = None
        _save_state(log_dir, ledger, state)
        return audit

    epoch = _epoch(cfg)
    if epoch is None:
        raise ValueError("wide-stop forward collector active without a valid epoch start")
    journal = _lane_journal(log_dir, ledger)
    balance, _peak = journal.get_account_state_since(ledger.starting_balance, epoch, for_date)
    broker = _lane_broker(balance)
    broker.restore_position(
        instrument=contract.INSTRUMENT,
        direction=str(position["direction"]),
        entry=float(position["entry"]),
        stop=float(position["stop"]),
        target=float(position["target"]),
        contracts=contract.CONTRACTS,
        paper_order_id=position.get("paper_order_id"),
    )
    fill = broker.resolve_position(
        NextBarOHLC(high=float(bar["high"]), low=float(bar["low"]))
    )
    if fill is None:
        fill = resolve_paper_eod(
            broker,
            position,
            timestamp=current_ts,
            timeframe="5m",
            close=float(bar["close"]),
        )
    if fill is None:
        return None

    gross = round(float(fill.pnl_dollars or 0.0), 2)
    net = round(gross - float(contract.COMMISSION_ROUND_TRIP), 2)
    economic_result = _economic_result(net)
    journal.log_outcome(
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        result=economic_result,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        exit_reason=fill.exit_reason,
        pnl_ticks=fill.pnl_ticks,
        pnl_dollars=net,
        contracts=fill.contracts,
        for_date=for_date,
        strategy=str(position.get("strategy") or ""),
        signal_timestamp=str(position.get("entry_time") or ""),
        paper_order_id=getattr(fill, "paper_order_id", None),
    )
    portfolio.record_outcome(
        log_dir=log_dir,
        for_date=for_date,
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        strategy=str(position.get("strategy") or ""),
        result=economic_result,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        exit_reason=str(fill.exit_reason or "UNKNOWN"),
        pnl_ticks=float(fill.pnl_ticks or 0.0),
        net_pnl_dollars=net,
        contracts=fill.contracts,
        signal_timestamp=str(position.get("entry_time") or ""),
        paper_order_id=getattr(fill, "paper_order_id", None),
    )
    audit = contract.evaluate(cfg).audit(
        instrument=contract.INSTRUMENT,
        strategy=str(position.get("strategy") or ""),
        stop_ticks=abs(float(position["planned_entry"]) - float(position["stop"])) / 0.25,
        rr_ratio=float(position.get("rr_ratio") or 0.0),
    )
    audit.update(
        candidate_key=position.get("candidate_key"),
        collector="wide_stop_forward_v1",
        collector_event="OUTCOME",
        broker_result=fill.result,
        outcome_result=economic_result,
        exit_reason=fill.exit_reason,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        pnl_ticks=fill.pnl_ticks,
        gross_pnl_dollars=gross,
        commission_round_trip=contract.COMMISSION_ROUND_TRIP,
        net_pnl_dollars=net,
        valid_outcome=True,
        promotion_path=False,
    )
    _journal(log_dir, ledger, audit, for_date)
    state["position"] = None
    _save_state(log_dir, ledger, state)
    return audit


def _process_five_min_bar_locked(
    *, payload, cfg, bars_5m: list[dict], log_dir: str | Path, for_date: Optional[date]
) -> list[dict[str, Any]]:
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    if current_ts is None:
        return []
    day = _trading_date(current_ts, for_date)
    bar = {
        "high": float(payload.high),
        "low": float(payload.low),
        "close": float(payload.close),
    }
    events: list[dict[str, Any]] = []

    for ledger in contract.LEDGERS.values():
        resolved = _resolve_one_position(
            cfg=cfg,
            ledger=ledger,
            log_dir=log_dir,
            for_date=for_date,
            current_ts=current_ts,
            bar=bar,
        )
        if resolved is not None:
            events.append(resolved)

    for strategy in _NATIVE:
        decision, market_state, candidate = _evaluate_canonical_candidate(
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
        lane_state = _load_state(log_dir, ledger)
        _reset_day_counter(lane_state, day)
        key = _candidate_key(strategy, candidate)
        if key in lane_state["seen"]:
            continue
        _mark_seen(lane_state, key)

        if decision is None or decision.decision != "TRADE" or decision.setup is None:
            failed = (
                (decision.failed_gates or ["SIGNAL_GATE_REJECTED"])[0]
                if decision is not None
                else "SIGNAL_GATE_REJECTED"
            )
            audit = _record_block(
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
            _save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        if lane_state.get("position") is not None:
            audit = _record_block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="BLOCKED_OPEN_POSITION",
                failed_rule="max_open_positions",
                reason="hypothetical ledger already has an open position",
            )
            _save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        if int(lane_state.get("filled_count") or 0) >= MAX_FILLED_PER_DAY:
            audit = _record_block(
                cfg=cfg,
                ledger=ledger,
                strategy=strategy,
                candidate=candidate,
                key=key,
                log_dir=log_dir,
                for_date=for_date,
                lane_result="BLOCKED_MAX_TRADES",
                failed_rule="max_trades_per_day",
                reason=(
                    f"hypothetical ledger already has {MAX_FILLED_PER_DAY} filled trades today"
                ),
            )
            _save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        snap = portfolio.admission_snapshot(log_dir, day)
        if snap["open_positions"] >= portfolio.MAX_OPEN_POSITIONS:
            audit = _record_block(
                cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                log_dir=log_dir, for_date=for_date,
                lane_result="BLOCKED_PORTFOLIO_OPEN_POSITIONS",
                failed_rule="portfolio_max_open_positions",
                reason=f"day-strategy family already has {portfolio.MAX_OPEN_POSITIONS} open positions",
            )
            _save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue
        if snap["daily_slots_used"] >= portfolio.MAX_FILLS_PER_DAY:
            audit = _record_block(
                cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                log_dir=log_dir, for_date=for_date,
                lane_result="BLOCKED_PORTFOLIO_MAX_TRADES",
                failed_rule="portfolio_max_trades_per_day",
                reason=f"day-strategy family already uses {portfolio.MAX_FILLS_PER_DAY} daily slots",
            )
            _save_state(log_dir, ledger, lane_state)
            events.append(audit)
            continue

        setup = _trade_setup(market_state, decision)
        lane_daily = _lane_daily_state(cfg, ledger, log_dir, for_date)
        global_risk = RiskEngine(
            config=cfg,
            schedule_mode=getattr(cfg, "schedule_mode", "current"),
        ).validate(setup, lane_daily)

        overridable = bool(global_risk.approved) or contract.global_rejection_is_overridable(
            global_risk.failed_rule
        )
        if overridable:
            proposed_risk = portfolio.risk_dollars(
                instrument=setup.instrument,
                entry=setup.entry,
                stop=setup.stop,
                contracts=contract.CONTRACTS,
            )
            risk_ok, combined = portfolio.proposed_risk_allowed(log_dir, proposed_risk)
            if not risk_ok:
                audit = _record_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    lane_result="BLOCKED_PORTFOLIO_RISK",
                    failed_rule="portfolio_combined_open_risk",
                    reason=(
                        f"combined planned risk ${combined:.2f} exceeds "
                        f"${portfolio.MAX_COMBINED_OPEN_RISK_DOLLARS:.2f}"
                    ),
                )
                _save_state(log_dir, ledger, lane_state)
                events.append(audit)
                continue

            reserved, reserve_reason = portfolio.reserve_daily_slot(
                log_dir, day, key, route="paper_sim"
            )
            if not reserved:
                audit = _record_block(
                    cfg=cfg, ledger=ledger, strategy=strategy, candidate=candidate, key=key,
                    log_dir=log_dir, for_date=for_date,
                    lane_result="BLOCKED_PORTFOLIO_MAX_TRADES",
                    failed_rule="portfolio_daily_slot",
                    reason=reserve_reason,
                )
                _save_state(log_dir, ledger, lane_state)
                events.append(audit)
                continue
        else:
            reserved = False

        audit = observe_candidate(
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
            if reserved:
                portfolio.release_daily_slot(log_dir, day, key)
            _save_state(log_dir, ledger, lane_state)
            continue
        audit["collector"] = "wide_stop_forward_v1"
        audit["collector_event"] = "CANDIDATE"
        if audit.get("fill_status") == "OPEN" and audit.get("fill_price") is not None:
            if reserved:
                portfolio.confirm_daily_slot(log_dir, day, key)
            lane_state["position"] = _position_record(
                strategy=strategy,
                setup=setup,
                audit=audit,
                candidate_key=key,
                day=day,
            )
            lane_state["filled_count"] = int(lane_state.get("filled_count") or 0) + 1
            lane_state["filled_date"] = day.isoformat()
        elif reserved:
            # PaperBroker is local/deterministic: a non-OPEN result cannot hide an
            # external fill, so the slot is safe to release.
            portfolio.release_daily_slot(log_dir, day, key)
        _save_state(log_dir, ledger, lane_state)
        audit["portfolio"] = portfolio.admission_snapshot(log_dir, day)
        events.append(audit)

    return events


def process_five_min_bar(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    log_dir: str | Path,
    for_date: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Resolve prior positions, then observe canonical candidates on one 5m bar."""
    if not contract.evaluate(cfg).active:
        return []
    if _root(getattr(payload, "ticker", None)) != contract.INSTRUMENT:
        return []
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    if current_ts is None:
        return []
    if _epoch(cfg) is None:
        raise ValueError("wide-stop forward collector active without a valid epoch start")

    with _collector_lock(log_dir):
        return _process_five_min_bar_locked(
            payload=payload,
            cfg=cfg,
            bars_5m=bars_5m,
            log_dir=log_dir,
            for_date=for_date,
        )
