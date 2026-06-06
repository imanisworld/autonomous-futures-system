#!/usr/bin/env python3
"""Standalone historical backtest for the experimental range-fade strategy."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from replay.candle_loader import ReplayCandleLoader
from strategy.range_fade import RangeBar, RangeFadeConfig, RangeTracker


@dataclass
class TradeResult:
    date: str
    instrument: str
    direction: str
    entry: float
    stop: float
    target: float
    support: float
    resistance: float
    result: str
    pnl_dollars: float


def run_file(
    path: Path,
    config: RangeFadeConfig,
    max_trades: int,
    max_losses: int,
    session: str = "new_york",
) -> list[TradeResult]:
    candles = [
        candle
        for candle in ReplayCandleLoader().load_jsonl(path)
        if candle.session == session
    ]
    if not candles:
        return []
    tracker = RangeTracker(config)
    broker = PaperBroker(starting_balance=1500, slippage_ticks=1, pessimistic_both_hit=True)
    results: list[TradeResult] = []
    pending = None

    for candle in candles:
        if pending is not None:
            fill = broker.resolve_position(NextBarOHLC(high=candle.high, low=candle.low))
            if fill is not None:
                results.append(
                    TradeResult(
                        date=pending.date,
                        instrument=pending.instrument,
                        direction=pending.direction,
                        entry=fill.entry_price,
                        stop=pending.stop,
                        target=pending.target,
                        support=pending.support,
                        resistance=pending.resistance,
                        result=fill.result,
                        pnl_dollars=round(float(fill.pnl_dollars or 0), 2),
                    )
                )
                pending = None

        losses = sum(result.result == "LOSS" for result in results)
        may_enter = pending is None and len(results) < max_trades and losses < max_losses
        signal = tracker.update(
            RangeBar(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                avg_volume=candle.avg_volume,
                market_condition=candle.market_condition,
                trend_direction=candle.trend_direction,
                trend_strength=candle.trend_strength,
            ),
            allow_signal=may_enter,
        )
        if signal is None or not may_enter:
            continue

        fill = broker.execute_bracket(
            BracketOrder(
                instrument=candle.instrument,
                direction=signal.direction,
                entry=signal.entry,
                stop=signal.stop,
                target=signal.target,
                rr_ratio=abs(signal.target - signal.entry) / max(abs(signal.entry - signal.stop), 0.01),
                strategy="range_fade",
                contracts=1,
            )
        )
        pending = TradeResult(
                date=candle.timestamp[:10],
                instrument=candle.instrument,
                direction=signal.direction,
                entry=fill.entry_price,
                stop=signal.stop,
                target=signal.target,
                support=signal.support,
                resistance=signal.resistance,
                result="OPEN",
                pnl_dollars=0.0,
        )
    return results


def summarize(trades: list[TradeResult], files: int) -> dict:
    wins = sum(trade.result == "WIN" for trade in trades)
    losses = sum(trade.result == "LOSS" for trade in trades)
    breakevens = sum(trade.result == "BREAKEVEN" for trade in trades)
    pnl = round(sum(trade.pnl_dollars for trade in trades), 2)
    resolved = wins + losses
    gross_wins = sum(max(trade.pnl_dollars, 0) for trade in trades)
    gross_losses = abs(sum(min(trade.pnl_dollars, 0) for trade in trades))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.pnl_dollars
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "days": files,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": round(wins / resolved, 4) if resolved else 0,
        "pnl_dollars": pnl,
        "expectancy": round(pnl / resolved, 2) if resolved else 0,
        "profit_factor": round(gross_wins / gross_losses, 3) if gross_losses else None,
        "average_win": round(gross_wins / wins, 2) if wins else 0,
        "average_loss": round(gross_losses / losses, 2) if losses else 0,
        "max_drawdown": round(max_drawdown, 2),
        "trades_per_day": round(len(trades) / files, 3) if files else 0,
    }


def split_for(day: str) -> str:
    value = date.fromisoformat(day)
    if value <= date(2026, 4, 17):
        return "development"
    if value <= date(2026, 5, 15):
        return "validation"
    return "holdout"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-trades", type=int, default=1)
    parser.add_argument("--max-losses", type=int, default=1)
    parser.add_argument("--confirmation-bars", type=int, default=6)
    parser.add_argument("--entry-zone", type=float, default=0.20)
    parser.add_argument("--session", default="new_york")
    args = parser.parse_args()

    files = sorted(Path(args.candles).glob("*.jsonl"))
    cfg = RangeFadeConfig(
        confirmation_bars=args.confirmation_bars,
        entry_zone_percent=args.entry_zone,
    )
    trades = [
        trade
        for path in files
        for trade in run_file(path, cfg, args.max_trades, args.max_losses, args.session)
    ]
    report = {
        "config": asdict(cfg),
        "overall": summarize(trades, len(files)),
        "splits": {
            name: summarize(
                [trade for trade in trades if split_for(trade.date) == name],
                len({path.stem for path in files if split_for(_date_from_stem(path.stem)) == name}),
            )
            for name in ("development", "validation", "holdout")
        },
        "trades": [asdict(trade) for trade in trades],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "trades"}, indent=2))
    return 0


def _date_from_stem(stem: str) -> str:
    return stem[-10:]


if __name__ == "__main__":
    raise SystemExit(main())
