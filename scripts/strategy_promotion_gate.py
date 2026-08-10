#!/usr/bin/env python3
"""Read-only CLI for the Strategy Promotion Proof Gate.

Usage:
    python3 scripts/strategy_promotion_gate.py --strategy orb_breakout [--json]
    python3 scripts/strategy_promotion_gate.py --strategy orb_breakout --from-date 2026-06-01 --to-date 2026-08-01

Reports whether a strategy's journal evidence went through the real
executable path (candidate -> DecisionEngine -> RiskEngine -> PaperBroker ->
resolved outcome) rather than just a standalone backtest. Never edits
risk_rules.yaml, never enables a strategy, never merges or deploys anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.strategy_promotion_gate import build_promotion_report


def _print_human(report: dict) -> None:
    print(f"Strategy Promotion Proof Gate: {report['strategy']}")
    print(f"Journal window: {report['window']['from_date'] or '(all)'} .. {report['window']['to_date'] or '(all)'}")
    print()
    attrition = report["gate_attrition"]
    print(f"Candidates observed: {attrition['candidate_count']}")
    print(f"By decision: {attrition['by_decision']}")
    print(f"Failed-gate counts (non-TRADE rows): {attrition['failed_gate_counts']}")
    print()
    identity = report["execution"]["accounting_identity"]
    print(
        f"Accounting: attempts={identity['attempts']} fills={identity['fills']} "
        f"cancellations={identity['cancellations']} needs_manual={identity['needs_manual_classification']} "
        f"legitimately_open={identity['legitimately_open']} ok={identity['ok']}"
    )
    perf = report["performance"]
    print(
        f"Performance: n={perf['filled_count']} net=${perf['net_pnl_dollars']} "
        f"pf={perf['profit_factor']} win_rate={perf['win_rate']} expectancy=${perf['expectancy_dollars']}"
    )
    print()
    classification = report["classification"]
    print(f"CLASSIFICATION: {classification['verdict']}")
    for reason in classification["reasons"]:
        print(f"  - {reason}")
    print()
    print(f"Identity/parity: {report['identity_parity']['status']}")
    print(report["no_promotion_side_effects"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, help="Strategy key as it appears in setup.strategy / risk_rules.yaml")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--risk-rules", default="risk_rules.yaml")
    parser.add_argument("--from-date", help="Include journal rows on/after YYYY-MM-DD")
    parser.add_argument("--to-date", help="Include journal rows on/before YYYY-MM-DD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_promotion_report(
        args.strategy,
        repo_root=args.repo_root,
        log_dir=args.log_dir,
        risk_rules_path=args.risk_rules,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)

    verdict = report["classification"]["verdict"]
    if verdict in ("BROKEN", "UNSAFE"):
        return 1
    if report["journal_read_errors"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
