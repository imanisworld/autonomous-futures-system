#!/usr/bin/env python3
"""P2-P — firing / bracket parity: live journal shadow candidates vs a replay run.

Structural-level prereg v1.3 / P2 spec §4 (frozen gates: firing Jaccard ≥ 0.90 per family
over bars evaluated on BOTH sides; entry/stop/target each within one tick on ≥ 98 % of
co-fired rows). Reads candidates only — every ``outcome`` block is split off at parse time
and discarded. Live rows: 15m decision rows of the frozen snapshot, ``context.timestamp`` as
the bar time, window start → 2026-09-14T22:00Z exclusive (roll cut). Replay rows: the
parity-corpus run's ``journal_*.jsonl`` (``bar_ts``).

Per family it reports: live firing count, replay firing count, overlap (Jaccard), bracket
parity, the declared input-source mismatch reason, and the final classification
(BOTH / BOTH — input-divergent / LIVE_ONLY / NOT_TESTABLE / DEAD, plus the fail-closed
MANIFEST_ERROR / BRACKET_CONFLICT states).

Usage:
    python3 scripts/structural_level_p2_parity.py --live-logs-root <snapshot> \
        --replay-log-dir <dir> --corpus-root data/replay_polygon_parity_2026_07_16_09_14 \
        --start 2026-07-16 --out <report.json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.structural_level_p2 import (  # noqa: E402
    INSTRUMENTS,
    ROLL_CUT,
    compute_parity,
    iter_live_rows,
    iter_replay_rows,
    load_corpus_bars,
    parse_dt,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-logs-root", required=True)
    ap.add_argument("--replay-log-dir", required=True)
    ap.add_argument("--corpus-root", default=None, help="parity corpus root (<INST>/ day files) for bar census")
    ap.add_argument("--start", default="2026-07-16", help="first live journal day (YYYY-MM-DD)")
    ap.add_argument("--end-ts", default=ROLL_CUT.isoformat(), help="exclusive end (roll cut)")
    ap.add_argument("--instruments", default=",".join(INSTRUMENTS))
    ap.add_argument("--out", required=True, help="report JSON path")
    args = ap.parse_args(argv)

    insts = [x.strip().upper() for x in args.instruments.split(",") if x.strip()]
    end_ts = parse_dt(args.end_ts)
    corpus = None
    if args.corpus_root:
        corpus = {i: load_corpus_bars(os.path.join(args.corpus_root, i)) for i in insts}
    live = list(iter_live_rows(args.live_logs_root, start_date=args.start, end_ts_exclusive=end_ts,
                               instruments=insts))
    replay = list(iter_replay_rows(args.replay_log_dir, instruments=insts, end_ts_exclusive=end_ts))
    report = compute_parity(live, replay, corpus=corpus)
    report["inputs"] = {
        "live_logs_root": os.path.abspath(args.live_logs_root),
        "replay_log_dir": os.path.abspath(args.replay_log_dir),
        "corpus_root": os.path.abspath(args.corpus_root) if args.corpus_root else None,
        "start": args.start, "end_ts_exclusive": end_ts.isoformat() if end_ts else None,
        "live_rows": len(live), "replay_rows": len(replay),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
        fh.write("\n")

    print(f"[p2p] live rows={len(live)} replay rows={len(replay)} stale_orb_excluded={report['live_stale_orb_rows_excluded']}")
    for inst, c in report["bar_census"].items():
        print(f"[p2p] {inst}: {c}")
    print(f"[p2p] {'family':40s} {'live':>6s} {'replay':>6s} {'jacc':>6s} {'brkt':>6s}  class")
    for fam, r in report["families"].items():
        b = r["on_both_evaluated_bars"]; k = r["bracket_on_cofired"]
        j = "-" if b["firing_jaccard"] is None else f"{b['firing_jaccard']:.3f}"
        br = "-" if k["all_three_within_one_tick_rate"] is None else f"{k['all_three_within_one_tick_rate']:.3f}"
        print(f"[p2p] {fam:40s} {b['live_firings']:6d} {b['replay_firings']:6d} {j:>6s} {br:>6s}  {r['classification']}")
    for g in report["gate_failures"]:
        print(f"[p2p] GATE FAILURE: {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
