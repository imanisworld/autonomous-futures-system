"""options_manager/replay/strat_212_replay.py

Advisory-only 2-1-2 replay wrapper — Increment 5. Replays caller-
supplied historical rows through options_manager.strategies.evaluate_strat_212()
and reports, per row and in aggregate, what the strategy said and
(when future price data is supplied) whether the resulting setup would
have hit its target or its stop.

Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
symbol scanning, no option-premium simulation, no paper trade
submission. Does not import replay/replay_engine.py or
replay/candle_loader.py — every row already carries whatever data it
needs. Does not modify options_manager/strategies/strat_212.py; this is
a pure consumer of evaluate_strat_212().
"""

from __future__ import annotations

from typing import Iterable

from options_manager.strategies import evaluate_strat_212

from .base import Strat212ReplayReport, Strat212ReplayResult, Strat212ReplayRow, ReplayOutcomeStatus


def _resolve_outcome(row: Strat212ReplayRow, target_1, target_2, invalidation) -> tuple[ReplayOutcomeStatus, str]:
    """Fails closed to NO_OUTCOME_DATA when no future price data was
    supplied at all. Otherwise: a target hit requires future_high (CALL)
    or future_low (PUT) to reach the target level; a stop hit requires
    the opposite side to reach the invalidation level. If both a target
    and the stop are hit within the same supplied future bar, this
    resolves conservatively to STOP_HIT rather than assuming the target
    was reached first -- intrabar sequencing is not knowable from a
    single high/low snapshot."""
    if row.future_high is None and row.future_low is None:
        return "NO_OUTCOME_DATA", "no future_high/future_low supplied"

    if row.direction == "CALL":
        target_1_hit = (
            row.future_high is not None and target_1 is not None and row.future_high >= target_1
        )
        target_2_hit = (
            row.future_high is not None and target_2 is not None and row.future_high >= target_2
        )
        stop_hit = (
            row.future_low is not None
            and invalidation is not None
            and row.future_low <= invalidation
        )
    else:
        target_1_hit = (
            row.future_low is not None and target_1 is not None and row.future_low <= target_1
        )
        target_2_hit = (
            row.future_low is not None and target_2 is not None and row.future_low <= target_2
        )
        stop_hit = (
            row.future_high is not None
            and invalidation is not None
            and row.future_high >= invalidation
        )

    if stop_hit and (target_1_hit or target_2_hit):
        return (
            "STOP_HIT",
            "target and stop both hit in the same future bar; resolved "
            "conservatively to STOP_HIT",
        )
    if target_2_hit:
        return "TARGET_2_HIT", "future price reached target_2"
    if target_1_hit:
        return "TARGET_1_HIT", "future price reached target_1"
    if stop_hit:
        return "STOP_HIT", "future price reached the invalidation/stop level"
    return "OPEN", "future price data supplied but neither target nor stop was reached"


def _evaluate_row(row: Strat212ReplayRow) -> Strat212ReplayResult:
    signal = evaluate_strat_212(
        row.bars,
        direction=row.direction,
        entry_trigger=row.entry_trigger,
        underlying_invalidation=row.underlying_invalidation,
        target_1=row.target_1,
        target_2=row.target_2,
        level_inputs=row.level_inputs,
        market_context=row.market_context,
        market_context_inputs=row.market_context_inputs,
        contract_constraints=row.contract_constraints,
        contract_constraints_inputs=row.contract_constraints_inputs,
    )

    warnings = list(signal.warnings) + list(signal.context_warnings) + list(
        signal.contract_warnings
    )

    common = dict(
        ticker=row.ticker,
        timestamp=row.timestamp,
        status=signal.status,
        reason_code=signal.reason_code,
        entry=signal.entry_trigger,
        invalidation=signal.underlying_invalidation,
        target_1=signal.target_1,
        target_2=signal.target_2,
        rr_1=signal.rr_1,
        rr_2=signal.rr_2,
        context_status=signal.context_status,
        contract_status=signal.contract_status,
        warnings=warnings,
    )

    if signal.status == "INVALID":
        return Strat212ReplayResult(
            **common, replay_outcome="INVALID", outcome_reason=signal.reason_code
        )
    if signal.status == "WATCH":
        return Strat212ReplayResult(
            **common, replay_outcome="NOT_TRIGGERED", outcome_reason=signal.reason_code
        )

    outcome, outcome_reason = _resolve_outcome(
        row, signal.target_1, signal.target_2, signal.underlying_invalidation
    )
    return Strat212ReplayResult(**common, replay_outcome=outcome, outcome_reason=outcome_reason)


def replay_strat_212(rows: Iterable[Strat212ReplayRow]) -> Strat212ReplayReport:
    """Pure function of its explicit inputs -> Strat212ReplayReport. Does
    not fetch anything; `rows` must already carry everything needed to
    evaluate and, optionally, score each setup's outcome."""
    results = [_evaluate_row(row) for row in rows]

    valid_setups = sum(1 for r in results if r.status == "VALID")
    invalid_setups = sum(1 for r in results if r.status == "INVALID")
    watch_setups = sum(1 for r in results if r.status == "WATCH")
    target_1_hits = sum(1 for r in results if r.replay_outcome == "TARGET_1_HIT")
    target_2_hits = sum(1 for r in results if r.replay_outcome == "TARGET_2_HIT")
    stop_hits = sum(1 for r in results if r.replay_outcome == "STOP_HIT")
    no_outcome_data = sum(1 for r in results if r.replay_outcome == "NO_OUTCOME_DATA")

    resolved = target_1_hits + target_2_hits + stop_hits
    win_rate_target_1 = (target_1_hits + target_2_hits) / resolved if resolved else None

    rejection_counts_by_reason: dict[str, int] = {}
    for r in results:
        if r.status == "INVALID":
            rejection_counts_by_reason[r.reason_code] = (
                rejection_counts_by_reason.get(r.reason_code, 0) + 1
            )

    return Strat212ReplayReport(
        total_rows=len(results),
        valid_setups=valid_setups,
        invalid_setups=invalid_setups,
        watch_setups=watch_setups,
        target_1_hits=target_1_hits,
        target_2_hits=target_2_hits,
        stop_hits=stop_hits,
        no_outcome_data=no_outcome_data,
        win_rate_target_1=win_rate_target_1,
        rejection_counts_by_reason=rejection_counts_by_reason,
        results=results,
    )
