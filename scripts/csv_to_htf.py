"""
scripts/csv_to_htf.py

Convert TradingView HTF CSVs (1D, 4H, 1H) to JSONL for use as HTF context
in replay and live signal evaluation.

Unlike csv_to_replay.py, this converter:
  - Requires no ORB data
  - Applies no session filter
  - Derives bar direction and adds a rolling trend bias (3-bar lookback)
  - Outputs one JSON record per bar

Usage:
    python3 scripts/csv_to_htf.py "CME_MINI_MNQ1!, 1D.csv" --out-dir data/htf
    python3 scripts/csv_to_htf.py "CME_MINI_MNQ1!, 240 (1).csv" --out-dir data/htf
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_UTC = timezone.utc

_STRAT_LABEL = {
    "0": "none",
    "1": "inside",
    "2": "directional",
}

_INSTRUMENT_MAP = {"MNQ": "MNQ", "MES": "MES", "MGC": "MGC", "MCL": "MCL"}


def _infer_instrument(path: Path) -> str:
    name = path.stem.upper()
    for key in _INSTRUMENT_MAP:
        if key in name:
            return key
    return "MNQ"


def _infer_timeframe(path: Path) -> str:
    stem = path.stem
    # "1D" in name → daily
    if re.search(r"1D", stem, re.IGNORECASE):
        return "1D"
    # "240" → treat as 4H (the actual 4H file uses 240-min bars)
    match = re.search(r"[,\s_](\d+)\s*(?:\(\d+\))?$", stem)
    if match:
        mins = int(match.group(1))
        if mins == 1440:
            return "1D"
        if mins >= 60:
            return f"{mins // 60}H"
        return f"{mins}m"
    return "unknown"


def _parse_time(value: str) -> datetime:
    value = value.strip()
    # Plain date: 2019-05-06
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(tzinfo=_UTC)
    # ISO with offset
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(_UTC)


def _bar_direction(o: float, c: float) -> str:
    if c > o:
        return "UP"
    if c < o:
        return "DOWN"
    return "FLAT"


def _rolling_bias(closes: list[float], lookback: int = 3) -> str:
    """Return UP/DOWN/NEUTRAL based on whether closes are trending over lookback bars."""
    if len(closes) < lookback:
        return "NEUTRAL"
    window = closes[-lookback:]
    ups = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    downs = sum(1 for i in range(1, len(window)) if window[i] < window[i - 1])
    if ups > downs:
        return "UP"
    if downs > ups:
        return "DOWN"
    return "NEUTRAL"


def convert(csv_path: Path, out_dir: Path) -> Path:
    instrument = _infer_instrument(csv_path)
    timeframe = _infer_timeframe(csv_path)
    print(f"[htf-convert] {csv_path.name} → instrument={instrument} timeframe={timeframe}")

    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                dt = _parse_time(row["time"])
                o = float(row["open"])
                h = float(row["high"])
                lo = float(row["low"])
                c = float(row["close"])
                vol = int(float(row.get("Volume") or row.get("volume") or 0))
                bt1 = _STRAT_LABEL.get(str(row.get("Bar Type 1 Label", "0")).strip(), "none")
                bt2 = _STRAT_LABEL.get(str(row.get("Bar Type 2 Label", "0")).strip(), "none")
                bt3 = _STRAT_LABEL.get(str(row.get("Bar Type 3 Label", "0")).strip(), "none")
            except (ValueError, KeyError):
                continue
            rows.append({
                "dt": dt,
                "open": o, "high": h, "low": lo, "close": c,
                "volume": vol,
                "bar_type_1": bt1,
                "bar_type_2": bt2,
                "bar_type_3": bt3,
            })

    rows.sort(key=lambda r: r["dt"])

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = csv_path.stem.replace(" ", "_").replace(",", "")
    out_path = out_dir / f"{stem}.jsonl"

    closes: list[float] = []
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            closes.append(r["close"])
            direction = _bar_direction(r["open"], r["close"])
            bias = _rolling_bias(closes)
            record = {
                "timestamp": r["dt"].isoformat(),
                "unix": int(r["dt"].timestamp()),
                "instrument": instrument,
                "timeframe": timeframe,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "bar_type_1": r["bar_type_1"],
                "bar_type_2": r["bar_type_2"],
                "bar_type_3": r["bar_type_3"],
                "direction": direction,
                "bias": bias,
            }
            f.write(json.dumps(record) + "\n")

    print(f"[htf-convert] {len(rows)} bars written → {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert TradingView HTF CSV to JSONL")
    parser.add_argument("csv", nargs="+", help="Path(s) to CSV file(s)")
    parser.add_argument("--out-dir", default="data/htf", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    for csv_arg in args.csv:
        csv_path = Path(csv_arg)
        if not csv_path.exists():
            print(f"[htf-convert] ERROR: {csv_path} not found", file=sys.stderr)
            continue
        convert(csv_path, out_dir)


if __name__ == "__main__":
    main()
