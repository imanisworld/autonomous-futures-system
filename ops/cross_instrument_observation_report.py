#!/usr/bin/env python3
"""Authoritative read-only report for cross_instrument_observation_v1.

Lists EVERY configured population (strategy × instrument × variant × epoch)
with raw counts and quality-eligible terminal counts — zero-count populations
included — plus authoritative per-instrument 15m campaign feed proof. Raw rows
remain visible, but only quality-eligible outcomes may satisfy the review gate.
Never grants execution eligibility.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.cross_instrument_evidence_quality import build_quality_report  # noqa: E402
from ops.cross_instrument_feed_health import build_feed_health  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--epoch", default=None, help="Report against this epoch (default: env / unarmed)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args(argv)
    report = build_quality_report(args.log_dir, epoch=args.epoch)
    report["feed_health"] = build_feed_health(args.log_dir, epoch=args.epoch)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    print(
        f"{report['campaign_id']}  enabled={report['enabled']}  epoch={report['evidence_epoch']} "
        f"rows={report['evidence_rows']}  quality={report['quality_version']}"
    )
    health = report["feed_health"]
    print(
        "feed_gate "
        f"proven={health['all_instruments_proven_once']} "
        f"active_healthy={health['active_instruments_healthy']} "
        f"trust={health['ready_to_trust_collection_feed']}"
    )
    for root, row in health["instruments"].items():
        print(
            f"  {root:4s} {row['status']:20s} last15={row['last_successful_15m_bar_ts']} "
            f"transport_ok={row['transport_ok']} bar_recorded={row['bar_recorded']}"
        )
    print(
        f"{'instrument':10s} {'strategy':38s} {'mode':19s} {'raw':>5s} {'clean':>5s} "
        f"{'blocked':>7s} {'days':>4s}  status"
    )
    for p in report["populations"]:
        print(
            f"{p['instrument']:10s} {p['strategy']:38s} {p['collection_mode']:19s} "
            f"{p['terminal_outcomes']:5d} {p['quality_eligible_terminal_outcomes']:5d} "
            f"{p['quality_blocked_terminal_outcomes']:7d} {p['quality_distinct_terminal_days']:4d}  {p['status']}"
        )
        if p["quality_population_blockers"] or p["quality_issue_counts"]:
            print(
                f"  quality blockers={p['quality_population_blockers']} issues={p['quality_issue_counts']}"
            )
    if report["unconfigured_rows"]:
        print("UNCONFIGURED ROWS (visible, never review-eligible):")
        for row in report["unconfigured_rows"]:
            print("  ", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
