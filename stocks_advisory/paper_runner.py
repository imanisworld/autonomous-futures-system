"""stocks_advisory/paper_runner.py

Orchestrator for the TQQQ/SQQQ Paper Advisory Bot v1 forward
paper-proof harness. Ties together `qqq_signal_builder.py` (bars ->
QQQSignalInput), `tqqq_sqqq_decision.py` (the locked decision engine,
untouched), `paper_simulator.py` (lifecycle resolution), and
`paper_journal.py` (append-only persistence) into a single once-per-day
entry point. No broker, order, execution, futures, or options_manager
import of any kind; nothing here places, prepares, or queues an order.

Scope, deliberately: this harness is designed to be run ONCE per
trading day, given that day's ENTIRE regular-session bar history for
QQQ/TQQQ/SQQQ (typically run at or after the close). A single call
both makes the day's decision (if not already made) AND, if the
verdict is TAKE_PAPER, resolves that plan's lifecycle to a terminal
state (ACTIVE->EXITED, or EXPIRED if it never confirmed/invalidated)
using the rest of that same day's bars. This sidesteps a class of
same-day re-invocation bugs a partial-bars-per-run design would
otherwise require careful "already consumed" bookkeeping to avoid; it
is a real, explicit scope limitation of v1, not a hidden gap (see
`REPORT.md`/PR description for how a later increment could add safe
intra-day incremental runs).

Cross-day behavior: any WATCHING/ACTIVE position still open from a
PRIOR trade_date (e.g. the harness was not run at all on some earlier
day) is force-resolved to EXPIRED before anything else happens on this
run -- consistent with `tqqq_sqqq_decision.py`'s own "no overnight
hold" invariant. It is never silently dropped (append-only journal),
and it is never given a fabricated exit price from a different day's
data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from .backtest_models import Bar
from .paper_journal import (
    PaperJournalRecord,
    append_record,
    has_decision_for,
    latest_open_positions,
)
from .paper_simulator import LifecycleState, advance_lifecycle
from .qqq_signal_builder import _opening_range_bars, build_qqq_signal
from .tqqq_sqqq_decision import evaluate_tqqq_sqqq_decision
from .tqqq_sqqq_models import TqqqSqqqDirection, TqqqSqqqVerdict

STRATEGY_VERSION = "tqqq_sqqq_decision_v1"
"""Dedup/version key for the journal. Locked for the duration of the
paper-proof window -- changing the decision engine's behavior requires
a new version string, never a silent overwrite of this one's history."""


@dataclass(frozen=True, kw_only=True)
class RunResult:
    ok: bool
    message: str
    journaled: bool = False
    decision: Optional[str] = None
    final_status: Optional[str] = None
    net_pnl_dollars: Optional[float] = None
    fee_only_net_pnl_dollars: Optional[float] = None
    """gross_pnl_dollars - regulatory_fees_dollars only -- excludes
    modeled slippage. Reporting-only comparison value; `net_pnl_dollars`
    (which includes the full locked friction model) is the proof
    metric, never this one."""
    resolved_prior_positions: tuple[str, ...] = field(default_factory=tuple)


def _vehicle_bars_for(direction: TqqqSqqqDirection, tqqq_bars: Sequence[Bar], sqqq_bars: Sequence[Bar]) -> Sequence[Bar]:
    if direction == TqqqSqqqDirection.LONG_TQQQ:
        return tqqq_bars
    if direction == TqqqSqqqDirection.LONG_SQQQ:
        return sqqq_bars
    return ()


