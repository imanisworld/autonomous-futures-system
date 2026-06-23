#!/usr/bin/env python3
"""MFE/MAE excursion study — does the strategy have a tail worth a runner?

For each trade (entry ts, direction, entry/stop/target) this replays the
*1-minute* price path forward and measures, in R units (R = |entry - stop|):

  - MFE  — how far price ran in our favour before the trade resolved
  - MAE  — how far it ran against us (heat taken)
  - exit — target / stop / timeout at 1-min resolution
  - continuation — for target-hits, how much FURTHER price ran past target in
    the next 15 / 30 / 60 min (the "did we leave money on the table" question)
  - giveback — trades that reached >=1R / >=2R favourable and then stopped out

METHODOLOGY NOTE: the trade list and the excursion bars MUST come from the same
price source or the stops/targets won't line up. This is sound for REPLAY trades
(generated from Polygon bars) measured against Polygon 1-min bars. It is NOT
reliable for live Tradovate-demo fills, whose prices don't match Polygon
continuous bars. 1-min bars still can't see true intrabar order, so a bar that
spans both stop and target is booked stop-first (conservative); those are counted.

Usage:
    python3 scripts/mfe_study.py --trades trades.json [--max-hold-min 480]
                                 [--cache-dir data/mfe_cache]

trades.json: a JSON list of {instrument, entry_ts, direction, entry, stop, target}.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources.polygon_client import PolygonBar, PolygonFuturesClient

TICK_SIZE = {"MNQ": 0.25, "MES": 0.25, "ES": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01}


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_bars(client, symbol, start_date, end_date, cache_dir) -> list[PolygonBar]:
    """Fetch (and disk-cache) 1-min continuous bars for symbol over the span."""
    cache = Path(cache_dir) / f"{symbol}_1m_{start_date}_{end_date}.jsonl"
    if cache.exists():
        rows = [json.loads(l) for l in cache.read_text().splitlines() if l.strip()]
        return [PolygonBar(ts=_parse_ts(r["ts"]), open=r["open"], high=r["high"],
                           low=r["low"], close=r["close"], volume=r["volume"],
                           ticker=r["ticker"]) for r in rows]
    bars = client.fetch_continuous(symbol, start_date, end_date, timeframe_minutes=1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(json.dumps(b.to_dict()) for b in bars))
    return bars


def _analyze_trade(trade: dict, bars: list[PolygonBar], max_hold_min: int) -> dict | None:
    instrument = trade["instrument"]
    direction = (trade["direction"] or "").upper()
    entry, stop, target = float(trade["entry"]), float(trade["stop"]), float(trade["target"])
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    entry_ts = _parse_ts(trade["entry_ts"])
    horizon = entry_ts + timedelta(minutes=max_hold_min)
    is_long = direction == "LONG"

    window = [b for b in bars if entry_ts <= b.ts <= horizon]
    if len(window) < 2:
        return None  # no usable 1-min coverage for this trade

    fav_max = 0.0       # favourable excursion (>=0)
    adv_max = 0.0       # adverse excursion (>=0)
    outcome = "TIMEOUT"
    exit_idx = len(window) - 1
    ambiguous = False
    for i, b in enumerate(window):
        fav = (b.high - entry) if is_long else (entry - b.low)
        adv = (entry - b.low) if is_long else (b.high - entry)
        fav_max = max(fav_max, fav)
        adv_max = max(adv_max, adv)
        hit_target = (b.high >= target) if is_long else (b.low <= target)
        hit_stop = (b.low <= stop) if is_long else (b.high >= stop)
        if hit_target and hit_stop:
            ambiguous = True
            outcome, exit_idx = "STOP", i   # conservative: stop-first
            break
        if hit_stop:
            outcome, exit_idx = "STOP", i
            break
        if hit_target:
            outcome, exit_idx = "TARGET", i
            break

    exit_bar = window[exit_idx]
    minutes_held = (exit_bar.ts - entry_ts).total_seconds() / 60.0

    # Continuation past target: favourable extreme reached within +N min of the
    # target-hit bar, expressed as R BEYOND the target.
    target_R = abs(target - entry) / risk
    cont = {15: None, 30: None, 60: None}
    if outcome == "TARGET":
        t_hit = exit_bar.ts
        for mins in cont:
            sub = [b for b in bars if t_hit <= b.ts <= t_hit + timedelta(minutes=mins)]
            if sub:
                ext = max((b.high for b in sub)) if is_long else min((b.low for b in sub))
                beyond = (ext - target) if is_long else (target - ext)
                cont[mins] = max(0.0, beyond) / risk

    return {
        "instrument": instrument,
        "outcome": outcome,
        "mfe_R": fav_max / risk,
        "mae_R": adv_max / risk,
        "target_R": target_R,
        "minutes_held": minutes_held,
        "ambiguous": ambiguous,
        "cont_R": cont,
        "reached_1R": fav_max / risk >= 1.0,
        "reached_2R": fav_max / risk >= 2.0,
    }


def _pct(n, d):
    return (100.0 * n / d) if d else 0.0


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else float("nan")


def _report(results: list[dict]) -> None:
    by_inst = defaultdict(list)
    for r in results:
        by_inst[r["instrument"]].append(r)

    for inst, rs in sorted(by_inst.items()):
        n = len(rs)
        wins = [r for r in rs if r["outcome"] == "TARGET"]
        stops = [r for r in rs if r["outcome"] == "STOP"]
        timeouts = [r for r in rs if r["outcome"] == "TIMEOUT"]
        ambiguous = sum(1 for r in rs if r["ambiguous"])
        giveback = [r for r in rs if r["reached_1R"] and r["outcome"] == "STOP"]
        giveback2 = [r for r in rs if r["reached_2R"] and r["outcome"] == "STOP"]

        print(f"\n══════════ {inst}  (n={n}) ══════════")
        print(f"  exits:   target {len(wins)} ({_pct(len(wins),n):.0f}%) · "
              f"stop {len(stops)} ({_pct(len(stops),n):.0f}%) · "
              f"timeout {len(timeouts)} ({_pct(len(timeouts),n):.0f}%)")
        if ambiguous:
            print(f"  ⚠ {ambiguous} trade(s) hit stop & target in the same 1-min bar "
                  f"→ booked stop-first (conservative); real result may be better")
        print(f"  MFE (R):  median {_med([r['mfe_R'] for r in rs]):.2f} · "
              f"mean {statistics.mean([r['mfe_R'] for r in rs]):.2f} · "
              f"max {max(r['mfe_R'] for r in rs):.2f}")
        print(f"  MAE (R):  median {_med([r['mae_R'] for r in rs]):.2f} · "
              f"mean {statistics.mean([r['mae_R'] for r in rs]):.2f}  (heat taken)")
        print(f"  target sits at {_med([r['target_R'] for r in rs]):.2f}R (median)")
        # The runner question: of trades that HIT target, how much further did price run?
        if wins:
            for mins in (15, 30, 60):
                vals = [w["cont_R"][mins] for w in wins if w["cont_R"][mins] is not None]
                if vals:
                    ran = sum(1 for v in vals if v >= 0.5)
                    print(f"  past-target +{mins}m:  median {_med(vals):.2f}R beyond · "
                          f"{_pct(ran,len(vals)):.0f}% ran ≥0.5R further")
        print(f"  give-back:  {len(giveback)} trade(s) reached ≥1R then stopped "
              f"({_pct(len(giveback),n):.0f}%) · ≥2R-then-stopped: {len(giveback2)}")
        print(f"  median time in trade: {_med([r['minutes_held'] for r in rs]):.0f} min")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="JSON list of trades")
    ap.add_argument("--max-hold-min", type=int, default=480)
    ap.add_argument("--cache-dir", default="data/mfe_cache")
    args = ap.parse_args()

    trades = json.loads(Path(args.trades).read_text())
    by_inst = defaultdict(list)
    for t in trades:
        if t.get("instrument") and t.get("entry_ts") and t.get("entry") is not None:
            by_inst[t["instrument"]].append(t)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    client = PolygonFuturesClient()
    if not client.configured:
        raise SystemExit("POLYGON_API_KEY not set — cannot fetch 1-min bars.")

    results: list[dict] = []
    skipped = 0
    for inst, ts in by_inst.items():
        dates = [_parse_ts(t["entry_ts"]).date() for t in ts]
        start, end = min(dates), max(dates) + timedelta(days=1)
        print(f"fetching {inst} 1-min bars {start}→{end} ({len(ts)} trades)…")
        bars = _load_bars(client, inst, start, end, args.cache_dir)
        print(f"  {len(bars)} bars")
        for t in ts:
            r = _analyze_trade(t, bars, args.max_hold_min)
            if r is None:
                skipped += 1
            else:
                results.append(r)

    print(f"\nanalyzed {len(results)} trades · skipped {skipped} (no 1-min coverage / bad risk)")
    if results:
        _report(results)


if __name__ == "__main__":
    main()
