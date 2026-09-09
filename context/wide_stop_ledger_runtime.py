"""Runtime orchestration for the wide-stop hypothetical-ledger lane.

Kept separate from `context/wide_stop_ledger_paper.py` so the contract stays a
pure data/policy module. Everything here is additive and isolated: it reads and
writes only the lane's own journal, balance, daily state and `PaperBroker`, and
returns an audit dict. It never touches the real book's journal, balance, daily
state, broker, decision or risk result — the caller's `risk_result` is an input
here, never an output.

Spec: `docs/wide-stop-hypothetical-ledger-lane-spec-2026-09-07.md` §3-§6.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Optional

from context import wide_stop_ledger_paper as contract
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from journal.journal_logger import JournalLogger
from risk.risk_engine import RiskEngine, TradeSetup

logger = logging.getLogger(__name__)


def _epoch(cfg) -> Optional[datetime]:
    raw = contract.epoch_start(cfg)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _lane_journal(log_dir, ledger: contract.Ledger) -> JournalLogger:
    return JournalLogger(log_dir=str(contract.journal_dir(log_dir, ledger)))


def _lane_broker(balance: float) -> PaperBroker:
    """The frozen entry contract: 1 contract, 8-tick marketable IOC, static.

    `PaperBroker` refuses a fill landing beyond its own bracket on every entry
    path since #508, so an over-detached candidate is cancelled rather than
    opened — the §6 build precondition, enforced by the broker itself.
    """
    return PaperBroker(
        starting_balance=balance,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={contract.INSTRUMENT: contract.MARKETABLE_TICKS},
    )


def observe_candidate(
    *,
    cfg,
    setup: TradeSetup,
    global_risk_result,
    log_dir,
    for_date: Optional[_date] = None,
    market_price: Optional[float] = None,
    schedule_mode: Optional[str] = None,
    candidate_key: Optional[str] = None,
) -> Optional[dict]:
    """Evaluate one candidate on its hypothetical ledger. Returns an audit dict.

    Returns None when the lane is inactive or the candidate is not a member —
    the overwhelmingly common path, so the real book pays almost nothing for
    this. Any failure is caught and reported in the audit: a research lane must
    never be able to break the real book's decision.

    ``candidate_key`` is optional forward-collector identity only. It is written
    into this lane's own audit row and has no execution authority.
    """
    decision = contract.evaluate(cfg)
    if not decision.active:
        return None
    ledger = contract.ledger_for(setup.instrument, setup.strategy)
    if ledger is None:
        return None

    tick = 0.25
    stop_ticks = abs(float(setup.entry) - float(setup.stop)) / tick
    audit = decision.audit(
        instrument=setup.instrument,
        strategy=setup.strategy,
        stop_ticks=round(stop_ticks, 1),
        rr_ratio=float(setup.rr_ratio),
    )
    if candidate_key:
        audit["candidate_key"] = str(candidate_key)

    global_approved = bool(getattr(global_risk_result, "approved", False))
    global_rule = getattr(global_risk_result, "failed_rule", None)
    # Spec §3: the lane overturns max_stop_ticks / min_rr_ratio and nothing
    # else. Anything the global engine rejected for another reason stays
    # rejected here, whatever the lane's own gates would say.
    overridable = global_approved or contract.global_rejection_is_overridable(global_rule)
    audit.update(
        global_result="APPROVED" if global_approved else "REJECTED",
        global_failed_rule=global_rule,
        lane_may_consider=overridable,
    )
    if not overridable:
        audit["lane_result"] = "REJECTED_UPSTREAM"
        audit["lane_failed_rule"] = global_rule
        _journal(log_dir, ledger, audit, for_date)
        return audit

    try:
        return _evaluate_on_ledger(
            cfg=cfg, ledger=ledger, setup=setup, audit=audit, log_dir=log_dir,
            for_date=for_date, market_price=market_price, schedule_mode=schedule_mode,
        )
    except Exception as exc:  # pragma: no cover - never break the real book
        logger.warning("wide-stop ledger lane skipped: %s", exc)
        audit["lane_result"] = "LANE_ERROR"
        audit["lane_error"] = str(exc)
        return audit


def _evaluate_on_ledger(
    *, cfg, ledger, setup, audit, log_dir, for_date, market_price, schedule_mode
) -> dict:
    epoch = _epoch(cfg)
    if epoch is None:
        raise ValueError("wide-stop ledger lane active without a valid epoch start")

    journal = _lane_journal(log_dir, ledger)
    balance, peak = journal.get_account_state_since(
        ledger.starting_balance, epoch, for_date
    )
    daily_state = journal.get_daily_state(for_date)
    daily_state.account_balance = balance
    daily_state.account_peak_balance = peak

    lane_cfg = contract.lane_config(cfg, ledger)
    lane_setup = TradeSetup(
        direction=setup.direction, entry=setup.entry, stop=setup.stop,
        target=setup.target, rr_ratio=setup.rr_ratio, strategy=setup.strategy,
        instrument=setup.instrument, session=setup.session, notes=setup.notes,
        entry_time=setup.entry_time, contracts=contract.CONTRACTS,
        confluence_grade=setup.confluence_grade,
    )
    result = RiskEngine(
        config=lane_cfg,
        schedule_mode=schedule_mode or getattr(cfg, "schedule_mode", "current"),
    ).validate(lane_setup, daily_state)

    audit.update(
        ledger_balance=round(balance, 2),
        ledger_peak=round(peak, 2),
        lane_result=result.result,
        lane_failed_rule=result.failed_rule,
        lane_reason=result.reason,
    )

    if not result.approved:
        _journal(log_dir, ledger, audit, for_date)
        return audit
    if not contract.is_fill_eligible(setup.instrument, setup.strategy):
        # D5: Miyagi is journaled with the family caps recorded, never filled.
        audit["lane_result"] = "SHADOW_ONLY_NOT_FILLED"
        _journal(log_dir, ledger, audit, for_date)
        return audit
    if market_price is None:
        audit["lane_result"] = "NO_MARKET_PRICE"
        _journal(log_dir, ledger, audit, for_date)
        return audit

    broker = _lane_broker(balance)
    fill = broker.execute_bracket(
        BracketOrder(
            instrument=setup.instrument, direction=setup.direction,
            entry=setup.entry, stop=setup.stop, target=setup.target,
            rr_ratio=setup.rr_ratio, strategy=setup.strategy,
            contracts=contract.CONTRACTS,
        ),
        market_price=float(market_price),
    )
    audit.update(
        fill_status=fill.result,
        fill_price=fill.entry_price if fill.result == "OPEN" else None,
        fill_reason=fill.exit_reason,
        fill_paper_order_id=getattr(fill, "paper_order_id", None),
        # #508 refuses a fill beyond its own bracket. Counting it separately is
        # the §6 checkpoint requirement: never blended into net.
        invalid_at_fill=(fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"),
    )
    _journal(log_dir, ledger, audit, for_date)
    return audit


def _journal(log_dir, ledger: contract.Ledger, audit: dict, for_date) -> None:
    """Append to the lane's own journal root. Never the real book's."""
    try:
        _lane_journal(log_dir, ledger).log_decision(
            {
                "decision": "HYPOTHETICAL_LEDGER",
                "label": contract.LABEL,
                "ledger": ledger.name,
                "instrument": contract.INSTRUMENT,
                "wide_stop_ledger": audit,
            },
            for_date=for_date,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("wide-stop ledger journal append failed: %s", exc)