def _resolve_stale_prior_positions(
    *, journal_path: Path, date: str, recorded_at: str, data_source: str
) -> list[str]:
    """Force-closes (EXPIRED, zero P&L) any journaled WATCHING/ACTIVE
    position whose trade_date is not `date` -- this trading day's own
    bars are unrelated to a different day's position, so it is never
    fed cross-day bars; it is only ever given `session_closed=True`
    with no new bars, matching `tqqq_sqqq_decision.py`'s no-overnight-
    hold invariant."""
    resolved: list[str] = []
    for position in latest_open_positions(journal_path, STRATEGY_VERSION):
        if position.trade_date == date:
            continue
        state = LifecycleState(
            trade_date=position.trade_date,
            direction=position.direction,
            vehicle_symbol=position.vehicle_symbol,
            stop_price_qqq=position.stop_price if position.stop_price is not None else 0.0,
            status=position.status,
            target_1=position.target_1,
            raw_entry_price=position.raw_entry_price,
            entry_price=position.modeled_entry_price,
            entry_time=position.entry_time,
            shares=position.shares,
            entry_slippage_dollars=position.entry_slippage_dollars,
        )
        advanced = advance_lifecycle(state, qqq_bars=(), vehicle_bars=(), session_closed=True)
        if not advanced.ok or advanced.state is None:
            # Fail closed: leave the stale position exactly as journaled rather
            # than guessing; the operator sees it via has_decision_for on a
            # future run and can investigate.
            continue
        record = PaperJournalRecord(
            trade_date=position.trade_date,
            strategy_version=STRATEGY_VERSION,
            recorded_at=recorded_at,
            data_source=data_source,
            signal_symbol=position.signal_symbol,
            qqq_price=position.qqq_price,
            direction=position.direction,
            vehicle_symbol=position.vehicle_symbol,
            decision=position.decision,
            reason=position.reason,
            entry_trigger=position.entry_trigger,
            stop_price=position.stop_price,
            target_1=position.target_1,
            target_2=position.target_2,
            status=advanced.state.status,
            raw_entry_price=advanced.state.raw_entry_price,
            modeled_entry_price=advanced.state.entry_price,
            entry_time=advanced.state.entry_time,
            raw_exit_price=advanced.state.raw_exit_price,
            modeled_exit_price=advanced.state.exit_price,
            exit_time=advanced.state.exit_time,
            exit_reason=advanced.state.exit_reason,
            shares=advanced.state.shares,
            entry_slippage_dollars=advanced.state.entry_slippage_dollars,
            exit_slippage_dollars=advanced.state.exit_slippage_dollars,
            regulatory_fees_dollars=advanced.state.regulatory_fees_dollars,
            total_friction_dollars=advanced.state.total_friction_dollars,
            gross_pnl_dollars=advanced.state.gross_pnl_dollars,
            net_pnl_dollars=advanced.state.net_pnl_dollars,
            notes="force-closed: carried open past its own session with no overnight hold",
        )
        append_record(journal_path, record)
        resolved.append(position.trade_date)
    return resolved


