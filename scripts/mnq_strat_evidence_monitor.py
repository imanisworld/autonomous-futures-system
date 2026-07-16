#!/usr/bin/env python3
"""Print read-only metrics for one or all MNQ Strat evidence lanes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from execution.mnq_strat_evidence import LANES  # noqa: E402
from ops.mnq_strat_evidence_monitor import summarize_all, summarize_lane  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--lane", choices=tuple(LANES))
    args = parser.parse_args()
    payload = (
        summarize_lane(args.log_dir, args.lane)
        if args.lane else summarize_all(args.log_dir)
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
