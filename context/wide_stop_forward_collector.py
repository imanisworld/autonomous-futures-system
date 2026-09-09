"""Canonical forward collector for the isolated wide-stop hypothetical ledgers.

This module closes two gaps in the original wide-stop lane:

1. the active book can keep ``strat_4hr_retrigger`` / ``strat_322_first_live``
   out of ``enabled_concepts`` while this observer still evaluates their exact
   canonical 5-minute-native state machines; and
2. an IOC fill is persisted and resolved causally on later 5-minute bars, so
   the lane produces outcome evidence rather than entry-side evidence only.

Safety contract:
- paper_sim only; no Tradovate/live mode exists;
- the active config is copied, never mutated;
- only the lane-authorized family stop cap and R:R floor differ;
- strategy permission is opened only inside the isolated DecisionEngine copy;
- one hypothetical position per ledger, max three FILLED entries per day;
- all state/journal files live under ``logs/hypothetical_ledger/<ledger>``;
- 4HR and 3-2-2 use the existing pure canonical state machines, not a copied
  detector;
- stop/target resolution is through the real PaperBroker with pessimistic
  same-bar handling; both strategies use the canonical day-only 15:55 ET
  flatten if still open.

Nothing in this module can submit to an external broker or alter the caller's
real decision/risk/account state.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from context import wide_stop_ledger_paper as contract
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

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MAX_SEEN = 500
MAX_FILLED_PER_DAY = 3
FOUR_HR = "strat_4hr_retrigger"
THREE_TWO_TWO = "strat_322_first_live"
_NATIVE = (FOUR_HR, THREE_TWO_TWO)


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
    path = _state_path(log_dir, ledger)
    try:
        raw = json.loads(path.read_text())
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
    rr = reward / risk if risk > 0 else 0.0
    stop_ticks = risk / 0.25
    return stop_ticks, rr


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
    if key in state["seen"]:
        return
    state["seen"].append(key)
    state["seen"] = state["seen"][-MAX_SEEN:]


def _isolated_config(cfg, ledger: contract.Ledger, strategy: str):
    """Copy config and open only this member inside the observer.

    ``contract.lane_config`` applies the two authorized family changes. The
    strategy permission/enablement edits below exist only on this copy so the
    parked strategy can be evaluated without entering active ranking.
    """
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
    """Reconstruct the state immediately BEFORE ``current_ts`` causally.

    Only current-day bars beginning at the strategy's establishment boundary
    need to be advanced. Each canonical state machine independently filters the
    supplied history to bars whose close was already knowable at that step.
    """
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
    for ts, _ in ordered:
        persisted, _candidate = advance(
            bars_5m=bars_5m,
            current_bar_ts=ts,
            instrument=instrument,
            persisted_state=persisted,
        )
    return persisted


def _evaluate_canonical_candidate(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    strategy: str,
):
    """Run one parked strategy through its normal DecisionEngine gates.

    The detector and state transition are the same production functions. The
    only differences are the lane's pre-approved R:R floor and the isolated
    permission/enablement copy. Returns ``(decision, state, candidate)``.
    """
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
    # Entry exists at the signal bar close. Never resolve against price action
    # from that same bar; the next 5m bar opens exactly at entry_time.
    if current_ts < entry_time:
        return None

    current_day = current_ts.astimezone(ET).date()
    entry_day = entry_time.astimezone(ET).date()
    if current_day > entry_day or is_after_eod_close(current_ts):
        # The 15:55 bar was missing, so no defensible day-only exit price exists.
        # Fail closed rather than carry a day strategy overnight.
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

    gross = float(fill.pnl_dollars or 0.0)
    net = gross - float(contract.COMMISSION_ROUND_TRIP)
    journal.log_outcome(
        instrument=fill.instrument,
        session=str(position.get("session") or "new_york"),
        result=fill.result,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        exit_reason=fill.exit_reason,
        pnl_ticks=fill.pnl_ticks,
        pnl_dollars=gross,
        contracts=fill.contracts,
        for_date=for_date,
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
        outcome_result=fill.result,
        exit_reason=fill.exit_reason,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        pnl_ticks=fill.pnl_ticks,
        gross_pnl_dollars=round(gross, 2),
        commission_round_trip=contract.COMMISSION_ROUND_TRIP,
        net_pnl_dollars=round(net, 2),
        valid_outcome=True,
        promotion_path=False,
    )
    _journal(log_dir, ledger, audit, for_date)
    state["position"] = None
    _save_state(log_dir, ledger, state)
    return audit


def process_five_min_bar(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    log_dir: str | Path,
    for_date: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Resolve prior positions, then observe canonical candidates on this 5m bar.

    Returns audit rows for visibility/tests. Fail-soft behavior belongs to the
    caller (`context.five_min_feed.record_five_min`); this function raises on a
    malformed active configuration so that caller can log the evidence failure
    without affecting ingestion.
    """
    if not contract.evaluate(cfg).active:
        return []
    if _root(getattr(payload, "ticker", None)) != contract.INSTRUMENT:
        return []
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    if current_ts is None:
        return []
    if _epoch(cfg) is None:
        raise ValueError("wide-stop forward collector active without a valid epoch start")

    day = _trading_date(current_ts, for_date)
    bar = {
        "high": float(payload.high),
        "low": float(payload.low),
        "close": float(payload.close),
    }
    events: list[dict[str, Any]] = []

    # Resolve first. A position opened by the previous 5m close is eligible on
    # this bar; a newly-created position below cannot see this bar's earlier OHLC.
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
                reason=(decision.reason if decision is not None else "isolated signal evaluation unavailable"),
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
                reason=f"hypothetical ledger already has {MAX_FILLED_PER_DAY} filled trades today",
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
            _save_state(log_dir, ledger, lane_state)
            continue
        audit["collector"] = "wide_stop_forward_v1"
        audit["collector_event"] = "CANDIDATE"
        if audit.get("fill_status") == "OPEN" and audit.get("fill_price") is not None:
            lane_state["position"] = _position_record(
                strategy=strategy,
                setup=setup,
                audit=audit,
                candidate_key=key,
                day=day,
            )
            lane_state["filled_count"] = int(lane_state.get("filled_count") or 0) + 1
            lane_state["filled_date"] = day.isoformat()
        _save_state(log_dir, ledger, lane_state)
        events.append(audit)

    return events
