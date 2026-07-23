"""Honest-fill replay for resolved-rule MNQ 12HR Miyagi setups.

Research only.  This module deliberately has no imports from strategy runtime,
configuration, execution routing, or deployment code.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from research.reconcile_12hr_miyagi import load_bars


MNQ_TICK_SIZE = 0.25
MNQ_TICK_VALUE = 0.50
MNQ_POINT_VALUE = 2.0
MNQ_IOC_TOLERANCE_TICKS = 32
MNQ_ROUND_TRIP_COMMISSION = 1.24


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _first_midpoint_touch(signal: dict, bars_5m: list[dict]) -> dict | None:
    open_ts = _dt(signal["entry_window_open"])
    close_ts = datetime.combine(open_ts.date(), time(16), open_ts.tzinfo)
    trigger = float(signal["entry_trigger"])
    candidates = sorted(
        (
            bar
            for bar in bars_5m
            if open_ts <= bar["ts"] < close_ts
            and float(bar["low"]) <= trigger <= float(bar["high"])
        ),
        key=lambda bar: bar["ts"],
    )
    return candidates[0] if candidates else None


def _last_completed_hour(
    bars_60m: list[dict], arrival_ts: datetime
) -> dict | None:
    eligible = [
        bar
        for bar in bars_60m
        if bar["ts"] + timedelta(hours=1) <= arrival_ts
    ]
    return max(eligible, key=lambda bar: bar["ts"]) if eligible else None


def _ioc_fill(
    *, direction: str, trigger: float, market: float, slippage_ticks: int
) -> tuple[float, float] | None:
    """Return (unslipped market fill, slipped/capped fill), or no fill.

    This mirrors the repository's live-faithful MNQ Limit-IOC contract:
    trigger +/- 32 ticks is the adverse cap; the touch bar close is the
    one-shot arrival proxy; favorable price improvement is retained.
    """
    tolerance = MNQ_IOC_TOLERANCE_TICKS * MNQ_TICK_SIZE
    slip = slippage_ticks * MNQ_TICK_SIZE
    if direction == "LONG":
        limit = trigger + tolerance
        if market > limit:
            return None
        return market, min(market + slip, limit)
    limit = trigger - tolerance
    if market < limit:
        return None
    return market, max(market - slip, limit)


def replay_signal(
    signal_date: date,
    signal: dict,
    bars_5m: list[dict],
    bars_60m: list[dict],
    *,
    slippage_ticks: int = 2,
) -> dict:
    direction = signal["direction"]
    trigger = float(signal["entry_trigger"])
    target = float(signal["target"])
    touch = _first_midpoint_touch(signal, bars_5m)
    base = {
        "date": signal_date.isoformat(),
        "direction": direction,
        "signal": True,
        "entry_trigger": trigger,
        "target": target,
        "touch": touch is not None,
        "filled": False,
        "gross_pnl": 0.0,
        "slippage_cost": 0.0,
        "commission": 0.0,
        "total_costs": 0.0,
        "net_pnl": 0.0,
    }
    if touch is None:
        return {**base, "outcome": "NO_TOUCH"}

    # Polygon 5m timestamps are bar starts.  An IOC based on the completed
    # crossing bar arrives five minutes later.
    arrival_ts = touch["ts"] + timedelta(minutes=5)
    market = float(touch["close"])
    fill = _ioc_fill(
        direction=direction,
        trigger=trigger,
        market=market,
        slippage_ticks=slippage_ticks,
    )
    touch_fields = {
        "touch_bar_ts": touch["ts"].isoformat(),
        "arrival_ts": arrival_ts.isoformat(),
        "arrival_market_price": market,
    }
    if fill is None:
        return {
            **base,
            **touch_fields,
            "outcome": "IOC_CANCELLED",
            "no_fill_reason": "NO_FILL_PRICE_MOVED_AWAY",
        }

    raw_entry, entry_fill = fill
    stop_bar = _last_completed_hour(bars_60m, arrival_ts)
    if stop_bar is None:
        return {
            **base,
            **touch_fields,
            "outcome": "DATA_ERROR_NO_COMPLETED_60M",
        }
    stop = float(stop_bar["low"] if direction == "LONG" else stop_bar["high"])
    if (direction == "LONG" and stop >= entry_fill) or (
        direction == "SHORT" and stop <= entry_fill
    ):
        return {
            **base,
            **touch_fields,
            "outcome": "INVALID_NON_PROTECTIVE_STOP",
            "entry_fill": entry_fill,
            "stop": stop,
            "stop_bar_ts": stop_bar["ts"].isoformat(),
        }

    session_close = datetime.combine(signal_date, time(16), arrival_ts.tzinfo)
    path = sorted(
        (
            bar
            for bar in bars_5m
            if arrival_ts <= bar["ts"] < session_close
        ),
        key=lambda bar: bar["ts"],
    )
    if not path:
        return {
            **base,
            **touch_fields,
            "outcome": "DATA_ERROR_NO_EXIT_PATH",
            "entry_fill": entry_fill,
            "stop": stop,
            "stop_bar_ts": stop_bar["ts"].isoformat(),
        }

    raw_exit = float(path[-1]["close"])
    exit_ts = path[-1]["ts"] + timedelta(minutes=5)
    outcome = "EOD"
    for bar in path:
        stop_hit = (
            float(bar["low"]) <= stop
            if direction == "LONG"
            else float(bar["high"]) >= stop
        )
        target_hit = (
            float(bar["high"]) >= target
            if direction == "LONG"
            else float(bar["low"]) <= target
        )
        # Pessimistic ordering: a stop always wins a same-bar ambiguity.
        if stop_hit:
            raw_exit = stop
            exit_ts = bar["ts"]
            outcome = "STOP"
            break
        if target_hit:
            raw_exit = target
            exit_ts = bar["ts"]
            outcome = "TARGET"
            break

    slip = slippage_ticks * MNQ_TICK_SIZE
    exit_fill = raw_exit - slip if direction == "LONG" else raw_exit + slip
    sign = 1.0 if direction == "LONG" else -1.0
    gross = sign * (raw_exit - raw_entry) * MNQ_POINT_VALUE
    net_before_commission = sign * (exit_fill - entry_fill) * MNQ_POINT_VALUE
    slippage_cost = gross - net_before_commission
    net = net_before_commission - MNQ_ROUND_TRIP_COMMISSION
    return {
        **base,
        **touch_fields,
        "filled": True,
        "outcome": outcome,
        "raw_entry": raw_entry,
        "entry_fill": entry_fill,
        "stop": stop,
        "stop_bar_ts": stop_bar["ts"].isoformat(),
        "raw_exit": raw_exit,
        "exit_fill": exit_fill,
        "exit_ts": exit_ts.isoformat(),
        "gross_pnl": round(gross, 8),
        "slippage_cost": round(slippage_cost, 8),
        "commission": MNQ_ROUND_TRIP_COMMISSION,
        "total_costs": round(slippage_cost + MNQ_ROUND_TRIP_COMMISSION, 8),
        "net_pnl": round(net, 8),
    }


def summarize(rows: list[dict]) -> dict:
    fills = [row for row in rows if row["filled"]]
    touches = [row for row in rows if row["touch"]]
    pnls = [float(row["net_pnl"]) for row in fills]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    equity = peak = max_drawdown = 0.0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    loss_total = abs(sum(losses))
    return {
        "n": len(rows),
        "touch_attempts": len(touches),
        "fills": len(fills),
        "no_touch": sum(row["outcome"] == "NO_TOUCH" for row in rows),
        "ioc_cancelled": sum(row["outcome"] == "IOC_CANCELLED" for row in rows),
        "invalid_non_protective_stop": sum(
            row["outcome"] == "INVALID_NON_PROTECTIVE_STOP" for row in rows
        ),
        "fill_rate": round(len(fills) / len(rows), 6) if rows else None,
        "fill_rate_per_touch": (
            round(len(fills) / len(touches), 6) if touches else None
        ),
        "win_rate": round(len(wins) / len(fills), 6) if fills else None,
        "gross_pnl": round(sum(float(row["gross_pnl"]) for row in fills), 2),
        "total_costs": round(sum(float(row["total_costs"]) for row in fills), 2),
        "net_pnl": round(sum(pnls), 2),
        "expectancy_per_signal": (
            round(sum(pnls) / len(rows), 2) if rows else None
        ),
        "expectancy_per_fill": round(mean(pnls), 2) if fills else None,
        # Undefined, rather than infinity, when the slice has no losses.
        "profit_factor": round(sum(wins) / loss_total, 6) if loss_total else None,
        "avg_win": round(mean(wins), 2) if wins else None,
        "avg_loss": round(mean(losses), 2) if losses else None,
        "max_drawdown": round(max_drawdown, 2),
        "outcomes": {
            name: sum(row["outcome"] == name for row in rows)
            for name in sorted({row["outcome"] for row in rows})
        },
    }


def _split_rows(rows: list[dict]) -> tuple[date, list[dict], list[dict]]:
    dates = [date.fromisoformat(row["date"]) for row in rows]
    midpoint = min(dates) + (max(dates) - min(dates)) / 2
    midpoint_date = midpoint.date() if isinstance(midpoint, datetime) else midpoint
    h1 = [row for row in rows if date.fromisoformat(row["date"]) <= midpoint_date]
    h2 = [row for row in rows if date.fromisoformat(row["date"]) > midpoint_date]
    return midpoint_date, h1, h2


def run_replay(
    detector_signals: dict[str, dict],
    bars_5m: list[dict],
    bars_60m: list[dict],
) -> dict:
    sensitivity = {}
    for ticks in (1, 2, 3, 4):
        rows = [
            replay_signal(
                date.fromisoformat(day),
                signal,
                bars_5m,
                bars_60m,
                slippage_ticks=ticks,
            )
            for day, signal in sorted(detector_signals.items())
        ]
        midpoint, h1, h2 = _split_rows(rows)
        sensitivity[str(ticks)] = {
            "slippage_ticks_each_side": ticks,
            "midpoint_date": midpoint.isoformat(),
            "overall": summarize(rows),
            "halves": {"H1": summarize(h1), "H2": summarize(h2)},
            "direction": {
                direction: summarize(
                    [row for row in rows if row["direction"] == direction]
                )
                for direction in ("LONG", "SHORT")
            },
            "trades": rows,
        }
    return {
        "schema_version": 1,
        "strategy": "12HR_Miyagi",
        "instrument": "MNQ",
        "primary_slippage_ticks": 2,
        "assumptions": {
            "entry": (
                "first 5m bar at/after 09:30 whose traded range contains the "
                "midpoint; IOC arrives at that completed bar's close; this is "
                "the conservative causal contract available at 5m granularity"
            ),
            "ioc": (
                "one-shot Limit-IOC, MNQ 32-tick adverse tolerance, arrival "
                "bar close proxy, favorable improvement retained, no persistence"
            ),
            "stop": (
                "last 60m candle completed by IOC arrival; low for LONG/high "
                "for SHORT; fixed forever; fail closed if the actual IOC fill "
                "makes that stop non-protective"
            ),
            "exit": (
                "T1, otherwise fixed stop, otherwise forced exit at the 15:55 "
                "bar close (16:00 ET); the EOD fallback is a replay assumption "
                "because unresolved carry is not pinned in the detector spec"
            ),
            "same_bar": (
                "the completed touch bar is never reused for bracket resolution; "
                "on any subsequent bar, stop wins if stop and target both touch"
            ),
            "costs": "$1.24 round trip plus adverse slippage on entry and exit",
            "walk_forward": (
                "exact calendar midpoint between first and last resolved setup "
                "date; midpoint date assigned to H1"
            ),
        },
        "sensitivity": sensitivity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconciliation-report", required=True)
    parser.add_argument("--bars-5m", required=True)
    parser.add_argument("--bars-60m", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    reconciliation = json.loads(Path(args.reconciliation_report).read_text())
    report = run_replay(
        reconciliation["detector_signals"],
        load_bars(args.bars_5m),
        load_bars(args.bars_60m),
    )
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["sensitivity"]["2"]["overall"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
