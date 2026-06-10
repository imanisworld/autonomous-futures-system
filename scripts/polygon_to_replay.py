#!/usr/bin/env python3
"""Download Polygon/Massive futures bars and derive replay candle JSONL.

Replaces the manual TradingView-CSV-export pipeline for bulk backtest data:
instead of exporting "CME_MINI_MES1!, 15.csv" by hand (~74 days max), pull up
to ~2 years of 15m bars straight from the exchange feed and derive the same
candle schema csv_to_replay produces, reusing ITS helpers (sessions, VWAP,
trend, ORB status, FTFC) so live/replay definitions stay single-source.

Derivations from raw OHLCV (no Pine columns available):
  • EMA 9/21/55/200 — standard EMA seeded with SMA; trend via the shared
    context.trend.classify_trend (same as live state_builder).
  • NY ORB — Pine-faithful: reset at the 09:30 ET session open, accumulate the
    first 15 minutes, persist until the next NY open. Bars before the first
    ORB of the dataset are skipped (csv_to_replay does the same).
  • Strat bar types — 1/2/3 vs the previous bar's high/low (classify_htf_bar
    collapsed to the engine's 1/2/3 vocabulary).
  • HOD/LOD — running CME-day extremes; PDH/PDL/PDC via detect_day_boundaries.
  • HTF FTFC — 1h/4h/daily resampled from the same bars (bar-close delayed by
    htf_at, no lookahead).
  • Supply/demand zones — not derivable from OHLC; left None (engine treats
    them as absent, same as TV exports without those columns).

Usage:
    python3 scripts/polygon_to_replay.py --symbol MES --start 2024-09-01 \
        --end 2026-06-01 [--timeframe 15] [--out data/replay_polygon]
Then:
    python3 scripts/run_replay_batch.py --candles data/replay_polygon/MES
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context.trend import classify_trend  # noqa: E402
from scripts.csv_to_replay import (  # noqa: E402
    _ET,
    build_ftfc_context,
    classify_htf_bar,
    compute_vwap,
    derive_market_condition,
    derive_orb_status,
    detect_day_boundaries,
    detect_session,
    direction_from_bar,
    htf_at,
)
from sources.polygon_client import PolygonFuturesClient  # noqa: E402

LOOKBACK = 20          # bars for rolling avg_volume (matches csv_to_replay)
ORB_MINUTES = 15       # Pine i_orb_min default
NY_OPEN_HOUR, NY_OPEN_MINUTE = 9, 30


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """Standard EMA, SMA-seeded; None until the seed window fills."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return out
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    out[period - 1] = ema
    for i in range(period, len(closes)):
        ema = ema + k * (closes[i] - ema)
        out[i] = ema
    return out


def collapse_bar_type(bt: str | None) -> str | None:
    """classify_htf_bar vocabulary (1/2U/2D/3) → engine vocabulary (1/2/3)."""
    if bt in ("2U", "2D"):
        return "2"
    return bt


