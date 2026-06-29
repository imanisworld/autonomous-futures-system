#!/usr/bin/env python3
"""Compare wide 1-2-2 breakout entries with stop-aware pullback entries.

This is an offline Polygon-cache study.  It does not import the live runner,
write journals, change configuration, or place orders.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TICK_SIZE = {"MES": 0.25, "MNQ": 0.25}
POINT_VALUE = {"MES": 5.0, "MNQ": 2.0}
MAX_STOP_TICKS = {"MES": 60, "MNQ": 120}
COMMISSION_RT = 5.0
SLIPPAGE_TICKS = 1.0


@dataclass(frozen=True)
class Candidate:
    instrument: str
    detected_at: datetime
    direction: str
    entry: float
    stop: float
    target: float


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_bars(root: str | Path, instrument: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((Path(root) / instrument).glob(f"{instrument}_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["_dt"] = _parse_ts(str(row["timestamp"]))
            rows.append(row)
    rows.sort(key=lambda row: row["_dt"])
    return rows


def _bar_type(current: dict, previous: dict) -> str:
    breaks_high = float(current["high"]) > float(previous["high"])
    breaks_low = float(current["low"]) < float(previous["low"])
    if breaks_high and breaks_low:
        return "outside"
    if not breaks_high and not breaks_low:
        return "inside"
    return "two_up" if breaks_high else "two_down"


def _live_quality_eligible(row: dict, direction: str) -> bool:
    """Approximate the current pre-setup gates from fields in Polygon cache."""
    if row.get("market_condition") not in (None, "TRENDING"):
        return False
    if row.get("trend_strength") not in (None, "STRONG"):
        return False
    volume = row.get("volume")
    average = row.get("avg_volume")
    if volume is not None and average:
        if float(volume) / float(average) < 0.8:
            return False
    trend = row.get("trend_direction")
    if trend is not None:
        if direction == "LONG" and trend != "UP":
            return False
        if direction == "SHORT" and trend != "DOWN":
            return False
    price_vs_vwap = row.get("price_vs_vwap")
    if price_vs_vwap is not None:
        if direction == "LONG" and price_vs_vwap not in {"above", "at"}:
            return False
        if direction == "SHORT" and price_vs_vwap not in {"below", "at"}:
            return False
    ema9, ema21, ema55 = (
        row.get("ema_9"), row.get("ema_21"), row.get("ema_55")
    )
    if None not in (ema9, ema21, ema55):
        close = float(row["close"])
        if direction == "LONG" and not close > float(ema9) > float(ema21) > float(ema55):
            return False
        if direction == "SHORT" and not close < float(ema9) < float(ema21) < float(ema55):
            return False
    return True


def detect_wide_122(rows: list[dict], instrument: str) -> list[tuple[int, Candidate, Candidate]]:
    tick = TICK_SIZE[instrument]
    cap = tick * MAX_STOP_TICKS[instrument]
    found: list[tuple[int, Candidate, Candidate]] = []
    for idx in range(3, len(rows)):
        current, previous, two_back, three_back = (
            rows[idx], rows[idx - 1], rows[idx - 2], rows[idx - 3]
        )
        # Require truly adjacent 15-minute bars.  Never form a pattern across a
        # feed gap, daily maintenance break, or weekend.
        if any(
            rows[pos]["_dt"] - rows[pos - 1]["_dt"] != timedelta(minutes=15)
            for pos in (idx - 2, idx - 1, idx)
        ):
            continue
        types = (
            _bar_type(two_back, three_back),
            _bar_type(previous, two_back),
            _bar_type(current, previous),
        )
        if types[0] != "inside":
            continue
        if types[1:] == ("two_down", "two_up"):
            direction = "LONG"
        elif types[1:] == ("two_up", "two_down"):
            direction = "SHORT"
        else:
            continue
        if not _live_quality_eligible(current, direction):
            continue

        if direction == "LONG":
            original_entry = float(current["high"]) + tick
            stop = float(current["low"]) - (tick * 4)
            risk = original_entry - stop
            original_target = original_entry + (risk * 2)
            pullback_entry = stop + cap
            pullback_target = pullback_entry + (cap * 2)
        else:
            original_entry = float(current["low"]) - tick
            stop = float(current["high"]) + (tick * 4)
            risk = stop - original_entry
            original_target = original_entry - (risk * 2)
            pullback_entry = stop - cap
            pullback_target = pullback_entry - (cap * 2)
        if risk <= cap:
            continue

        common = {
            "instrument": instrument,
            "detected_at": current["_dt"],
            "direction": direction,
            "stop": stop,
        }
        found.append((
            idx,
            Candidate(entry=original_entry, target=original_target, **common),
            Candidate(entry=pullback_entry, target=pullback_target, **common),
        ))
    return found


def resolve(
    candidate: Candidate,
    future: list[dict],
    *,
    order_type: str,
    max_hold_bars: int = 32,
) -> dict[str, Any]:
    """Resolve only after a real future-bar fill; use stop-first ambiguity."""
    long = candidate.direction == "LONG"
    tick = TICK_SIZE[candidate.instrument]
    point_value = POINT_VALUE[candidate.instrument]
    filled = False
    fill_bar = None
    bars_after_fill = 0

    for row in future[:max_hold_bars]:
        high, low = float(row["high"]), float(row["low"])
        if not filled:
            touched = (
                high >= candidate.entry if order_type == "stop" and long
                else low <= candidate.entry if order_type == "stop"
                else low <= candidate.entry if long
                else high >= candidate.entry
            )
            if not touched:
                continue
            filled = True
            fill_bar = row

        bars_after_fill += 1
        hit_stop = low <= candidate.stop if long else high >= candidate.stop
        hit_target = high >= candidate.target if long else low <= candidate.target
        if hit_stop:
            exit_price = candidate.stop - tick if long else candidate.stop + tick
            result = "LOSS"
        elif hit_target:
            exit_price = candidate.target
            result = "WIN"
        else:
            continue
        effective_entry = (
            candidate.entry + tick if long else candidate.entry - tick
        )
        points = (
            exit_price - effective_entry if long
            else effective_entry - exit_price
        )
        return {
            "filled": True,
            "result": result,
            "pnl": round(points * point_value - COMMISSION_RT, 2),
            "fill_ts": fill_bar["_dt"].isoformat() if fill_bar else None,
            "bars_after_fill": bars_after_fill,
        }

    if not filled:
        return {"filled": False, "result": "NO_FILL", "pnl": 0.0}
    last = future[min(len(future), max_hold_bars) - 1]
    effective_entry = candidate.entry + tick if long else candidate.entry - tick
    close = float(last["close"])
    points = close - effective_entry if long else effective_entry - close
    return {
        "filled": True,
        "result": "TIMEOUT",
        "pnl": round(points * point_value - COMMISSION_RT, 2),
        "fill_ts": fill_bar["_dt"].isoformat() if fill_bar else None,
        "bars_after_fill": bars_after_fill,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [row for row in results if row["filled"]]
    pnls = [float(row["pnl"]) for row in filled]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    equity = peak = max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "signals": len(results),
        "fills": len(filled),
        "fill_rate_pct": round(100 * len(filled) / len(results), 1) if results else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(filled), 1) if filled else 0.0,
        "net_pnl": round(sum(pnls), 2),
        "expectancy_per_signal": round(sum(pnls) / len(results), 2) if results else 0.0,
        "expectancy_per_fill": round(sum(pnls) / len(filled), 2) if filled else 0.0,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 2)
            if losses else ("infinite" if wins else None)
        ),
        "max_drawdown": round(max_drawdown, 2),
    }


def run_study(root: str | Path, instrument: str) -> dict[str, Any]:
    rows = load_bars(root, instrument)
    detected = detect_wide_122(rows, instrument)
    observations = []
    for idx, original, pullback in detected:
        future = rows[idx + 1:]
        observations.append({
            "detected_at": original.detected_at,
            "original": resolve(original, future, order_type="stop"),
            "pullback": resolve(pullback, future, order_type="limit"),
        })

    midpoint = len(observations) // 2
    halves = {
        "first_half": observations[:midpoint],
        "second_half": observations[midpoint:],
    }
    return {
        "instrument": instrument,
        "bars": len(rows),
        "wide_122_signals": len(observations),
        "assumptions": {
            "max_hold_bars": 32,
            "commission_round_trip": COMMISSION_RT,
            "adverse_slippage_ticks": SLIPPAGE_TICKS,
            "same_bar_stop_target": "stop_first",
            "entry_bar": "strictly_after_detection",
            "quality_filter": (
                "TRENDING, STRONG trend/direction, >=0.8 relative volume, "
                "VWAP direction, EMA 9/21/55 alignment"
            ),
        },
        "overall": {
            "original": summarize([row["original"] for row in observations]),
            "pullback": summarize([row["pullback"] for row in observations]),
        },
        "splits": {
            name: {
                "original": summarize([row["original"] for row in sample]),
                "pullback": summarize([row["pullback"] for row in sample]),
            }
            for name, sample in halves.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", default="data/replay_polygon")
    parser.add_argument("--instrument", action="append", choices=("MES", "MNQ"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    instruments = args.instrument or ["MES", "MNQ"]
    reports = [run_study(args.candles, instrument) for instrument in instruments]
    if args.json:
        print(json.dumps(reports, indent=2))
        return 0
    for report in reports:
        print(f"\n{report['instrument']}: {report['wide_122_signals']} wide 1-2-2 signals")
        for policy in ("original", "pullback"):
            stats = report["overall"][policy]
            print(
                f"  {policy:<8} fills={stats['fills']:>3}/{stats['signals']:<3} "
                f"win={stats['win_rate_pct']:>5.1f}% net=${stats['net_pnl']:>8.2f} "
                f"exp/signal=${stats['expectancy_per_signal']:>6.2f} "
                f"PF={stats['profit_factor']} DD=${stats['max_drawdown']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
