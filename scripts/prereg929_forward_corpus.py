#!/usr/bin/env python3
"""Build the prereg #929 forward corpus from box-collected MNQ bars.

Research only. Uses the existing enrichment path
(``scripts.polygon_to_replay.derive_candles``) via
``research/prereg929_forward_corpus.py``. Writes OUTSIDE the repo.

Usage:
    python3 scripts/prereg929_forward_corpus.py \
        --bars-5m  <box copy>/tf5m  --bars-15m <box copy>/tf15m \
        --out /private/tmp/.../prereg929_corpus
Produces <out>/5m/MNQ/MNQ_<day>.jsonl, <out>/15m/MNQ/MNQ_<day>.jsonl and a
MANIFEST_<n>m.json per timeframe. The output dirs must not already hold files.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.prereg929_forward_corpus import build  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars-5m", type=Path, required=True, help="dir of bars_MNQ_*.jsonl (5m, box logs/tf5m)")
    p.add_argument("--bars-15m", type=Path, required=True, help="dir of bars_MNQ_*.jsonl (15m, box logs/)")
    p.add_argument("--out", type=Path, required=True, help="output root OUTSIDE the repo")
    p.add_argument("--start", type=date.fromisoformat, default=None, help="first UTC day to write (optional)")
    p.add_argument("--end", type=date.fromisoformat, default=None, help="last UTC day to write (optional)")
    args = p.parse_args(argv)

    summary = {}
    for tf, src in ((5, args.bars_5m), (15, args.bars_15m)):
        m = build(src, tf, args.out / f"{tf}m", start=args.start, end=args.end)
        summary[f"{tf}m"] = {
            "raw_bars": m["raw_bars"],
            "derived_candles": m["derived_candles"],
            "dropped_by_derive_candles": m["dropped_by_derive_candles"],
            "exact_duplicates_dropped": m["exact_duplicates_dropped"],
            "day_files": len(m["output_files"]),
            "first_day": m["output_files"][0]["file"] if m["output_files"] else None,
            "last_day": m["output_files"][-1]["file"] if m["output_files"] else None,
            "out": str(args.out / f"{tf}m"),
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
