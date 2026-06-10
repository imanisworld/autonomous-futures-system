#!/usr/bin/env python3
"""Backfill missing bars in BarHistory from Polygon/Massive futures data.

Closes the loop bar_history.py left open: detect_gap could SURFACE missing
bars but nothing could FILL them (Tradovate only exposes get_quote). This
pulls real exchange bars for the gap window and merges them into the day
files — gaps only, live bars always win on collision, every backfilled bar
tagged "source":"polygon". We still never fabricate bars.

Usage:
    python3 scripts/polygon_backfill.py --instrument MES --days 3
    python3 scripts/polygon_backfill.py --instrument MES --instrument MNQ \
        --days 7 --timeframe 15 --log-dir logs [--dry-run]

Requires POLYGON_API_KEY in the environment / .env. Offline maintenance tool —
never imported by the live pipeline.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context.bar_history import BarHistory  # noqa: E402
from sources.polygon_client import PolygonError, PolygonFuturesClient  # noqa: E402


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass


def backfill_instrument(
    client: PolygonFuturesClient,
    history: BarHistory,
    instrument: str,
    days: int,
    timeframe_minutes: int,
    dry_run: bool = False,
) -> int:
    end = date.today()
    start = end - timedelta(days=days)
    bars = client.fetch_continuous(instrument, start, end, timeframe_minutes)
    if not bars:
        print(f"[backfill] {instrument}: polygon returned no bars for {start}..{end}")
        return 0
    timeframe = f"{timeframe_minutes}m"
    by_day: dict = {}
    for bar in bars:
        rec = bar.to_dict()
        rec["timeframe"] = timeframe
        by_day.setdefault(bar.ts.date(), []).append(rec)
    total = 0
    for d in sorted(by_day):
        if dry_run:
            existing = {b.get("ts") for b in history.recent(instrument, 10_000, for_date=d, lookback_days=1)}
            missing = sum(1 for r in by_day[d] if r["ts"] not in existing)
            print(f"[backfill] {instrument} {d}: would add {missing} bars (dry run)")
            total += missing
            continue
        added = history.merge_backfill(instrument, d, by_day[d])
        if added:
            print(f"[backfill] {instrument} {d}: added {added} bars")
        total += added
    print(f"[backfill] {instrument}: {total} bars {'missing' if dry_run else 'added'} over {start}..{end}")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", action="append", required=True,
                        help="continuous symbol, e.g. MES (repeatable)")
    parser.add_argument("--days", type=int, default=3, help="lookback days (default 3)")
    parser.add_argument("--timeframe", type=int, default=15, help="bar minutes (default 15)")
    parser.add_argument("--log-dir", default="logs", help="BarHistory log dir (default logs)")
    parser.add_argument("--dry-run", action="store_true", help="report gaps without writing")
    args = parser.parse_args(argv)

    _load_env()
    # Pace under the free tier's ~5 req/min so multi-instrument runs don't 429.
    client = PolygonFuturesClient(min_request_interval=13.0)
    if not client.configured:
        print("[backfill] POLYGON_API_KEY not set — nothing to do", file=sys.stderr)
        return 1
    history = BarHistory(log_dir=args.log_dir)

    failures = 0
    for instrument in args.instrument:
        try:
            backfill_instrument(
                client, history, instrument.strip().upper(),
                days=args.days, timeframe_minutes=args.timeframe, dry_run=args.dry_run,
            )
        except PolygonError as exc:
            print(f"[backfill] {instrument}: FAILED — {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
