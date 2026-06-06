#!/usr/bin/env python3
"""Run a compact robustness grid for the standalone range-fade experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.range_fade_backtest import run_file, summarize
from strategy.range_fade import RangeFadeConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--session", default="new_york")
    args = parser.parse_args()

    files = sorted(Path(args.candles).glob("*.jsonl"))
    rows = []
    for confirmation_bars in (4, 6, 8):
        for entry_zone in (0.10, 0.15, 0.20):
            config = RangeFadeConfig(
                confirmation_bars=confirmation_bars,
                entry_zone_percent=entry_zone,
            )
            trades = [
                trade
                for path in files
                for trade in run_file(path, config, max_trades=1, max_losses=1, session=args.session)
            ]
            rows.append(
                {
                    "confirmation_bars": confirmation_bars,
                    "entry_zone": entry_zone,
                    **summarize(trades, len(files)),
                }
            )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