def run_paper_session(
    *,
    date: str,
    qqq_bars_full_day: Sequence[Bar],
    tqqq_bars_full_day: Sequence[Bar],
    sqqq_bars_full_day: Sequence[Bar],
    qqq_previous_day_close: float,
    qqq_previous_day_high: float,
    qqq_previous_day_low: float,
    qqq_relative_volume: float,
    allowed_max_gap_percent: float,
    allowed_min_first_hour_range: float,
    allowed_max_first_hour_range: float,
    journal_path: Path,
    recorded_at: str,
    data_source: str,
    market_regime_label: Optional[str] = None,
) -> RunResult:
    """Runs one full day's paper-proof session. `qqq_bars_full_day` /
    `tqqq_bars_full_day` / `sqqq_bars_full_day` are that day's entire
    regular-session bars, ascending, index-aligned by timestamp (the
    same convention `DaySession` already establishes). `recorded_at` is
    the caller-supplied "now" -- this module never reads the system
    clock itself.

    Position sizing is not a parameter here -- `advance_lifecycle`
    always uses the locked `DEFAULT_POSITION_DOLLAR_SIZE`; this harness
    exposes no runtime way to change it.
    """
    resolved_prior = _resolve_stale_prior_positions(
        journal_path=journal_path,
        date=date,
        recorded_at=recorded_at,
        data_source=data_source,
    )

    if has_decision_for(journal_path, date, STRATEGY_VERSION):
        return RunResult(
            ok=True,
            journaled=False,
            message=f"decision already journaled for {date} (strategy_version={STRATEGY_VERSION}); nothing further to do",
            resolved_prior_positions=tuple(resolved_prior),
        )

    opening_range = _opening_range_bars(qqq_bars_full_day)
    if not opening_range or len(qqq_bars_full_day) <= len(opening_range):
        return RunResult(
            ok=False,
            journaled=False,
            message="first trading hour has not closed yet -- no confirming bar available; re-run later",
            resolved_prior_positions=tuple(resolved_prior),
        )

    decision_cutoff = len(opening_range) + 1
    decision_qqq_bars = qqq_bars_full_day[:decision_cutoff]
    remaining_qqq_bars = qqq_bars_full_day[decision_cutoff:]

    build_result = build_qqq_signal(
        date=date,
        qqq_bars_today=decision_qqq_bars,
        qqq_previous_day_close=qqq_previous_day_close,
        qqq_previous_day_high=qqq_previous_day_high,
        qqq_previous_day_low=qqq_previous_day_low,
        qqq_relative_volume=qqq_relative_volume,
        allowed_max_gap_percent=allowed_max_gap_percent,
        allowed_min_first_hour_range=allowed_min_first_hour_range,
        allowed_max_first_hour_range=allowed_max_first_hour_range,
        market_regime_label=market_regime_label,
    )
    if not build_result.ok:
        record = PaperJournalRecord(
            trade_date=date,
            strategy_version=STRATEGY_VERSION,
            recorded_at=recorded_at,
            data_source=data_source,
            signal_symbol="QQQ",
            qqq_price=decision_qqq_bars[-1].close if decision_qqq_bars else 0.0,
            direction="",
            vehicle_symbol="",
            decision="INVALID",
            reason=build_result.reject_reason,
            status="",
        )
        append_record(journal_path, record)
        return RunResult(
            ok=True,
            journaled=True,
            decision="INVALID",
            message=build_result.reject_reason,
            resolved_prior_positions=tuple(resolved_prior),
        )

    decision_result = evaluate_tqqq_sqqq_decision(build_result.signal)
    trade = decision_result.trade
    if decision_result.verdict == TqqqSqqqVerdict.INVALID or trade is None:
        record = PaperJournalRecord(
            trade_date=date,
            strategy_version=STRATEGY_VERSION,
            recorded_at=recorded_at,
            data_source=data_source,
            signal_symbol="QQQ",
            qqq_price=build_result.signal.qqq_current_price,
            direction="",
            vehicle_symbol="",
            decision="INVALID",
            reason="; ".join(decision_result.blocking_reasons) or "decision engine returned INVALID",
            status="",
        )
        append_record(journal_path, record)
        return RunResult(
            ok=True,
            journaled=True,
            decision="INVALID",
            message=record.reason,
            resolved_prior_positions=tuple(resolved_prior),
        )

    decision_label = "TRADE" if decision_result.verdict == TqqqSqqqVerdict.TAKE_PAPER else "NO_TRADE"
    decision_record = PaperJournalRecord(
        trade_date=date,
        strategy_version=STRATEGY_VERSION,
        recorded_at=recorded_at,
        data_source=data_source,
        signal_symbol=trade.signal_symbol,
        qqq_price=build_result.signal.qqq_current_price,
        direction=trade.direction.value,
        vehicle_symbol=trade.vehicle_symbol,
        decision=decision_label,
        reason=trade.reason,
        entry_trigger=trade.entry_trigger,
        stop_price=trade.stop_price,
        target_1=trade.target_1,
        target_2=trade.target_2,
        status=trade.status.value,
    )
    append_record(journal_path, decision_record)

    if decision_result.verdict != TqqqSqqqVerdict.TAKE_PAPER:
        return RunResult(
            ok=True,
            journaled=True,
            decision=decision_label,
            final_status=decision_record.status,
            message=trade.reason,
            resolved_prior_positions=tuple(resolved_prior),
        )

    vehicle_bars_full = _vehicle_bars_for(trade.direction, tqqq_bars_full_day, sqqq_bars_full_day)
    remaining_vehicle_bars = vehicle_bars_full[decision_cutoff:]
    if len(remaining_qqq_bars) != len(remaining_vehicle_bars):
        return RunResult(
            ok=False,
            journaled=True,
            decision=decision_label,
            message=(
                "decision journaled, but QQQ/vehicle bar alignment mismatch after the "
                "decision cutoff -- lifecycle could not be resolved this run"
            ),
            resolved_prior_positions=tuple(resolved_prior),
        )

    lifecycle_state = LifecycleState(
        trade_date=date,
        direction=trade.direction.value,
        vehicle_symbol=trade.vehicle_symbol,
        stop_price_qqq=trade.stop_price if trade.stop_price is not None else 0.0,
        status="watching",
        target_1=trade.target_1,
    )
    advance_result = advance_lifecycle(
        lifecycle_state,
        qqq_bars=remaining_qqq_bars,
        vehicle_bars=remaining_vehicle_bars,
        session_closed=True,
    )
    if not advance_result.ok or advance_result.state is None:
        return RunResult(
            ok=False,
            journaled=True,
            decision=decision_label,
            message=f"decision journaled, but lifecycle resolution failed: {advance_result.reject_reason}",
            resolved_prior_positions=tuple(resolved_prior),
        )

    final_state = advance_result.state
    lifecycle_record = PaperJournalRecord(
        trade_date=date,
        strategy_version=STRATEGY_VERSION,
        recorded_at=recorded_at,
        data_source=data_source,
        signal_symbol=trade.signal_symbol,
        qqq_price=build_result.signal.qqq_current_price,
        direction=trade.direction.value,
        vehicle_symbol=trade.vehicle_symbol,
        decision=decision_label,
        reason=trade.reason,
        entry_trigger=trade.entry_trigger,
        stop_price=trade.stop_price,
        target_1=trade.target_1,
        target_2=trade.target_2,
        status=final_state.status,
        raw_entry_price=final_state.raw_entry_price,
        modeled_entry_price=final_state.entry_price,
        entry_time=final_state.entry_time,
        raw_exit_price=final_state.raw_exit_price,
        modeled_exit_price=final_state.exit_price,
        exit_time=final_state.exit_time,
        exit_reason=final_state.exit_reason,
        shares=final_state.shares,
        entry_slippage_dollars=final_state.entry_slippage_dollars,
        exit_slippage_dollars=final_state.exit_slippage_dollars,
        regulatory_fees_dollars=final_state.regulatory_fees_dollars,
        total_friction_dollars=final_state.total_friction_dollars,
        gross_pnl_dollars=final_state.gross_pnl_dollars,
        net_pnl_dollars=final_state.net_pnl_dollars,
    )
    append_record(journal_path, lifecycle_record)

    fee_only_net_pnl_dollars: Optional[float] = None
    if final_state.gross_pnl_dollars is not None and final_state.regulatory_fees_dollars is not None:
        fee_only_net_pnl_dollars = final_state.gross_pnl_dollars - final_state.regulatory_fees_dollars

    return RunResult(
        ok=True,
        journaled=True,
        decision=decision_label,
        final_status=final_state.status,
        net_pnl_dollars=final_state.net_pnl_dollars,
        fee_only_net_pnl_dollars=fee_only_net_pnl_dollars,
        message=f"{decision_label} -> {final_state.status}",
        resolved_prior_positions=tuple(resolved_prior),
    )
