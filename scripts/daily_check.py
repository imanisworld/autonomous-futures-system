#!/usr/bin/env python3
"""Read-only CLI for Daily Reconciliation + Trade Chain Integrity.

Usage:
    python3 scripts/daily_check.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.daily_check import build_daily_report, format_daily_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--journal-dir", default=None)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = build_daily_report(
        repo_root=args.repo_root,
        log_dir=args.log_dir,
        journal_dir=args.journal_dir,
        base_branch=args.base_branch,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_daily_report(report))
    return 0 if report["trade_chain"]["ok"] and not report["evidence_preservation"]["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
