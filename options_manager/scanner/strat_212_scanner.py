"""options_manager/scanner/strat_212_scanner.py

Advisory-only 2-1-2 watchlist scanner — Increment 9. Scans caller-
supplied WatchlistRow entries by calling
options_manager.strategies.evaluate_strat_212() directly, per row, and
translates each StrategySignal into the scanner's own
TRIGGERED/WATCH/INVALID/NO_TRADE vocabulary.

Per the Increment 8 audit's decisions: this module calls
evaluate_strat_212() directly rather than going through
options_manager.replay's own batch-replay entry point (replay's
outcome-resolution layer only makes sense against a *known* future price
snapshot, which a live scan never has), never reimplements strategy logic,
does not rank
or score results against each other, does not send alerts, does not
fetch a quote/option chain/market data, and performs no I/O of any kind.

NO_TRADE vs INVALID: NO_TRADE means no actionable 2-1-2 setup exists for
the requested direction on this ticker right now (the bars simply don't
form one, or form the opposite direction's continuation) -- a caller
should not treat this as an error, just "nothing to see here." INVALID
means the row was missing required data, or an explicit market-context/
contract-constraints/target check rejected it -- a caller should treat
this as "this setup could not be safely evaluated or was rejected,"
which is a materially different signal than "there is no setup."
"""

from __future__ import annotations

from typing import Iterable

from options_manager.strategies import StrategySignal, evaluate_strat_212

from .base import ScanReport, ScanResult, ScanStatus, WatchlistRow

# Reason codes representing "no actionable setup exists for the
# requested direction," not a data/safety failure. Both are raised by
# evaluate_strat_212() before any entry/invalidation/target/context/
# contract check is ever reached, so a row landing here is, by
# construction, structurally complete and safe -- it just isn't a 2-1-2
# setup in the requested direction.
_NO_TRADE_REASON_CODES = frozenset(
    {
        "sequence_not_212",
        "direction_mismatch",
    }
)


def _classify_scan_status(signal: StrategySignal) -> ScanStatus:
    """Fail-closed mapping from a StrategySignal to a ScanStatus. Any
    reason_code not explicitly recognized as a "no setup" case defaults
    to INVALID -- never to TRIGGERED or NO_TRADE by default."""
    if signal.status == "VALID":
        return "TRIGGERED"
    if signal.status == "WATCH":
        return "WATCH"
    if signal.reason_code in _NO_TRADE_REASON_CODES:
        return "NO_TRADE"
    return "INVALID"


def _evaluate_row(row: WatchlistRow) -> ScanResult:
    if row.exclude:
        return ScanResult(
            ticker=row.ticker,
            timestamp=row.timestamp,
            scan_status="NO_TRADE",
            strategy_status=None,
            reason_code="excluded",
            reason="row explicitly excluded by caller",
        )

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

    return ScanResult(
        ticker=row.ticker,
        timestamp=row.timestamp,
        scan_status=_classify_scan_status(signal),
        strategy_status=signal.status,
        reason_code=signal.reason_code,
        reason=signal.reason,
        signal=signal,
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


def scan_watchlist_strat_212(rows: Iterable[WatchlistRow]) -> ScanReport:
    """Pure function of its explicit inputs -> ScanReport. Does not fetch
    anything; `rows` must already carry everything needed to evaluate
    each ticker's 2-1-2 setup."""
    results = [_evaluate_row(row) for row in rows]

    triggered = sum(1 for r in results if r.scan_status == "TRIGGERED")
    watch = sum(1 for r in results if r.scan_status == "WATCH")
    invalid = sum(1 for r in results if r.scan_status == "INVALID")
    no_trade = sum(1 for r in results if r.scan_status == "NO_TRADE")

    counts_by_status = {
        "TRIGGERED": triggered,
        "WATCH": watch,
        "INVALID": invalid,
        "NO_TRADE": no_trade,
    }

    counts_by_reason: dict[str, int] = {}
    for r in results:
        counts_by_reason[r.reason_code] = counts_by_reason.get(r.reason_code, 0) + 1

    return ScanReport(
        total_rows=len(results),
        triggered=triggered,
        watch=watch,
        invalid=invalid,
        no_trade=no_trade,
        results=results,
        counts_by_status=counts_by_status,
        counts_by_reason=counts_by_reason,
    )
