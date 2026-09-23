#!/usr/bin/env python3
"""Cross-market paper admission status (prereg 2026-09-23). Research only.

Reads the observation lane's OUTCOME rows and prints each (market, setup)
pair's status: SCREENING, CONFIRMING, CONFIRMED, REJECTED, NOT_ADMITTED or
INSUFFICIENT_CONFIRMATION. Standard library only; safe to run on the box:

    python3 scripts/cross_market_paper_admission.py \
        --log /root/autonomous-futures-system/logs/cross_instrument_observation_v1.jsonl \
        [--as-of 2026-10-02T21:00:00Z] [--out status.json]

A CONFIRMED pair is eligible for a proposal only (prereg §7); nothing here
changes what the bot trades.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research import cross_market_paper_admission as cma  # noqa: E402


def _as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise SystemExit("--as-of must carry a timezone")
    return dt.astimezone(timezone.utc)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", type=Path, required=True, help="cross_instrument_observation_v1.jsonl")
    p.add_argument("--as-of", default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    report = cma.evaluate(cma.read_jsonl(args.log), _as_of(args.as_of))
    print(f"as of {report['as_of']}  checkpoints passed: {len(report['checkpoints_passed'])}  "
          f"status: {report['status_counts']}")
    for name, pair in report["pairs"].items():
        a = pair["stage_a_latest"]
        line = (f"{name:<45} {pair['status']:<26} trades {a['trades']:>3}  days {a['trade_dates']:>3}  "
                f"net ${a['net']:>9,.2f}  PF {a['pf']}")
        if pair["stage_a_pass_checkpoint"]:
            b = pair["stage_b"] or {}
            line += f"  | passed A {pair['stage_a_pass_checkpoint']}, fresh {b.get('trades', b.get('trades_so_far'))}/40"
        print(line)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
