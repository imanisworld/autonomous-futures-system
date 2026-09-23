#!/usr/bin/env python3
"""Build the prereg #929 forward corpus from Polygon (prereg §9.1–§9.3).

Research only. Fresh fetch every run through the unchanged polygon_to_replay
path (PolygonFuturesClient.fetch_continuous, roll 8 days, derive_candles).
Keeps only CME observation days that closed >= 24h before the fetch, detects
gap days mechanically and removes them from BOTH timeframes (never filled).
Box bars are no longer a forward source. Writes OUTSIDE the repo. Needs the
local POLYGON_API_KEY (.env on the Mac; the box has none).

Usage:
    python3 scripts/prereg929_forward_corpus.py --out /private/tmp/.../prereg929_corpus
Produces <out>/5m/MNQ/MNQ_<day>.jsonl, <out>/15m/MNQ/MNQ_<day>.jsonl,
<out>/<tf>m/MANIFEST_<tf>m.json and <out>/FORWARD_MANIFEST.json (gap days,
raw-bar SHA-256). The output dirs must not already hold files.
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

from research.prereg929_forward_corpus import (  # noqa: E402
    FORWARD_CORPUS_START,
    POLYGON_PREROLL_DAYS,
    build_polygon_corpus,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True, help="output root OUTSIDE the repo")
    p.add_argument("--corpus-start", type=date.fromisoformat, default=FORWARD_CORPUS_START,
                   help="first corpus day (prereg §9.2: 2026-07-25)")
    args = p.parse_args(argv)

    res = build_polygon_corpus(args.out, args.corpus_start, preroll_days=POLYGON_PREROLL_DAYS)
    fw = res["forward"]
    summary = {
        "fetched_at": fw["fetched_at"],
        "settled_cutoff": fw["settled_cutoff"],
        "corpus_start": fw["corpus_start"],
        "last_obs_day_checked": fw["last_obs_day_checked"],
        "gap_days": [g["obs_day"] for g in fw["gap_days"]],
        "raw_sha256": fw["raw_sha256"],
    }
    for tf, m in res["timeframes"].items():
        summary[tf] = {
            "raw_bars_after_settlement": m["raw_bars_after_settlement"],
            "candles_written": m["derived_candles_in_range"] - m["candles_removed_as_gap_days"],
            "candles_removed_as_gap_days": m["candles_removed_as_gap_days"],
            "day_files": len(m["output_files"]),
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