def resample(bars: list[dict], minutes: int, label: str) -> list[dict]:
    """Resample base bars into fixed UTC-aligned buckets with strat typing."""
    buckets: dict[int, dict] = {}
    width = minutes * 60
    for b in bars:
        start = (b["ts"] // width) * width
        agg = buckets.get(start)
        if agg is None:
            buckets[start] = {
                "ts": start, "open": b["open"], "high": b["high"],
                "low": b["low"], "close": b["close"],
            }
        else:
            agg["high"] = max(agg["high"], b["high"])
            agg["low"] = min(agg["low"], b["low"])
            agg["close"] = b["close"]
    out: list[dict] = []
    previous = None
    for start in sorted(buckets):
        bar = buckets[start]
        bar["bar_type"] = classify_htf_bar(bar, previous)
        bar["direction"] = direction_from_bar(bar["bar_type"], bar)
        bar["label"] = label
        out.append(bar)
        previous = bar
    return out


def resample_daily(bars: list[dict]) -> list[dict]:
    """Daily resample on the CME session day (18:00 ET boundary)."""
    groups: dict[date, dict] = {}
    order: list[date] = []
    for b in bars:
        local = datetime.fromtimestamp(b["ts"], tz=_ET)
        # Session belongs to the day it STARTED (>=18:00 ET starts next session).
        session_day = local.date() if local.hour < 18 else local.date() + timedelta(days=1)
        agg = groups.get(session_day)
        if agg is None:
            groups[session_day] = {
                "ts": b["ts"], "open": b["open"], "high": b["high"],
                "low": b["low"], "close": b["close"],
            }
            order.append(session_day)
        else:
            agg["high"] = max(agg["high"], b["high"])
            agg["low"] = min(agg["low"], b["low"])
            agg["close"] = b["close"]
    out: list[dict] = []
    previous = None
    for day in order:
        bar = groups[day]
        bar["bar_type"] = classify_htf_bar(bar, previous)
        bar["direction"] = direction_from_bar(bar["bar_type"], bar)
        bar["label"] = "daily"
        out.append(bar)
        previous = bar
    return out


def derive_candles(
    raw: list[dict], instrument: str, timeframe_minutes: int
) -> list[dict]:
    """raw bars [{ts(unix sec), open, high, low, close, volume}] → replay candles."""
    closes_all = [b["close"] for b in raw]
    ema9_s = ema_series(closes_all, 9)
    ema21_s = ema_series(closes_all, 21)
    ema55_s = ema_series(closes_all, 55)
    ema200_s = ema_series(closes_all, 200)

    one_hour_bars = resample(raw, 60, "1h")
    four_hour_bars = resample(raw, 240, "4h")
    daily_bars = resample_daily(raw)

    boundaries = detect_day_boundaries(raw)
    day_ranges: list[tuple[int, int]] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(raw)
        day_ranges.append((start, end))

    def prev_day_stats(idx: int) -> tuple[float, float, float]:
        for i, (s, e) in enumerate(day_ranges):
            if s <= idx < e:
                if i == 0:
                    first = raw[0]
                    return first["high"], first["low"], first["close"]
                ps, pe = day_ranges[i - 1]
                prev = raw[ps:pe]
                return (max(b["high"] for b in prev), min(b["low"] for b in prev),
                        prev[-1]["close"])
        fb = raw[0]
        return fb["high"], fb["low"], fb["close"]

    orb_bars_needed = max(1, round(ORB_MINUTES / timeframe_minutes))
    orb_high = orb_low = None
    orb_count = 0
    orb_done = False
    hod = lod = None
    current_day_range = None

    candles: list[dict] = []
    session_bars: list[dict] = []
    prev_session = None
    closes: list[float] = []

    for i, bar in enumerate(raw):
        dt_utc = datetime.fromtimestamp(bar["ts"], tz=timezone.utc)
        et = dt_utc.astimezone(_ET)
        session = detect_session(dt_utc)

        # CME-day extremes (HOD/LOD) reset at the 18:00 ET day boundary.
        day_range = next(((s, e) for (s, e) in day_ranges if s <= i < e), None)
        if day_range != current_day_range:
            current_day_range = day_range
            hod = lod = None
        hod = bar["high"] if hod is None else max(hod, bar["high"])
        lod = bar["low"] if lod is None else min(lod, bar["low"])

        # NY ORB — Pine-faithful: reset at the 09:30 ET bar, accumulate the
        # opening window, then freeze until the next NY open.
        is_ny_open_bar = et.hour == NY_OPEN_HOUR and et.minute == NY_OPEN_MINUTE
        if is_ny_open_bar:
            orb_high, orb_low = bar["high"], bar["low"]
            orb_count = 1
            orb_done = orb_count >= orb_bars_needed
        elif orb_high is not None and not orb_done:
            orb_high = max(orb_high, bar["high"])
            orb_low = min(orb_low, bar["low"])
            orb_count += 1
            orb_done = orb_count >= orb_bars_needed
        closes.append(bar["close"])

        if orb_high is None or orb_low is None:
            continue  # no ORB yet (dataset starts before its first NY open)

        if session != prev_session:
            session_bars = []
            prev_session = session
        session_bars.append(bar)
        vwap = compute_vwap(session_bars)

        ema9, ema21 = ema9_s[i], ema21_s[i]
        ema55, ema200 = ema55_s[i], ema200_s[i]
        if ema9 and ema21 and ema55:
            trend_dir, trend_str = classify_trend(bar["close"], ema9, ema21, ema55)
            market_cond = ("TRENDING" if trend_str in ("STRONG", "MODERATE")
                           else derive_market_condition(closes))
        else:
            continue  # EMA warmup not complete — skip rather than guess trend

        prev_bar = raw[i - 1] if i > 0 else None
        prev2_bar = raw[i - 2] if i > 1 else None
        cur_type = collapse_bar_type(classify_htf_bar(bar, prev_bar)) if prev_bar else None
        prev_type = (collapse_bar_type(classify_htf_bar(prev_bar, prev2_bar))
                     if prev_bar and prev2_bar else None)
        prev3_bar = raw[i - 3] if i > 2 else None
        prev2_type = (collapse_bar_type(classify_htf_bar(prev2_bar, prev3_bar))
                      if prev2_bar and prev3_bar else None)

        vol_window = [raw[j]["volume"] for j in range(max(0, i - LOOKBACK), i + 1)]
        avg_vol = max(1, int(sum(vol_window) / len(vol_window)))

        pdh, pdl, pdc = prev_day_stats(i)
        htf_context = build_ftfc_context(
            htf_at(daily_bars, bar["ts"]),
            htf_at(four_hour_bars, bar["ts"]),
            htf_at(one_hour_bars, bar["ts"]),
        )

        candles.append({
            "timestamp": dt_utc.isoformat(),
            "instrument": instrument,
            "session": session,
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": int(bar["volume"]),
            "avg_volume": avg_vol,
            "vwap": vwap,
            "price_vs_vwap": ("above" if bar["close"] > vwap
                              else ("below" if bar["close"] < vwap else "at")),
            "ema_9": ema9, "ema_21": ema21, "ema_55": ema55, "ema_200": ema200,
            "hod": hod, "lod": lod,
            "supply_top": None, "supply_bottom": None,
            "demand_top": None, "demand_bottom": None,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "orb_status": derive_orb_status(
                bar["close"], orb_high, orb_low,
                previous_close=prev_bar["close"] if prev_bar else None,
            ),
            "market_condition": market_cond,
            "trend_direction": trend_dir,
            "trend_strength": trend_str,
            "previous_day_high": pdh,
            "previous_day_low": pdl,
            "previous_day_close": pdc,
            "price_vs_pdh": ("above" if bar["close"] > pdh
                             else ("below" if bar["close"] < pdh else "at")),
            "price_vs_pdl": ("above" if bar["close"] > pdl
                             else ("below" if bar["close"] < pdl else "at")),
            "timeframe": f"{timeframe_minutes}m",
            "current_bar_type": cur_type,
            "previous_bar_type": prev_type,
            "two_bars_back_type": prev2_type,
            "previous_bar_high": prev_bar["high"] if prev_bar else None,
            "previous_bar_low": prev_bar["low"] if prev_bar else None,
            "two_bars_back_high": prev2_bar["high"] if prev2_bar else None,
            "two_bars_back_low": prev2_bar["low"] if prev2_bar else None,
            **htf_context,
        })
    return candles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="continuous symbol, e.g. MES")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--timeframe", type=int, default=15, help="bar minutes (default 15)")
    parser.add_argument("--warmup-days", type=int, default=10,
                        help="extra days fetched before --start for EMA200 warmup")
    parser.add_argument("--out", default="data/replay_polygon", help="output directory")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    symbol = args.symbol.strip().upper()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    client = PolygonFuturesClient()
    if not client.configured:
        print("[polygon] POLYGON_API_KEY not set", file=sys.stderr)
        return 1

    fetch_start = start - timedelta(days=args.warmup_days)
    print(f"[polygon] fetching {symbol} {args.timeframe}m bars {fetch_start}..{end} "
          f"(warmup {args.warmup_days}d)")
    bars = client.fetch_continuous(symbol, fetch_start, end, args.timeframe)
    print(f"[polygon] {len(bars)} bars fetched")
    if not bars:
        return 1

    raw = [{"ts": int(b.ts.timestamp()), "open": b.open, "high": b.high,
            "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    candles = derive_candles(raw, symbol, args.timeframe)
    # Drop warmup-period candles; keep only the requested range.
    start_iso = start.isoformat()
    candles = [c for c in candles if c["timestamp"][:10] >= start_iso]
    print(f"[polygon] {len(candles)} candles derived")

    out_dir = Path(args.out) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for c in candles:
        by_day.setdefault(c["timestamp"][:10], []).append(c)
    for day, day_candles in sorted(by_day.items()):
        path = out_dir / f"{symbol}_{day}.jsonl"
        with path.open("w") as f:
            for c in day_candles:
                f.write(json.dumps(c) + "\n")
    print(f"[polygon] wrote {len(by_day)} day files → {out_dir}")
    print(f"[polygon] next: python3 scripts/run_replay_batch.py --candles {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
