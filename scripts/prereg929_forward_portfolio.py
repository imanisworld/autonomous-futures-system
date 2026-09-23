#!/usr/bin/env python3
"""Prereg #929 forward evaluator (MNQ five-family ex-Asia shared account).

RESEARCH ONLY. BLIND BY DEFAULT.

  counts  (default) operational counts only: fills / terminal fills by family,
          open positions, busy-skips, max-trades skips, CME observation days
          since 2026-09-23T22:00Z, pipeline health. Never emits net, PF,
          drawdown or win/loss (enforced by ``assert_blind``).
  Both modes read only a corpus built by scripts/prereg929_forward_corpus.py
  from Polygon (prereg §9.1); gap days in its manifests are removed from the
  corpus and fills touching them are VOID_GAP_DAY (§9.3).
  look    the single P&L look. Refuses unless a passing step-0 report is given
          AND (>= 40 terminal portfolio fills and >= 120 CME days, or today is
          on/after 2027-09-30, in which case H1 = INSUFFICIENT_SAMPLE when the
          minimum is not met). Refuses to overwrite an existing output file and
          requires --confirm-single-look.

Usage:
    python3 scripts/prereg929_forward_portfolio.py counts \
        --corpus-5m <out>/5m --corpus-15m <out>/15m [--out counts.json]
    python3 scripts/prereg929_forward_portfolio.py look \
        --corpus-5m <out>/5m --corpus-15m <out>/15m \
        --step0-report step0.json --out look.json --confirm-single-look
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research import prereg929_forward_portfolio as fp  # noqa: E402
from research.prereg929_forward_corpus import FORWARD_CORPUS_START, load_gap_days  # noqa: E402


def _parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise SystemExit("--as-of must carry a timezone")
    return dt.astimezone(timezone.utc)


def _collect(corpus_5m: Path, corpus_15m: Path, as_of: datetime, tmp: Path, corpus_start: date | None = None):
    days5, days15 = fp.corpus_days(corpus_5m), fp.corpus_days(corpus_15m)
    if not days5 or not days15:
        raise SystemExit("empty forward corpus")
    lo = max(days5[0], days15[0], corpus_start.isoformat() if corpus_start else "")
    hi = min(days5[-1], days15[-1])
    roots = fp.single_pair_roots(corpus_5m, corpus_15m, miyagi_candidates="detect")
    views = fp.view_roots(roots, date.fromisoformat(lo), date.fromisoformat(hi), tmp)
    window_end = min(as_of.date(), date.fromisoformat(hi))
    run = fp.run_family_adapters(views, fp.SCORING_START.date(), max(window_end, fp.SCORING_START.date()))
    cme = fp.observed_cme_days(views.root5, as_of)
    return run, cme, (lo, hi)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", choices=("counts", "look"), default="counts")
    p.add_argument("--corpus-5m", type=Path, required=True)
    p.add_argument("--corpus-15m", type=Path, required=True)
    p.add_argument("--as-of", default=None, help="ISO timestamp with tz (default: now)")
    p.add_argument("--corpus-start", type=date.fromisoformat, default=FORWARD_CORPUS_START,
                   help="first corpus day the adapters load (prereg §9.2: 2026-07-25)")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--step0-report", type=Path, default=None)
    p.add_argument("--confirm-single-look", action="store_true")
    args = p.parse_args(argv)
    as_of = _parse_as_of(args.as_of)

    if args.mode == "look":
        if not args.confirm_single_look:
            print("REFUSED: the look happens once; pass --confirm-single-look", file=sys.stderr)
            return 3
        if args.out is None:
            print("REFUSED: look requires --out (the §7 record)", file=sys.stderr)
            return 3
        if args.out.exists():
            print(f"REFUSED: {args.out} exists; the single look already happened", file=sys.stderr)
            return 3
        try:
            fp.validate_step0_report(args.step0_report)
        except fp.LookRefused as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3

    # §9.1/§9.3: only a Polygon-built corpus is scored; its manifests carry the gap days.
    try:
        gap_days = sorted(set(load_gap_days(args.corpus_5m)) | set(load_gap_days(args.corpus_15m)))
    except RuntimeError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    sink_err = io.StringIO()
    with tempfile.TemporaryDirectory(prefix="prereg929-eval-") as tmp, contextlib.redirect_stderr(sink_err):
        run, cme, extent = _collect(args.corpus_5m, args.corpus_15m, as_of, Path(tmp), args.corpus_start)

    if args.mode == "counts":
        report = fp.counts_report(run, cme, as_of=as_of, gap_days=gap_days)
        report["corpus_common_day_range"] = list(extent)
        fp.assert_blind(report)
        text = json.dumps(report, indent=2, sort_keys=True)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n")
        print(text)
        return 0 if run.healthy else 2

    try:
        report = fp.look_report(run, cme, as_of=as_of, step0_report_path=args.step0_report, gap_days=gap_days)
    except fp.LookRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    report["corpus_common_day_range"] = list(extent)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(report["section7"], indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
