#!/usr/bin/env python3
"""MGC 4H wide-geometry forward observation (docs/prereg-mgc-4h-wide-forward-2026-09-23.md).

OBSERVATION / RESEARCH ONLY. Scored offline from a fresh Polygon fetch (local key).

  counts  (default) blind operational counts: trades, trades by setup, void trades,
          observation days, gap days, sample status. Never prints P&L.
  step0   parity gate vs the live observation lane (copy of the box's
          logs/cross_instrument_observation_v1.jsonl): writes the step-0 report.
  look    the single look. Refuses without --confirm-single-look, a passing step-0
          report, the minimum sample (or the deadline), or if --out exists.

Usage:
    python3 scripts/mgc_4h_wide_forward.py counts [--out counts.json]
    python3 scripts/mgc_4h_wide_forward.py step0 --live-obs obs.jsonl --out step0.json
    python3 scripts/mgc_4h_wide_forward.py look --step0-report step0.json --out look.json --confirm-single-look
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

from research import mgc_4h_wide_forward as mf  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", choices=("counts", "step0", "look"), default="counts")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--live-obs", type=Path, default=None)
    p.add_argument("--step0-report", type=Path, default=None)
    p.add_argument("--confirm-single-look", action="store_true")
    a = p.parse_args(argv)
    now = datetime.now(timezone.utc)

    if a.mode == "step0":
        if a.live_obs is None or a.out is None:
            print("REFUSED: step0 needs --live-obs and --out", file=sys.stderr)
            return 3
        rep = mf.step0_parity(a.live_obs)
        a.out.write_text(json.dumps(rep, indent=2) + "\n")
        print(json.dumps({k: rep[k] for k in ("live_candidates", "reproduced_rate", "geometry_rate", "verdict")}))
        return 0 if rep["verdict"] == "PASS" else 2

    if a.mode == "look":
        if not a.confirm_single_look:
            print("REFUSED: the look happens once; pass --confirm-single-look", file=sys.stderr)
            return 3
        if a.out is None or a.out.exists():
            print("REFUSED: look needs a new --out file (an existing one means the look already happened)", file=sys.stderr)
            return 3
        try:
            mf.validate_step0(a.step0_report)
        except mf.LookRefused as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3

    run = mf.build_run()
    if a.mode == "counts":
        rep = mf.counts_report(run, as_of=now)
        text = json.dumps(rep, indent=2, sort_keys=True)
        if a.out:
            a.out.write_text(text + "\n")
        print(text)
        return 0 if run.healthy else 2
    try:
        rep = mf.look_report(run, as_of=now, step0_path=a.step0_report)
    except mf.LookRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    a.out.write_text(json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({"verdict": rep["verdict"], "criteria": rep["criteria"]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
