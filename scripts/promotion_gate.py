#!/usr/bin/env python3
"""Read-only CLI for the Strategy Promotion Proof Gate.

Usage:
    python3 scripts/promotion_gate.py --strategy strat_22_reversal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.promotion_gate import build_promotion_report, format_promotion_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, help="Strategy identifier, e.g. strat_22_reversal.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--journal-dir", default=None)
    parser.add_argument("--research-evidence", default=None, help="Optional path to a standalone research evidence JSON.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = build_promotion_report(
        args.strategy,
        repo_root=args.repo_root,
        log_dir=args.log_dir,
        journal_dir=args.journal_dir,
        research_evidence_path=args.research_evidence,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_promotion_report(report))
    return 0 if report["classification"]["verdict"] in ("VALIDATED", "PROMISING BUT UNPROVEN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
