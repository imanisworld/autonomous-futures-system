"""
scripts/csv_to_replay.py

Convert TradingView CSV export (MNQ 15m with ORB + Bar Type labels) to:
  1. A replay JSONL file the ReplayEngine can consume
  2. A signal analysis report (where signals fired and why)
  3. A Pine validation report (cross-check ORB levels and bar types)

Usage:
    python3 scripts/csv_to_replay.py <csv_path> [--date YYYY-MM-DD]
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

LOOKBACK = 20  # bars for rolling avg_volume


def ts_to_dt(unix_sec: int) -> datetime:
    return datetime.fromtimestamp(unix_sec, tz=_UTC)


def detect_session(dt: datetime) -> str:
    et = dt.astimezone(_ET).time()
    from datetime import time
    if et >= time(19, 0) or et < time(3, 0):
        return "asian"
    if time(3, 0) <= et < time(8, 30):
        return "london"
    if time(8, 30) <= et < time(9, 30):
        return "session_gap"
    if time(9, 30) <= et <= time(12, 0):
        return "new_york"
    return "off_hours"


def bar_type_str(t1: str, t2: str, t3: str) -> str | None:
    if t1 == "1":
        return "1"
    if t2 == "1":
        return "2"
    if t3 == "1":
        return "3"
    return None


def derive_orb_status(close: float, orb_high: float, orb_low: float) -> str:
    if close > orb_high:
        return "above"
    if close < orb_low:
        return "below"
    orb_range = orb_high - orb_low
    mid = orb_low + orb_range / 2
    if close >= mid:
        return "reclaimed_high"
    return "reclaimed_low"


def derive_trend(closes: list[float]) -> tuple[str, str]:
    if len(closes) < 5:
        return "SIDEWAYS", "WEAK"
    recent = closes[-5:]
    slope = recent[-1] - recent[0]
    spread = max(recent) - min(recent)
    if abs(slope) < spread * 0.2:
        return "SIDEWAYS", "WEAK"
    direction = "UP" if slope > 0 else "DOWN"
    strength = "STRONG" if abs(slope) > spread * 0.6 else "MODERATE"
    return direction, strength


def derive_market_condition(closes: list[float]) -> str:
    if len(closes) < 10:
        return "UNKNOWN"
    window = closes[-10:]
    high = max(window)
    low = min(window)
    spread = high - low
    # trending = consistent directional move
    first_half = window[:5]
    second_half = window[5:]
    avg_first = sum(first_half) / 5
    avg_second = sum(second_half) / 5
    direction_pct = abs(avg_second - avg_first) / max(spread, 1)
    if direction_pct > 0.5:
        return "TRENDING"
    if spread < closes[-1] * 0.003:
        return "CONSOLIDATING"
    return "CHOPPY"


def compute_vwap(bars: list[dict]) -> float:
    """Session VWAP: sum(typical_price * volume) / sum(volume)."""
    total_tp_vol = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars)
    total_vol = sum(b["volume"] for b in bars)
    return round(total_tp_vol / max(total_vol, 1), 2)


def load_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def detect_day_boundaries(bars: list[dict]) -> list[int]:
    """Return indices where the CME equity futures session date changes (17:00 ET)."""
    boundaries = [0]
    from datetime import time
    for i in range(1, len(bars)):
        prev_dt = ts_to_dt(bars[i - 1]["ts"]).astimezone(_ET)
        curr_dt = ts_to_dt(bars[i]["ts"]).astimezone(_ET)
        # New CME day starts at 18:00 ET
        prev_session_day = prev_dt.date() if prev_dt.time() >= time(18, 0) else (prev_dt - timedelta(days=1)).date()
        curr_session_day = curr_dt.date() if curr_dt.time() >= time(18, 0) else (curr_dt - timedelta(days=1)).date()
        if curr_session_day != prev_session_day:
            boundaries.append(i)
    return boundaries


def convert(csv_path: Path, out_dir: Path) -> Path:
    raw = load_csv(csv_path)
    stem = csv_path.stem.replace(" ", "_").replace(",", "")

    # Parse raw rows
    bars: list[dict] = []
    for row in raw:
        ts = int(row["time"])
        bars.append({
            "ts": ts,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["Volume"]),
            "orb_high_raw": row["ORB High"].strip(),
            "orb_low_raw": row["ORB Low"].strip(),
            "bt1": row["Bar Type 1 Label"].strip(),
            "bt2": row["Bar Type 2 Label"].strip(),
            "bt3": row["Bar Type 3 Label"].strip(),
        })

    # Detect day boundaries to track prev day high/low/close
    boundaries = detect_day_boundaries(bars)
    day_ranges: list[tuple[int, int]] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(bars)
        day_ranges.append((start, end))

    def get_prev_day_stats(bar_idx: int) -> tuple[float, float, float]:
        for i, (start, end) in enumerate(day_ranges):
            if start <= bar_idx < end:
                if i == 0:
                    # No previous day in data — use first bar's OHLC as proxy
                    first = bars[0]
                    return first["high"], first["low"], first["close"]
                ps, pe = day_ranges[i - 1]
                day_bars = bars[ps:pe]
                pdh = max(b["high"] for b in day_bars)
                pdl = min(b["low"] for b in day_bars)
                pdc = day_bars[-1]["close"]
                return pdh, pdl, pdc
        pb = bars[0]
        return pb["high"], pb["low"], pb["close"]

    # Build candle records
    candles: list[dict] = []
    session_bars: list[dict] = []  # for VWAP calculation within session
    prev_session = None
    closes: list[float] = []

    for i, bar in enumerate(bars):
        orb_high_raw = bar["orb_high_raw"]
        orb_low_raw = bar["orb_low_raw"]

        # Skip bars without ORB — can't replay without it
        if not orb_high_raw or not orb_low_raw:
            closes.append(bar["close"])
            continue

        orb_high = float(orb_high_raw)
        orb_low = float(orb_low_raw)

        dt = ts_to_dt(bar["ts"])
        session = detect_session(dt)

        # Reset VWAP accumulation at session change
        if session != prev_session:
            session_bars = []
            prev_session = session

        session_bars.append(bar)
        vwap = compute_vwap(session_bars)

        closes.append(bar["close"])
        trend_dir, trend_str = derive_trend(closes)
        market_cond = derive_market_condition(closes)
        orb_status = derive_orb_status(bar["close"], orb_high, orb_low)

        # Rolling avg_volume
        vol_window = [bars[j]["volume"] for j in range(max(0, i - LOOKBACK), i + 1)]
        avg_vol = max(1, int(sum(vol_window) / len(vol_window)))

        # Bar types from Pine labels
        cur_type = bar_type_str(bar["bt1"], bar["bt2"], bar["bt3"])
        prev_bar = bars[i - 1] if i > 0 else None
        prev2_bar = bars[i - 2] if i > 1 else None
        prev_type = bar_type_str(prev_bar["bt1"], prev_bar["bt2"], prev_bar["bt3"]) if prev_bar else None
        prev2_type = bar_type_str(prev2_bar["bt1"], prev2_bar["bt2"], prev2_bar["bt3"]) if prev2_bar else None

        pdh, pdl, pdc = get_prev_day_stats(i)
        price_vs_pdh = "above" if bar["close"] > pdh else ("below" if bar["close"] < pdh else "at")
        price_vs_pdl = "above" if bar["close"] > pdl else ("below" if bar["close"] < pdl else "at")

        candle = {
            "timestamp": dt.isoformat(),
            "instrument": "MNQ",
            "session": session,
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "avg_volume": avg_vol,
            "vwap": vwap,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "orb_status": orb_status,
            "market_condition": market_cond,
            "trend_direction": trend_dir,
            "trend_strength": trend_str,
            "previous_day_high": pdh,
            "previous_day_low": pdl,
            "previous_day_close": pdc,
            "price_vs_pdh": price_vs_pdh,
            "price_vs_pdl": price_vs_pdl,
            "timeframe": "15m",
            "current_bar_type": cur_type,
            "previous_bar_type": prev_type,
            "two_bars_back_type": prev2_type,
            "previous_bar_high": prev_bar["high"] if prev_bar else None,
            "previous_bar_low": prev_bar["low"] if prev_bar else None,
            "two_bars_back_high": prev2_bar["high"] if prev2_bar else None,
            "two_bars_back_low": prev2_bar["low"] if prev2_bar else None,
        }
        candles.append(candle)

    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{stem}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for c in candles:
            f.write(json.dumps(c) + "\n")

    print(f"[convert] {len(candles)} candles written → {jsonl_path}")
    return jsonl_path


def analyze_signals(jsonl_path: Path) -> None:
    """Print a table of every bar: session, ORB status, bar type, and signal potential."""
    print("\n" + "=" * 90)
    print("SIGNAL ANALYSIS — bar-by-bar")
    print("=" * 90)
    print(f"{'Time (ET)':<22} {'Sess':<10} {'Close':>9} {'ORB H':>9} {'ORB L':>9} {'Status':<16} {'BT':<4} {'Trend':<10} {'Cond':<14}")
    print("-" * 90)

    signals_found = []

    with jsonl_path.open() as f:
        for line in f:
            c = json.loads(line)
            dt_utc = datetime.fromisoformat(c["timestamp"])
            dt_et = dt_utc.astimezone(_ET)
            time_str = dt_et.strftime("%m/%d %H:%M ET")

            bt = c.get("current_bar_type") or "-"
            orb_status = c.get("orb_status", "?")
            sess = c.get("session", "?")
            trend = f"{c.get('trend_direction','?')} {c.get('trend_strength','?')}"
            cond = c.get("market_condition", "?")

            # Flag potential signals
            flag = ""
            if orb_status in ("reclaimed_high", "reclaimed_low") and sess in ("new_york", "london"):
                flag = " ← ORB RECLAIM"
                signals_found.append({"time": time_str, "type": "orb_reclaim", "candle": c})
            if bt == "2" and orb_status == "above" and sess == "new_york":
                flag = " ← STRAT-2 LONG"
                signals_found.append({"time": time_str, "type": "strat_2_long", "candle": c})
            if bt == "2" and orb_status == "below" and sess == "new_york":
                flag = " ← STRAT-2 SHORT"
                signals_found.append({"time": time_str, "type": "strat_2_short", "candle": c})

            print(
                f"{time_str:<22} {sess:<10} {c['close']:>9.2f} "
                f"{c['orb_high']:>9.2f} {c['orb_low']:>9.2f} "
                f"{orb_status:<16} {bt:<4} {trend:<10} {cond:<14}{flag}"
            )

    print("\n" + "=" * 90)
    print(f"SIGNALS FOUND: {len(signals_found)}")
    for s in signals_found:
        print(f"  {s['time']} — {s['type']}")


def validate_pine(csv_path: Path, jsonl_path: Path) -> None:
    """Cross-check ORB levels and bar types between Pine CSV and derived JSONL."""
    print("\n" + "=" * 90)
    print("PINE VALIDATION — ORB levels and bar types")
    print("=" * 90)

    raw = load_csv(csv_path)
    raw_by_ts = {}
    for row in raw:
        ts = int(row["time"])
        raw_by_ts[ts] = row

    mismatches = []
    checked = 0

    with jsonl_path.open() as f:
        for line in f:
            c = json.loads(line)
            dt = datetime.fromisoformat(c["timestamp"])
            ts = int(dt.timestamp())
            row = raw_by_ts.get(ts)
            if not row:
                continue
            checked += 1

            # ORB high/low
            pine_orb_h = float(row["ORB High"]) if row["ORB High"].strip() else None
            pine_orb_l = float(row["ORB Low"]) if row["ORB Low"].strip() else None
            if pine_orb_h and abs(pine_orb_h - c["orb_high"]) > 0.01:
                mismatches.append(f"ORB HIGH mismatch at {c['timestamp']}: pine={pine_orb_h} derived={c['orb_high']}")
            if pine_orb_l and abs(pine_orb_l - c["orb_low"]) > 0.01:
                mismatches.append(f"ORB LOW mismatch at {c['timestamp']}: pine={pine_orb_l} derived={c['orb_low']}")

            # Bar type
            pine_bt = bar_type_str(row["Bar Type 1 Label"].strip(), row["Bar Type 2 Label"].strip(), row["Bar Type 3 Label"].strip())
            derived_bt = c.get("current_bar_type")
            if pine_bt != derived_bt:
                mismatches.append(f"BAR TYPE mismatch at {c['timestamp']}: pine={pine_bt} derived={derived_bt}")

    print(f"Bars checked: {checked}")
    if mismatches:
        print(f"MISMATCHES ({len(mismatches)}):")
        for m in mismatches:
            print(f"  {m}")
    else:
        print("All ORB levels and bar types match Pine output.")


def run_replay(jsonl_path: Path) -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from replay.replay_engine import ReplayEngine

    print("\n" + "=" * 90)
    print("REPLAY ENGINE")
    print("=" * 90)

    engine = ReplayEngine(log_dir="logs/replay")

    # Split by day if multiple days in file
    by_day: dict[str, list[dict]] = {}
    with jsonl_path.open() as f:
        for line in f:
            c = json.loads(line)
            dt = datetime.fromisoformat(c["timestamp"])
            day = dt.date().isoformat()
            by_day.setdefault(day, []).append(c)

    for day, candles in sorted(by_day.items()):
        day_path = jsonl_path.parent / f"_day_{day}.jsonl"
        with day_path.open("w") as f:
            for c in candles:
                f.write(json.dumps(c) + "\n")
        try:
            report = engine.run(day_path, review_date=day)
            print(f"\n{day} — {report.candles_processed} bars | "
                  f"trades={report.approved_trades} wins={report.wins} losses={report.losses} "
                  f"open={report.open_trades} PnL=${report.realized_pnl_dollars:+.2f}")
            if report.stopped_reason:
                print(f"  stopped: {report.stopped_reason}")
        except Exception as e:
            print(f"\n{day} — REPLAY ERROR: {e}")
        finally:
            day_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/csv_to_replay.py <csv_path>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    out_dir = csv_path.parent / "replay_out"

    jsonl_path = convert(csv_path, out_dir)
    analyze_signals(jsonl_path)
    validate_pine(csv_path, jsonl_path)
    run_replay(jsonl_path)
