#!/usr/bin/env python3
"""Read-only CLI for Daily Reconciliation + Trade Chain Integrity.

Usage:
    python3 scripts/daily_reconciliation.py [--date YYYY-MM-DD] [--json]

Folds PR/branch/worktree hygiene, evidence preservation, deployed-state
tracking, strategy-inventory drift, and per-trade signal->decision->risk->
order->fill->protection->exit->outcome chain integrity into one pass. Never
cancels an order, flattens a position, edits config/docs, or creates/deletes
a git tag or branch -- report and fail closed only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.daily_reconciliation import build_daily_reconciliation_report, format_trade_chain_line


def _print_human(report: dict) -> None:
    print(f"Daily Reconciliation: {report['overall_verdict']}")
    print(f"Generated at: {report['generated_at']}")
    if report["blockers"]:
        print()
        print("BLOCKERS:")
        for blocker in report["blockers"]:
            print(f"  - {blocker}")
    print()
    print("-- GitHub / repo reconciliation --")
    print(json.dumps(report["github_repo_reconciliation"], indent=2, sort_keys=True, default=str))
    print()
    print("-- Deployed state --")
    print(f"status={report['deployed_state']['status']}: {report['deployed_state']['summary']}")
    print()
    print("-- Strategy source of truth --")
    drift_findings = report["strategy_source_of_truth"]["findings"]
    if drift_findings:
        for finding in drift_findings:
            print(f"  [{finding['severity']}] {finding['strategy']}: {finding['issue']}")
    else:
        print("  no drift found")
    print()
    print(format_trade_chain_line(report["trade_chain_integrity"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--risk-rules", default="risk_rules.yaml")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-prs", action="store_true", help="Skip the best-effort gh pr list call")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_daily_reconciliation_report(
        repo_root=args.repo_root,
        log_dir=args.log_dir,
        risk_rules_path=args.risk_rules,
        target_date=args.date,
        check_prs=not args.no_prs,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)

    return 0 if report["overall_verdict"] == "CLEAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
