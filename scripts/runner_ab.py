#!/usr/bin/env python3
"""A/B test the production 1-contract static and runner exit paths.

Both modes receive the exact same decision bar and subsequent candles; only
``runner_mode`` changes. Defaults match the replay paper-fill contract: IOC
entry, one adverse tick on market legs, pessimistic same-bar resolution, and
the evidence-layer $1.48 commission. This is a reporting-only harness.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker


DEFAULT_COMMISSION_ROUND_TRIP = 1.48
DEFAULT_SLIPPAGE_TICKS = 1.0
DEFAULT_IOC_TOLERANCE = {"MES": 16.0, "MNQ": 32.0}
RESOLVED = {"WIN", "LOSS", "BREAKEVEN"}


def _parse(ts: Any) -> datetime:
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_candles(candles_dir: str | Path, inst: str) -> list[tuple[datetime, float, float, float, float]]:
    out = []
    for path in sorted(Path(candles_dir, inst).glob(f"{inst}_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out.append((_parse(row["timestamp"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    return sorted(out, key=lambda item: item[0])


def _run_trade(trade: dict[str, Any], candles: list[tuple[datetime, float, float, float, float]], *, runner: bool, activation_r: float, trail_r: float, slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS, commission_round_trip: float = DEFAULT_COMMISSION_ROUND_TRIP, entry_fill_model: str = "ioc_limit", entry_tolerance_ticks_by_root: dict[str, float] | None = None, breakeven_at_1r: bool = False, max_hold_min: int = 480) -> dict[str, Any]:
    inst = str(trade["instrument"]).upper()
    entry, stop, target = (float(trade[key]) for key in ("entry", "stop", "target"))
    start = _parse(trade["entry_ts"])
    decision = next((c for c in candles if _parse(c[0]) == start), None)
    window = [c for c in candles if start < _parse(c[0]) <= start + timedelta(minutes=max_hold_min)]
    row: dict[str, Any] = {"status": "NO_DATA" if decision is None or not window else "OPEN", "result": "NO_DATA" if decision is None or not window else "OPEN", "gross_pnl": 0.0, "net_pnl": 0.0, "entry_price": None, "exit_reason": None}
    if decision is None or not window:
        return row

    broker = PaperBroker(starting_balance=100_000.0, slippage_ticks=slippage_ticks, pessimistic_both_hit=True, breakeven_at_1r=breakeven_at_1r, runner_mode=runner, runner_activation_r=activation_r, runner_trail_r=trail_r, entry_fill_model=entry_fill_model, entry_tolerance_ticks_by_root=entry_tolerance_ticks_by_root or DEFAULT_IOC_TOLERANCE)
    order = BracketOrder(instrument=inst, direction=str(trade["direction"]).upper(), entry=entry, stop=stop, target=target, rr_ratio=0.0, strategy=trade.get("strategy", ""), notes="runner_ab evidence", contracts=1)
    attempted = broker.execute_bracket(order, market_price=decision[4])
    row["entry_price"] = attempted.entry_price
    if attempted.result == "CANCELLED":
        row.update(status="NO_FILL", result="NO_FILL", exit_reason=attempted.exit_reason)
        return row

    for _, open_, high, low, _ in window:
        fill = broker.resolve_position(NextBarOHLC(open=open_, high=high, low=low))
        if fill is not None:
            gross = float(fill.pnl_dollars or 0.0)
            row.update(status="FILLED", result=fill.result, gross_pnl=gross, net_pnl=gross - commission_round_trip, exit_reason=fill.exit_reason)
            return row
    return row


def _profit_factor(values: list[float]) -> float | None:
    winners = sum(value for value in values if value > 0)
    losers = abs(sum(value for value in values if value < 0))
    if losers:
        return round(winners / losers, 4)
    return math.inf if winners else None


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = peak = max_dd = 0.0
    for row in rows:
        if row["result"] not in RESOLVED:
            continue
        equity += row["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row["result"] in RESOLVED]
    net = [row["net_pnl"] for row in resolved]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    return {"attempts": len(rows), "fills": sum(row["status"] == "FILLED" for row in rows), "no_fill": sum(row["status"] == "NO_FILL" for row in rows), "open": sum(row["result"] == "OPEN" for row in rows), "resolved": len(resolved), "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(resolved), 4) if resolved else None, "net_pnl": round(sum(net), 2), "expectancy_per_resolved": round(sum(net) / len(resolved), 2) if resolved else None, "profit_factor": _profit_factor(net), "avg_winner": round(sum(wins) / len(wins), 2) if wins else None, "avg_loser": round(sum(losses) / len(losses), 2) if losses else None, "max_drawdown": _max_drawdown(rows)}


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return {name: _summary(group) for name, group in sorted(groups.items())}


def _evaluate(trades: list[dict[str, Any]], candles_by_inst: dict[str, list], args: argparse.Namespace, runner: bool) -> dict[str, Any]:
    rows = []
    for trade in sorted(trades, key=lambda item: _parse(item["entry_ts"])):
        result = _run_trade(trade, candles_by_inst.get(str(trade["instrument"]).upper(), []), runner=runner, activation_r=args.activation_r, trail_r=args.trail_r, slippage_ticks=args.slippage_ticks, commission_round_trip=args.commission_round_trip, entry_fill_model=args.entry_fill_model, entry_tolerance_ticks_by_root=args.ioc_tolerance, breakeven_at_1r=args.breakeven_at_1r, max_hold_min=args.max_hold_min)
        result.update(instrument=str(trade["instrument"]).upper(), entry_ts=trade["entry_ts"], session=trade.get("session", "unknown"))
        rows.append(result)
    midpoint = len(rows) // 2
    return {"summary": _summary(rows), "sessions": _group_summary(rows, "session"), "halves": {"first": _summary(rows[:midpoint]), "second": _summary(rows[midpoint:])}, "rows": rows}


def _parse_tolerance(values: list[str]) -> dict[str, float]:
    result = dict(DEFAULT_IOC_TOLERANCE)
    for value in values:
        root, ticks = value.split("=", 1)
        result[root.upper()] = float(ticks)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", required=True)
    parser.add_argument("--candles", default="data/replay_polygon")
    parser.add_argument("--activation-r", type=float, default=1.0)
    parser.add_argument("--trail-r", type=float, default=0.5)
    parser.add_argument("--slippage-ticks", type=float, default=DEFAULT_SLIPPAGE_TICKS)
    parser.add_argument("--commission-round-trip", type=float, default=DEFAULT_COMMISSION_ROUND_TRIP)
    parser.add_argument("--entry-fill-model", choices=("ioc_limit", "market"), default="ioc_limit")
    parser.add_argument("--ioc-tolerance", action="append", default=[], metavar="ROOT=TICKS")
    parser.add_argument("--breakeven-at-1r", action="store_true")
    parser.add_argument("--max-hold-min", type=int, default=480)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args()
    args.ioc_tolerance = _parse_tolerance(args.ioc_tolerance)
    trades = json.loads(Path(args.trades).read_text(encoding="utf-8"))
    by_inst: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        if trade.get("instrument") and trade.get("entry_ts") and all(trade.get(key) is not None for key in ("entry", "stop", "target")):
            by_inst[str(trade["instrument"]).upper()].append(trade)
    candles = {inst: _load_candles(args.candles, inst) for inst in by_inst}
    all_trades = [trade for values in by_inst.values() for trade in values]
    report = {"config": {"entry_fill_model": args.entry_fill_model, "ioc_tolerance_ticks": args.ioc_tolerance, "slippage_ticks": args.slippage_ticks, "commission_round_trip": args.commission_round_trip, "pessimistic_both_hit": True, "breakeven_at_1r": args.breakeven_at_1r, "activation_r": args.activation_r, "trail_r": args.trail_r}, "static": _evaluate(all_trades, candles, args, False), "runner": _evaluate(all_trades, candles, args, True)}
    print(json.dumps({mode: report[mode]["summary"] for mode in ("static", "runner")}, indent=2, sort_keys=True))
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_md:
        lines = ["# Runner A/B Evidence", "", f"Config: `{json.dumps(report['config'], sort_keys=True)}`", "", "| mode | net $ | exp/resolved $ | PF | DD $ | win % |", "|---|---:|---:|---:|---:|---:|"]
        for mode in ("static", "runner"):
            s = report[mode]["summary"]
            lines.append(f"| {mode} | {s['net_pnl']:.2f} | {s['expectancy_per_resolved']} | {s['profit_factor']} | {s['max_drawdown']:.2f} | {s['win_rate']} |")
        Path(args.output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
