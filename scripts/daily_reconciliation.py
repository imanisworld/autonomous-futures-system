#!/usr/bin/env python3
"""Read-only CLI: daily reconciliation + trade-chain integrity.

  python3 scripts/daily_reconciliation.py [--since ISO8601_OR_DATE]

Entirely READ ONLY: never cancels orders, flattens positions, modifies
broker orders, repairs the journal, synthesizes an OUTCOME, rewrites state,
retries execution, or submits an order. On any discrepancy it reports or
fails closed — nothing more.

Produces, in order: (A) GitHub/repo reconciliation, (B) evidence
preservation, (C) deployed state, (D) strategy source-of-truth drift,
(E) trade-chain integrity. See ops/daily_reconciliation.py for the
underlying (tested) logic — this file is only argument parsing and
printing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.daily_reconciliation import build_daily_reconciliation_report, format_daily_reconciliation_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--since", default=None,
        help="ISO8601 timestamp or YYYY-MM-DD date. Overrides the on-disk checkpoint for this run only.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Repo root (default: discovered from cwd).")
    parser.add_argument("--journal-dir", type=Path, default=None, help="Journal directory (default: <repo_root>/logs).")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    args = parser.parse_args(argv)

    report = build_daily_reconciliation_report(
        repo_root=args.repo_root,
        journal_dir=args.journal_dir,
        since=args.since,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_daily_reconciliation_report(report))

    if not report.get("ok"):
        return 2
    trade_chain_clean = report["section_e_trade_chain_integrity"]["clean"]
    evidence_blockers = report["section_b_evidence_preservation"].get("blockers") or []
    return 0 if trade_chain_clean and not evidence_blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
