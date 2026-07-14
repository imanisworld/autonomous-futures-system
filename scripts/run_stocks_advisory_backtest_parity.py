#!/usr/bin/env python3
"""scripts/run_stocks_advisory_backtest_parity.py

Manual, once-per-day CLI for the TQQQ/SQQQ Historical-Engine Parity
build (`stocks_advisory/tqqq_sqqq_backtest_parity.py`). Not a scheduler
and not a daemon -- invoke this by hand, once, for one trading day, once
that day's complete regular-hours QQQ/TQQQ/SQQQ bars are available.

This is NOT the v1 paper-harness CLI (`run_stocks_advisory_paper.py`,
which drives the now-rejected `tqqq_sqqq_decision.py`) and NOT a v2 CLI
(`tqqq_sqqq_backtest_v2.py`'s Lane 1/Lane 2, v2 rejected as a freeze
candidate). It drives `run_parity_day()`, which calls the ORIGINAL,
unmodified `tqqq_sqqq_backtest.evaluate_day()` twice (gross and
friction-adjusted) -- the exact engine that produced the +$4.08/trade,
290-trade result this build exists to reproduce, not redesign.

Reuses `stocks_advisory.csv_loader.load_bars_from_csv` and
`build_day_sessions` (both unmodified). This script performs no network
call, no broker/execution/futures/options_manager import, and never
places, prepares, or queues an order; it only reads the CSV paths given
on the command line and appends to the journal file given on the command
line.

Building and running this CLI does NOT declare the parity engine the
official forward-proof source -- that remains a separate decision, gated
on review of the parity report this script's output feeds.

Fail-closed: any CSV validation error or session-build exclusion for the
requested date prints a clear reason to stderr and exits non-zero --
never guesses, never proceeds partially.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocks_advisory.backtest_models import SkippedDay
from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.paper_journal import PaperJournalRecord, append_record, has_decision_for
from stocks_advisory.tqqq_sqqq_backtest_parity import STRATEGY_VERSION, run_parity_day


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qqq-csv", required=True, help="Path to a QQQ 5-minute bar CSV")
    parser.add_argument("--tqqq-csv", required=True, help="Path to a TQQQ 5-minute bar CSV")
    parser.add_argument("--sqqq-csv", required=True, help="Path to an SQQQ 5-minute bar CSV")
    parser.add_argument("--date", required=True, help="Trading day to evaluate, YYYY-MM-DD")
    parser.add_argument(
        "--journal", required=True,
        help="Path to the append-only parity-engine journal (JSONL). Created if absent.",
    )
    parser.add_argument("--recorded-at", default=None, help="ISO-8601 timestamp to stamp the journal entry with")
    parser.add_argument("--data-source", default=None, help="Free-text label for where these bars came from")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        qqq_csv = load_bars_from_csv(args.qqq_csv)
        tqqq_csv = load_bars_from_csv(args.tqqq_csv)
        sqqq_csv = load_bars_from_csv(args.sqqq_csv)
    except Exception as exc:  # csv_loader.CsvValidationError, unmodified
        print(f"CSV validation error: {exc}", file=sys.stderr)
        return 2

    sessions, report = build_day_sessions(qqq_csv, tqqq_csv, sqqq_csv)
    session = next((s for s in sessions if s.date == args.date), None)
    if session is None:
        excluded_reason = next((reason for date, reason in report.excluded_dates if date == args.date), None)
        if excluded_reason:
            print(f"{args.date} excluded from session build: {excluded_reason}", file=sys.stderr)
        else:
            print(f"{args.date} not present in the supplied CSVs at all", file=sys.stderr)
        return 2

    journal_path = Path(args.journal)
    if has_decision_for(journal_path, args.date, STRATEGY_VERSION):
        print(f"decision already journaled for {args.date} (strategy_version={STRATEGY_VERSION}); not rerunning")
        return 0

    result = run_parity_day(session)

    recorded_at = args.recorded_at
    if recorded_at is None:
        from datetime import datetime, timezone

        recorded_at = datetime.now(timezone.utc).isoformat()

    data_source = args.data_source or (
        f"csv:{Path(args.qqq_csv).name}+{Path(args.tqqq_csv).name}+{Path(args.sqqq_csv).name}"
    )

    if isinstance(result, SkippedDay):
        record = PaperJournalRecord(
            trade_date=args.date,
            strategy_version=STRATEGY_VERSION,
            recorded_at=recorded_at,
            data_source=data_source,
            signal_symbol="QQQ",
            qqq_price=0.0,
            direction="",
            vehicle_symbol="",
            decision="INVALID",
            reason=result.reason,
            status="",
        )
        append_record(journal_path, record)
        print(f"date: {args.date}\ndecision: INVALID\nreason: {result.reason}")
        return 1

    gross = result.gross
    friction = result.friction_adjusted
    decision = "NO_TRADE" if gross.skipped else "TRADE"

    record = PaperJournalRecord(
        trade_date=args.date,
        strategy_version=STRATEGY_VERSION,
        recorded_at=recorded_at,
        data_source=data_source,
        signal_symbol="QQQ",
        qqq_price=0.0,
        direction=gross.direction.value,
        vehicle_symbol=gross.vehicle_symbol,
        decision=decision,
        reason=gross.skipped_reason or gross.exit_reason,
        entry_trigger="",
        raw_entry_price=gross.entry_price,
        modeled_entry_price=friction.entry_price,
        entry_time=gross.entry_time,
        raw_exit_price=gross.exit_price,
        modeled_exit_price=friction.exit_price,
        exit_time=gross.exit_time,
        exit_reason=gross.exit_reason,
        gross_pnl_dollars=gross.dollar_result,
        net_pnl_dollars=friction.dollar_result,
        status="exited" if not gross.skipped else "no_trade",
        notes=f"friction_adjusted_regulatory_fees_dollars={result.friction_adjusted_regulatory_fees_dollars}",
    )
    append_record(journal_path, record)

    print(f"strategy_version:  {STRATEGY_VERSION}")
    print(f"date:               {args.date}")
    print(f"decision:           {decision}")
    print(f"direction:          {gross.direction.value if not gross.skipped else ''}")
    print(f"gross_pnl_dollars:  {gross.dollar_result}  <- reproduces the original evidence-report methodology")
    print(f"net_pnl_dollars:    {friction.dollar_result}  <- 0.15% slippage + real Robinhood regulatory fees")
    print(f"exit_reason:        {gross.exit_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
