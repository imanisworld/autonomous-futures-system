#!/usr/bin/env python3
"""Read-only CLI: per-lane evidence inventory across the whole system.

Answers "how much evidence do we have, for what, and is it even tradeable"
in one pass. See ops/evidence_report.py for the underlying (tested) logic —
this file is only argument parsing and printing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.evidence_report import build_evidence_report

DEFAULT_JOURNAL_DIR = ROOT / "logs"


def print_human(report: dict) -> None:
    print("AFS Evidence Inventory")
    print(f"Journal dir: {report['journal_dir']} ({report['journal_files_scanned']} file(s))")
    print(f"repo_head: {report['repo_head'] or 'unknown'}  box_release: {report['box_release'] or 'not provided'}")
    print()

    print("── Real live/demo trades (decisions -> fills) ──")
    for row in report["real_trades_by_strategy"]:
        print(
            f"[{row['class']:>20}] {row['strategy']:<24} {row['instrument'] or '':<4} "
            f"decisions={row['decisions']:>3} fills={row['fills']:>3} cancelled={row['cancelled']:>3} "
            f"W={row['wins']:>2} L={row['losses']:>2} no_fill%={row['no_fill_rate_pct']} "
            f"net=${row['net_pnl_dollars']:>8.2f} exp/fill=${row['expectancy_per_fill_dollars']} "
            f"exp/decision=${row['expectancy_per_decision_dollars']} {row['date_range']}"
        )
        if row.get("no_fill_reasons"):
            print(f"    no_fill_reasons: {row['no_fill_reasons']}")
        if row.get("note"):
            print(f"    note: {row['note']}")
    print()

    print("── Shadow evidence (SHADOW_OUTCOME, by lane/strategy) ──")
    for row in report["shadow_by_lane_strategy"]:
        print(
            f"[{row['class']:>20}] {row['lane']:<14} {row['strategy']:<32} "
            f"resolved={row['resolved']:>3} W={row['wins']:>3} L={row['losses']:>3} "
            f"no_fill={row['no_fill']:>3} open={row['open']:>3} WR%={row['win_rate_pct']} "
            f"net_ticks={row['net_pnl_ticks']:>8} {row['date_range']}"
        )
        if row.get("note"):
            print(f"    note: {row['note']}")
    print()

    print("── MES orb_reclaim (lead replay candidate) ──")
    mes = report["mes_orb_reclaim"]
    print(
        f"eligible_since={mes['eligible_since']} decisions={mes['decisions']} "
        f"fills={mes['fills']} cancelled={mes['cancelled']} W={mes['wins']} L={mes['losses']}"
    )
    print(f"status: {mes['live_sample_status']}")
    if mes.get("no_fill_reasons"):
        print(f"no_fill_reasons: {mes['no_fill_reasons']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only per-lane evidence inventory.")
    parser.add_argument("--journal-dir", default=str(DEFAULT_JOURNAL_DIR), help="Directory containing journal_*.jsonl.")
    parser.add_argument("--box-release", default=None, help="Deployed box release SHA, if known (not auto-fetched).")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = build_evidence_report(Path(args.journal_dir), box_release=args.box_release)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
