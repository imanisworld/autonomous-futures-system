#!/usr/bin/env python3
"""scripts/run_stocks_advisory_paper.py

Manual, once-per-day CLI for the TQQQ/SQQQ Paper Advisory Bot v1
forward paper-proof harness. Not a scheduler and not a daemon --
invoke this by hand, once, for one trading day, typically at or after
the close once that day's complete regular-hours QQQ/TQQQ/SQQQ bars are
available.

Reuses `stocks_advisory.csv_loader.load_bars_from_csv` and
`build_day_sessions` (both unmodified) for CSV parsing, regular-hours
filtering, and previous-day close/high/low derivation -- the exact same
tested code path the backtest lane already uses. This script itself
performs no network call, no broker/execution/futures/options_manager
import, and never places, prepares, or queues an order; it only reads
the CSV paths given on the command line and appends to the journal
file given on the command line.

Fail-closed: any CSV validation error, any session-build exclusion for
the requested date, or any harness-level rejection prints a clear
reason to stderr and exits non-zero -- never guesses, never proceeds
partially.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocks_advisory.csv_loader import CsvValidationError, build_day_sessions, load_bars_from_csv
from stocks_advisory.paper_runner import STRATEGY_VERSION, run_paper_session


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qqq-csv", required=True, help="Path to a QQQ 5-minute bar CSV")
    parser.add_argument("--tqqq-csv", required=True, help="Path to a TQQQ 5-minute bar CSV")
    parser.add_argument("--sqqq-csv", required=True, help="Path to an SQQQ 5-minute bar CSV")
    parser.add_argument("--date", required=True, help="Trading day to evaluate, YYYY-MM-DD")
    parser.add_argument(
        "--relative-volume",
        required=True,
        type=float,
        help="Today's QQQ relative volume, computed by the operator elsewhere -- "
        "this harness never computes a cross-day rolling average itself.",
    )
    parser.add_argument("--allowed-max-gap-percent", required=True, type=float)
    parser.add_argument("--allowed-min-first-hour-range", required=True, type=float)
    parser.add_argument("--allowed-max-first-hour-range", required=True, type=float)
    parser.add_argument("--market-regime-label", default=None)
    parser.add_argument(
        "--journal",
        required=True,
        help="Path to the append-only paper-proof journal (JSONL). Created if absent.",
    )
    parser.add_argument(
        "--recorded-at",
        default=None,
        help="ISO-8601 timestamp to stamp journal entries with. Defaults to the current "
        "system time if omitted -- this is the one place in the whole harness that reads "
        "the clock; every library module underneath stays clock-free.",
    )
    parser.add_argument("--data-source", default=None, help="Free-text label for where these bars came from")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    try:
        qqq_csv = load_bars_from_csv(args.qqq_csv)
        tqqq_csv = load_bars_from_csv(args.tqqq_csv)
        sqqq_csv = load_bars_from_csv(args.sqqq_csv)
    except CsvValidationError as exc:
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

    recorded_at = args.recorded_at
    if recorded_at is None:
        from datetime import datetime, timezone

        recorded_at = datetime.now(timezone.utc).isoformat()

    data_source = args.data_source or (
        f"csv:{Path(args.qqq_csv).name}+{Path(args.tqqq_csv).name}+{Path(args.sqqq_csv).name}"
    )

    kwargs = dict(
        date=session.date,
        qqq_bars_full_day=session.qqq_bars,
        tqqq_bars_full_day=session.tqqq_bars,
        sqqq_bars_full_day=session.sqqq_bars,
        qqq_previous_day_close=session.qqq_previous_close,
        qqq_previous_day_high=session.qqq_previous_high,
        qqq_previous_day_low=session.qqq_previous_low,
        qqq_relative_volume=args.relative_volume,
        allowed_max_gap_percent=args.allowed_max_gap_percent,
        allowed_min_first_hour_range=args.allowed_min_first_hour_range,
        allowed_max_first_hour_range=args.allowed_max_first_hour_range,
        journal_path=Path(args.journal),
        recorded_at=recorded_at,
        data_source=data_source,
        market_regime_label=args.market_regime_label,
    )

    result = run_paper_session(**kwargs)

    print(f"strategy_version:        {STRATEGY_VERSION}")
    print(f"date:                    {session.date}")
    print(f"ok:                      {result.ok}")
    print(f"journaled:               {result.journaled}")
    print(f"decision:                {result.decision}")
    print(f"final_status:            {result.final_status}")
    print(f"fee_only_net_pnl_dollars: {result.fee_only_net_pnl_dollars}")
    print(f"net_pnl_dollars:         {result.net_pnl_dollars}  <- proof metric (includes locked slippage)")
    print(f"message:                 {result.message}")
    if result.resolved_prior_positions:
        print(f"resolved_prior_positions: {', '.join(result.resolved_prior_positions)}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
