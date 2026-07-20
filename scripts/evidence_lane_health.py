#!/usr/bin/env python3
"""Print one read-only MES + MNQ evidence-lane health snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.evidence_lane_health import build_snapshot, format_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--date", type=date.fromisoformat, help="UTC date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    snapshot = build_snapshot(args.log_dir, day=args.date)
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        print(format_snapshot(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
