#!/usr/bin/env python3
"""IOC directional A/B for an existing transition shadow candidate population.

This is evidence-only. It does not regenerate signals or alter strategy code:
each candidate is replayed at its original timestamp as-is, with an exact
bracket mirror, plus a zero-trade control. Both trade variants use the same
PaperBroker IOC, cost, and pessimistic same-bar rules.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_VALUE


COMMISSION = 1.48
SLIPPAGE_TICKS = 1.0
IOC_TOLERANCE = {"MES": 16.0, "MNQ": 32.0}
RESOLVED = {"WIN", "LOSS", "BREAKEVEN"}


def _parse(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_candles(root: str | Path, instrument: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(Path(root, instrument).glob(f"{instrument}_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_dt"] = _parse(row["timestamp"])
                rows.append(row)
    return sorted(rows, key=lambda row: row["_dt"])


def mirror_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    entry = float(candidate["entry"])
    if candidate["direction"] == "LONG":
        stop = entry + (entry - float(candidate["stop"]))
        target = entry - (float(candidate["target"]) - entry)
        direction = "SHORT"
    else:
        stop = entry - (float(candidate["stop"]) - entry)
        target = entry + (entry - float(candidate["target"]))
        direction = "LONG"
    mirrored = dict(candidate)
    mirrored.update(direction=direction, stop=round(stop, 8), target=round(target, 8))
    return mirrored


def run_variant(row: dict[str, Any], candles: list[dict[str, Any]], variant: str, *, commission: float = COMMISSION, slippage_ticks: float = SLIPPAGE_TICKS, max_hold_bars: int = 24, candle_index: dict[datetime, int] | None = None) -> dict[str, Any]:
    if variant == "control":
        return {"status": "NO_TRADE", "result": "CONTROL", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0}
    candidate = row["candidate"] if variant == "original" else mirror_candidate(row["candidate"])
    start = _parse(row["ts"])
    if candle_index is None:
        candle_index = {
            _parse(bar.get("_dt", bar["timestamp"])): i
            for i, bar in enumerate(candles)
        }
    idx = candle_index.get(start)
    if idx is None:
        return {"status": "NO_DATA", "result": "NO_DATA", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0}
    future = candles[idx + 1:idx + 1 + max_hold_bars]
    if not future:
        return {"status": "NO_DATA", "result": "NO_DATA", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0}
    instrument = str(row["instrument"]).upper()
    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=IOC_TOLERANCE,
    )
    attempted = broker.execute_bracket(
        BracketOrder(
            instrument=instrument,
            direction=candidate["direction"],
            entry=float(candidate["entry"]),
            stop=float(candidate["stop"]),
            target=float(candidate["target"]),
            rr_ratio=float(candidate.get("rr_ratio") or 0.0),
            strategy=str(candidate.get("strategy") or "transition_directional_ab"),
            notes="evidence-only directional A/B",
            contracts=1,
        ),
        market_price=float(candles[idx]["close"]),
    )
    if attempted.result == "CANCELLED":
        return {"status": "NO_FILL", "result": "NO_FILL", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0, "reason": attempted.exit_reason}
    for bar in future:
        fill = broker.resolve_position(NextBarOHLC(open=float(bar["open"]), high=float(bar["high"]), low=float(bar["low"])))
        if fill is not None:
            realized = float(fill.pnl_dollars or 0.0)
            tick_value = TICK_VALUE.get(instrument, 1.0)
            exit_slippage = slippage_ticks if fill.exit_reason in {"STOP_HIT", "BREAKEVEN_STOP", "RUNNER_TRAIL"} else 0.0
            slippage = (slippage_ticks + exit_slippage) * tick_value
            gross = realized + slippage
            return {"status": "FILLED", "result": fill.result, "gross_pnl": gross, "slippage": slippage, "commission": commission, "net_pnl": realized - commission, "reason": fill.exit_reason}
    return {"status": "OPEN", "result": "OPEN", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0}


def profit_factor(values: list[float]) -> float | None:
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses:
        return round(wins / losses, 4)
    return math.inf if wins else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row["result"] in RESOLVED]
    gross_values = [float(row["gross_pnl"]) for row in resolved]
    values = [float(row["net_pnl"]) for row in resolved]
    return {
        "attempts": len(rows),
        "fills": sum(row["status"] == "FILLED" for row in rows),
        "no_fill": sum(row["status"] == "NO_FILL" for row in rows),
        "no_data": sum(row["status"] == "NO_DATA" for row in rows),
        "open": sum(row["status"] == "OPEN" for row in rows),
        "resolved": len(resolved),
        "wins": sum(value > 0 for value in values),
        "losses": sum(value < 0 for value in values),
        "gross_pnl": round(sum(gross_values), 2),
        "slippage": round(sum(float(row["slippage"]) for row in resolved), 2),
        "commission": round(sum(float(row["commission"]) for row in resolved), 2),
        "costs": round(sum(gross_values) - sum(values), 2),
        "net_pnl": round(sum(values), 2),
        "expectancy_per_fill": round(sum(values) / len(values), 2) if values else None,
        "profit_factor": profit_factor(values),
    }


def evaluate(candidates: list[dict[str, Any]], candles: list[dict[str, Any]], *, max_hold_bars: int = 24, commission: float = COMMISSION, slippage_ticks: float = SLIPPAGE_TICKS) -> dict[str, Any]:
    output: dict[str, Any] = {}
    candle_index = {
        _parse(bar.get("_dt", bar["timestamp"])): i
        for i, bar in enumerate(candles)
    }
    for variant in ("original", "mirrored", "control"):
        rows = []
        for candidate in candidates:
            result = run_variant(candidate, candles, variant, max_hold_bars=max_hold_bars, commission=commission, slippage_ticks=slippage_ticks, candle_index=candle_index)
            result["session"] = candidate.get("session_bucket", "unknown")
            result["ts"] = candidate["ts"]
            rows.append(result)
        rows.sort(key=lambda row: _parse(row["ts"]))
        sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            sessions[row["session"]].append(row)
        output[variant] = {"summary": summarize(rows), "sessions": {key: summarize(value) for key, value in sorted(sessions.items())}, "rows": rows}
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    args = parser.parse_args(argv)
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    instrument = str(candidates[0]["instrument"]).upper()
    report = {
        "config": {"entry_fill_model": "ioc_limit", "ioc_tolerance_ticks": IOC_TOLERANCE, "slippage_ticks_per_leg": SLIPPAGE_TICKS, "commission_round_trip": COMMISSION, "pessimistic_both_hit": True, "max_hold_bars": args.max_hold_bars},
        "source": {"candidate_file": str(args.candidates), "candidate_count": len(candidates), "instrument": instrument},
        "variants": evaluate(candidates, load_candles(args.candles, instrument), max_hold_bars=args.max_hold_bars),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value["summary"] for key, value in report["variants"].items()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
