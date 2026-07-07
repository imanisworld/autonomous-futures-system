#!/usr/bin/env python3
"""Read-only CLI for the next 30 resolved MNQ live/demo-paper trades."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.proof_30_mnq import (
    DEFAULT_API_BASE,
    DEFAULT_JOURNAL_DIR,
    DEFAULT_LIMIT,
    build_report,
    pair_resolved_trades,
    parse_proof_ts,
    read_journal_entries,
)


def print_human(report: dict) -> None:
    print("RiskSentinel 30-Trade Proof")
    print(f"Journal dir: {report['runtime_sources']['journal_dir']}")
    print(f"Freeze ts: {report['freeze_ts'] or 'not set'}")
    # Proof bar = FILLED W/L only. The resolved superset (below) also contains
    # no-fill CANCELLEDs and reconciler phantom-clears, which do NOT count.
    print(f"MNQ FILLED W/L (proof bar): {report['filled_wl_count']}/{report['target_trades']}")
    print(f"Filled remaining to target: {report['filled_remaining_to_target']}")
    print(f"Filled W/L P&L: ${report['filled_wl_pnl_dollars']:.2f}")
    print(
        f"  raw resolved pairs: {report['total_resolved_pairs']} "
        f"(= filled {report['filled_wl_count']} + breakeven {report['breakeven_count']} "
        f"+ cancelled/no-fill {report['cancelled_nofill_count']} "
        f"+ reconciler-touched {report['reconciler_touched_count']} "
        f"+ other {report['other_outcome_count']})"
    )
    if report['reconciler_touched_count']:
        print(
            f"  ⚠ {report['reconciler_touched_count']} reconciler-touched outcome(s) need "
            "broker-verified classification before they can count (see RUNBOOK)."
        )
    print(f"Journal P&L (all resolved): ${report['journal_pnl_dollars']:.2f}")
    print(f"MNQ risk rejected: {report['mnq_risk_rejected_count']}")
    print(f"Unmatched MNQ outcomes: {report['unmatched_mnq_outcomes']}")
    err = report["errors_log"]
    print(f"errors.log: {'present' if err.get('exists') else 'missing'} ({err.get('lines', 0)} line(s))")
    if report.get("status_today_error"):
        print(f"/status/today: ERROR {report['status_today_error']}")
    elif report.get("status_today"):
        status = report["status_today"]
        print(
            "/status/today: "
            f"trades={status.get('trade_count')} wins={status.get('wins')} "
            f"losses={status.get('losses')} journal={status.get('journal_path')}"
        )
    if report.get("broker_account_error"):
        print(f"/status/broker-account: ERROR {report['broker_account_error']}")
    elif report.get("broker_account"):
        broker = report["broker_account"]
        print(
            "/status/broker-account: "
            f"ok={broker.get('ok')} env={broker.get('env')} "
            f"realized={broker.get('realized_pnl')} open_pnl={broker.get('open_pnl')} "
            f"position={broker.get('position')}"
        )
    if report["journal_read_errors"]:
        print(f"Journal read errors: {len(report['journal_read_errors'])}")
    if report.get("warnings"):
        print()
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    print()
    for idx, trade in enumerate(report["trades"], start=1):
        print(
            f"{idx:02d}. [{trade.get('category', '?')}] {trade['trade_ts']} -> {trade['outcome_ts']} "
            f"{trade['instrument']} {trade['direction']} {trade['strategy']} "
            f"{trade['result']} {trade['exit_reason']} pnl=${float(trade['pnl_dollars'] or 0):.2f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only 30 resolved MNQ trade proof checker.")
    parser.add_argument("--journal-dir", default=str(DEFAULT_JOURNAL_DIR), help="Directory containing journal_*.jsonl and errors.log.")
    parser.add_argument("--freeze-ts", help="Only count trades/outcomes at or after this ISO timestamp.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Resolved MNQ trades required.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Base URL for /status/today and /status/broker-account. Use empty string to skip.")
    parser.add_argument("--status-json", type=Path, help="Read /status/today payload from a local JSON file instead of HTTP.")
    parser.add_argument("--broker-json", type=Path, help="Read /status/broker-account payload from a local JSON file instead of HTTP.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    freeze = parse_proof_ts(args.freeze_ts) if args.freeze_ts else None
    if args.freeze_ts and freeze is None:
        print(f"Invalid --freeze-ts: {args.freeze_ts}", file=sys.stderr)
        return 2
    api_base = args.api_base.strip() or None
    report = build_report(
        journal_dir=Path(args.journal_dir),
        freeze_ts=freeze,
        limit=max(1, args.limit),
        api_base=api_base,
        status_json=args.status_json,
        broker_json=args.broker_json,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)

    if report["journal_read_errors"]:
        return 2
    if report["errors_log"].get("exists") and report["errors_log"].get("lines", 0):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
