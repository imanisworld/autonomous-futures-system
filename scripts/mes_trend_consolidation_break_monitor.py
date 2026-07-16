#!/usr/bin/env python3
"""Print read-only metrics for the MES trend-consolidation-break evidence lane."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.mes_trend_consolidation_break_monitor import summarize_lane  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()
    print(json.dumps(summarize_lane(args.log_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
