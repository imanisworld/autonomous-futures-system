#!/usr/bin/env python3
"""Read-only CLI for reconciler/phantom/naked/auto-flatten outcome audit."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR
from ops.reconciler_outcome_audit import build_audit_report, report_to_json


DEFAULT_OVERRIDES_DOC = ROOT / "docs" / "proof-operator-overrides.md"


def _money(value: object) -> str:
    try:
        return f"${float(value or 0.0):.2f}"
    except (TypeError, ValueError):
        return "$?"


def _print_item(item: dict, idx: int) -> None:
    trade = item.get("trade") or {}
    marker = ",".join(item.get("markers") or [])
    print(
        f"{idx:02d}. {item.get('audit_id')} "
        f"{item.get('instrument')} {trade.get('direction') or '?'} {trade.get('strategy') or '?'} "
        f"{item.get('result')} {_money(item.get('pnl_dollars'))} markers={marker}"
    )
    print(f"    trade:   {item.get('trade_ts') or '?'} {item.get('trade_journal_path') or '?'}:{item.get('trade_journal_line') or '?'}")
    print(f"    outcome: {item.get('outcome_ts') or '?'} {item.get('outcome_journal_path') or '?'}:{item.get('outcome_journal_line') or '?'}")
    print(f"    reason:  {item.get('exit_reason')}")
    print(f"    status:  {item.get('classification_reason')}")
    print(f"    verify:  {'yes' if item.get('needs_broker_verification') else 'no'}")
    matches = item.get("operator_overrides") or []
    for match in matches:
        print(f"    override: {match.get('heading')} ({match.get('ruling') or 'no ruling text parsed'})")


def print_human(report: dict, *, only: str = "all") -> None:
    summary = report["summary"]
    print("RiskSentinel Reconciler Outcome Audit")
    print(f"Journal dir: {report['journal_dir']}")
    print(f"Overrides doc: {report['overrides_doc'] or 'not used'}")
    print(
        f"Touched outcomes: {summary['total_touched']} "
        f"(classified {summary['classified']}, unaudited {summary['unaudited']})"
    )
    if summary.get("journal_read_errors"):
        print(f"Journal read errors: {summary['journal_read_errors']}")
    print(f"By instrument: {summary['by_instrument']}")
    print(f"By marker: {summary['by_marker']}")
    print()

    if only in ("all", "classified"):
        print("Already Classified")
        if report["classified"]:
            for idx, item in enumerate(report["classified"], start=1):
                _print_item(item, idx)
        else:
            print("  none")
        print()

    if only in ("all", "unaudited"):
        print("Unaudited / Broker Verification Follow-Up")
        if report["unaudited"]:
            for idx, item in enumerate(report["unaudited"], start=1):
                _print_item(item, idx)
        else:
            print("  none")
        print()
    print("Operator override note: if broker evidence contradicts an unaudited journal outcome, record the public-safe ruling in docs/proof-operator-overrides.md; do not edit journal JSONL.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only audit for reconciler-touched journal outcomes.")
    parser.add_argument("--journal-dir", default=str(DEFAULT_JOURNAL_DIR), help="Directory containing journal_*.jsonl files.")
    parser.add_argument("--overrides-doc", type=Path, default=DEFAULT_OVERRIDES_DOC, help="Operator override markdown to use for classified matches.")
    parser.add_argument("--from-date", help="Include rows on/after YYYY-MM-DD.")
    parser.add_argument("--to-date", help="Include rows on/before YYYY-MM-DD.")
    parser.add_argument("--only", choices=("all", "classified", "unaudited"), default="all", help="Limit human output to one section.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = build_audit_report(
        journal_dir=Path(args.journal_dir),
        overrides_doc=args.overrides_doc,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    if args.json:
        print(report_to_json(report))
    else:
        print_human(report, only=args.only)
    return 2 if report.get("journal_read_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
