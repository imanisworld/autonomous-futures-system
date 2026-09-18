#!/usr/bin/env python3
"""Read-only Backtest -> Tradovate DEMO qualification gate.

This command never deploys, restarts, edits configuration, or submits orders.
It only evaluates an explicit evidence-facts JSON file.

Usage:
  python3 scripts/demo_qualification_gate.py \
      --strategy <name> \
      --evidence-file <facts.json> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.project_check.demo_qualification import build_demo_qualification_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_demo_qualification_report(
        strategy=args.strategy,
        repo_root=ROOT,
        evidence_path=args.evidence_file,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"BACKTEST -> DEMO QUALIFICATION: {args.strategy}")
        print(f"  verdict: {report.get('verdict', 'BLOCKED')}")
        print(f"  demo evidence eligible: {report.get('demo_evidence_eligible', False)}")
        print(f"  base promotion gate: {report.get('base_promotion_gate_pass', False)}")
        print(f"  effective classification: {report.get('effective_classification')}")
        if report.get("evidence_load_error"):
            print(f"  evidence error: {report['evidence_load_error']}")
        blockers = report.get("blockers") or []
        if blockers:
            print("  BLOCKERS:")
            for blocker in blockers:
                print(f"    - {blocker}")
        else:
            print("  all direct-to-demo evidence gates passed")
        print("  runtime/release reconciliation still required before DEMO: yes")
        print("  live trading authorized: NO")

    return 0 if report.get("gate_pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
