#!/usr/bin/env python3
"""Build an offline missed-move report for transition/reclaim shadow candidates.

The input is a TradingView OHLCV CSV export. The output is evidence-only: it
does not create replay orders, paper orders, broker orders, or config changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VWAPData,
    VolumeData,
)
from strategy.shadow_setups import evaluate_shadow_setups, resolve_shadow_candidate


TARGET_STRATEGY = "transition_failed_breakdown_reclaim"


@dataclass(frozen=True)
class CsvBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_recent_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _load_csv(path: Path) -> list[CsvBar]:
    rows: list[CsvBar] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                CsvBar(
                    ts=_parse_dt(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("Volume") or row.get("volume") or 0),
                )
            )
    return rows


def _infer_instrument(path: Path, fallback: str | None) -> str:
    if fallback:
        return fallback.upper()
    name = path.name.upper()
    if "MNQ" in name:
        return "MNQ"
    if "MES" in name:
        return "MES"
    if "NQ" in name:
        return "MNQ"
    if "ES" in name:
        return "MES"
    return "MNQ"


def _infer_timeframe(path: Path, fallback: str | None) -> str:
    if fallback:
        return fallback
    match = re.search(r",\s*(\d+)", path.name)
    return f"{match.group(1)}m" if match else "5m"


def _filter_window(
    bars: Iterable[CsvBar],
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[CsvBar]:
    out = []
    for bar in bars:
        if start and bar.ts < start:
            continue
        if end and bar.ts > end:
            continue
        out.append(bar)
    return out


def _avg_volume(bars: list[CsvBar], idx: int, lookback: int = 20) -> float:
    prior = bars[max(0, idx - lookback):idx]
    if not prior:
        return max(bars[idx].volume, 1.0)
    return max(sum(bar.volume for bar in prior) / len(prior), 1.0)


def _state_for(
    bars: list[CsvBar],
    idx: int,
    *,
    instrument: str,
    timeframe: str,
) -> MarketState:
    bar = bars[idx]
    previous = bars[max(0, idx - 1)]
    direction = "UP" if bar.close > previous.close else "DOWN" if bar.close < previous.close else "SIDEWAYS"
    avg_volume = _avg_volume(bars, idx)
    return MarketState(
        timestamp=bar.ts,
        instrument=instrument,
        session="offline_csv",
        price=PriceData(last=bar.close, bid=bar.close, ask=bar.close),
        ohlc=OHLCData(
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            timeframe=timeframe,
            bar_start=bar.ts.isoformat(),
        ),
        vwap=VWAPData(value=bar.close, price_vs_vwap="at", reclaimed=None, holding=None),
        orb=ORBData(high=bar.high, low=bar.low, timeframe_minutes=15, status="inside"),
        previous_day=PreviousDayData(high=bar.high, low=bar.low, close=bar.close),
        volume=VolumeData(
            current_bar=int(bar.volume),
            avg_bar=int(avg_volume),
            relative=bar.volume / avg_volume,
        ),
        market_condition="RANGE_BOUND",
        trend=TrendData(direction=direction, strength="MODERATE"),
        raw={},
    )


def _mfe_mae(candidate, forward_bars: list[tuple[float, float]], instrument: str) -> dict:
    tick = {"MNQ": 0.25, "MES": 0.25, "NQ": 0.25, "ES": 0.25}.get(instrument, 0.25)
    entry = candidate.entry
    is_long = candidate.direction == "LONG"
    fill_idx = None
    for idx, (high, low) in enumerate(forward_bars):
        if low <= entry <= high:
            fill_idx = idx
            break
    if fill_idx is None:
        return {"mfe_points": None, "mae_points": None, "mfe_ticks": None, "mae_ticks": None}
    after_fill = forward_bars[fill_idx:]
    if is_long:
        mfe = max(high - entry for high, _ in after_fill)
        mae = min(low - entry for _, low in after_fill)
    else:
        mfe = max(entry - low for _, low in after_fill)
        mae = min(entry - high for high, _ in after_fill)
    return {
        "mfe_points": round(mfe, 2),
        "mae_points": round(mae, 2),
        "mfe_ticks": round(mfe / tick, 2),
        "mae_ticks": round(mae / tick, 2),
    }


def _scan(
    bars: list[CsvBar],
    *,
    instrument: str,
    timeframe: str,
    max_forward_bars: int,
) -> list[dict]:
    found: list[dict] = []
    for idx, _bar in enumerate(bars):
        state = _state_for(bars, idx, instrument=instrument, timeframe=timeframe)
        recent = [bar.to_recent_dict() for bar in bars[max(0, idx - 7):idx + 1]]
        for candidate in evaluate_shadow_setups(state, recent):
            if candidate.strategy != TARGET_STRATEGY:
                continue
            forward = [
                (bar.high, bar.low)
                for bar in bars[idx + 1:idx + 1 + max_forward_bars]
            ]
            outcome = resolve_shadow_candidate(
                candidate, forward, instrument=instrument
            ).to_dict()
            found.append(
                {
                    "ts": bars[idx].ts.isoformat(),
                    "instrument": instrument,
                    "timeframe": timeframe,
                    "candidate": candidate.to_dict(),
                    "outcome": outcome,
                    **_mfe_mae(candidate, forward, instrument),
                }
            )
    return found


def _summary(bars: list[CsvBar]) -> dict:
    if not bars:
        return {}
    return {
        "start": bars[0].ts.isoformat(),
        "end": bars[-1].ts.isoformat(),
        "bars": len(bars),
        "open": bars[0].open,
        "close": bars[-1].close,
        "net_points": round(bars[-1].close - bars[0].open, 2),
        "high": max(bar.high for bar in bars),
        "low": min(bar.low for bar in bars),
        "range_points": round(max(bar.high for bar in bars) - min(bar.low for bar in bars), 2),
        "total_volume": int(sum(bar.volume for bar in bars)),
        "max_bar_volume": int(max(bar.volume for bar in bars)),
    }


def _markdown(source: Path, summary: dict, rows: list[dict]) -> str:
    lines = [
        f"# Missed-Move Transition Report - {source.name}",
        "",
        "Evidence-only scan. No live, paper, or broker orders are created.",
        "",
        "## Window Summary",
        "",
    ]
    if summary:
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No bars in requested window.")
    lines.extend(["", "## Candidates", ""])
    if not rows:
        lines.append("No transition_failed_breakdown_reclaim candidates found.")
        return "\n".join(lines) + "\n"
    lines.append(
        "| ts | direction | entry | stop | target | result | fill | MFE pts | MAE pts | notes |"
    )
    lines.append("|---|---:|---:|---:|---:|---|---|---:|---:|---|")
    for row in rows:
        cand = row["candidate"]
        out = row["outcome"]
        lines.append(
            "| {ts} | {direction} | {entry} | {stop} | {target} | {result} | {filled} | {mfe} | {mae} | {notes} |".format(
                ts=row["ts"],
                direction=cand["direction"],
                entry=cand["entry"],
                stop=cand["stop"],
                target=cand["target"],
                result=out["result"],
                filled=out["entry_filled"],
                mfe=row["mfe_points"],
                mae=row["mae_points"],
                notes=str(cand["notes"]).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--instrument")
    parser.add_argument("--timeframe")
    parser.add_argument("--start", help="Inclusive ISO timestamp, e.g. 2026-07-08T18:00:00-04:00")
    parser.add_argument("--end", help="Inclusive ISO timestamp")
    parser.add_argument("--max-forward-bars", type=int, default=24)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    all_bars = _load_csv(args.csv_path)
    bars = _filter_window(
        all_bars,
        start=_parse_dt(args.start) if args.start else None,
        end=_parse_dt(args.end) if args.end else None,
    )
    instrument = _infer_instrument(args.csv_path, args.instrument)
    timeframe = _infer_timeframe(args.csv_path, args.timeframe)
    rows = _scan(
        bars,
        instrument=instrument,
        timeframe=timeframe,
        max_forward_bars=max(1, args.max_forward_bars),
    )
    summary = _summary(bars)
    output = args.output or Path("logs") / f"missed_move_transition_{instrument}_{timeframe}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_markdown(args.csv_path, summary, rows))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps({"summary": summary, "candidates": rows}, indent=2))
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
