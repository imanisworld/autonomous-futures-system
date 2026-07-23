"""Honest-fill replay for the resolved MNQ 60M 3-2-2 ground truth.

This module is deliberately research-only.  It consumes already-reconciled
signals and cached 5-minute bars; it does not call runtime strategy, broker, or
configuration code.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
TICK_SIZE = 0.25
POINT_VALUE = 2.0
ROUND_TRIP_COMMISSION = 1.24
IOC_TOLERANCE_TICKS = 32.0


class ReplayInputError(ValueError):
    """Raised when private replay inputs violate the resolved data contract."""


def load_signals(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open(newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "date": date.fromisoformat(raw["date"]),
                    "direction": raw["direction"].upper(),
                    "entry_trigger": float(raw["expected_entry_trigger"]),
                    "entry_price": float(raw["expected_entry_price"]),
                    "stop": float(raw["expected_stop"]),
                    "target": float(raw["expected_target"]),
                    "gap_open": raw["expected_gap_open"].lower() == "true",
                }
            )
    if len({row["date"] for row in rows}) != len(rows):
        raise ReplayInputError("signal dates must be unique")
    if any(row["direction"] not in {"LONG", "SHORT"} for row in rows):
        raise ReplayInputError("direction must be LONG or SHORT")
    return sorted(rows, key=lambda row: row["date"])


def load_bars(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            ts = datetime.fromisoformat(row["ts"])
            if ts.tzinfo is None:
                raise ReplayInputError("bar timestamps must be timezone-aware")
            rows.append(
                {
                    "ts": ts.astimezone(ET),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
    return sorted(rows, key=lambda row: row["ts"])


def _crossed(bar: dict, direction: str, trigger: float) -> bool:
    return bar["high"] > trigger if direction == "LONG" else bar["low"] < trigger


def _gap_open(bar: dict, direction: str, trigger: float) -> bool:
    return bar["open"] > trigger if direction == "LONG" else bar["open"] < trigger


def recover_entry(signal: dict, day_bars: list[dict]) -> dict | None:
    """Recover the first strict trigger crossing in [10:00, 11:00) ET."""
    start = datetime.combine(signal["date"], time(10), ET)
    end = datetime.combine(signal["date"], time(11), ET)
    window = [bar for bar in day_bars if start <= bar["ts"] < end]
    if not window:
        raise ReplayInputError(f"{signal['date']}: missing 10AM five-minute window")

    first = window[0]
    if first["ts"] == start and _gap_open(
        first, signal["direction"], signal["entry_trigger"]
    ):
        return {
            "bar": first,
            "gap_open": True,
            "base_entry_price": first["open"],
            "market_proxy": first["open"],
        }

    for bar in window:
        if _crossed(bar, signal["direction"], signal["entry_trigger"]):
            return {
                "bar": bar,
                "gap_open": False,
                "base_entry_price": signal["entry_trigger"],
                # Production IOC-faithful replay uses the decision bar close as
                # the order-arrival market proxy.
                "market_proxy": bar["close"],
            }
    return None


def _ioc_fill(entry: dict, direction: str, slippage_ticks: float) -> dict:
    slip = slippage_ticks * TICK_SIZE
    if entry["gap_open"]:
        base = entry["base_entry_price"]
        fill = base + slip if direction == "LONG" else base - slip
        return {"filled": True, "base_fill": base, "fill": fill, "limit": None}

    trigger = entry["base_entry_price"]
    market = entry["market_proxy"]
    tolerance = IOC_TOLERANCE_TICKS * TICK_SIZE
    if direction == "LONG":
        limit_price = trigger + tolerance
        if market > limit_price:
            return {
                "filled": False,
                "base_fill": None,
                "fill": None,
                "limit": limit_price,
            }
        return {
            "filled": True,
            "base_fill": market,
            "fill": min(limit_price, market + slip),
            "limit": limit_price,
        }
    limit_price = trigger - tolerance
    if market < limit_price:
        return {
            "filled": False,
            "base_fill": None,
            "fill": None,
            "limit": limit_price,
        }
    return {
        "filled": True,
        "base_fill": market,
        "fill": max(limit_price, market - slip),
        "limit": limit_price,
    }


def _resolve_exit(
    signal: dict,
    day_bars: list[dict],
    entry_bar: dict,
    *,
    include_entry_bar: bool,
    slippage_ticks: float,
) -> dict:
    close_time = datetime.combine(signal["date"], time(16), ET)
    eligible = [
        bar
        for bar in day_bars
        if (bar["ts"] >= entry_bar["ts"] if include_entry_bar else bar["ts"] > entry_bar["ts"])
        and bar["ts"] < close_time
    ]
    if not eligible:
        raise ReplayInputError(f"{signal['date']}: no post-entry bars through 4PM")

    direction = signal["direction"]
    for bar in eligible:
        stop_hit = (
            bar["low"] <= signal["stop"]
            if direction == "LONG"
            else bar["high"] >= signal["stop"]
        )
        target_hit = (
            bar["high"] >= signal["target"]
            if direction == "LONG"
            else bar["low"] <= signal["target"]
        )
        if stop_hit:
            # Includes same-bar stop+target ambiguity: stop wins.
            return {"reason": "STOP", "base_exit": signal["stop"], "bar": bar}
        if target_hit:
            return {"reason": "TARGET", "base_exit": signal["target"], "bar": bar}

    return {
        "reason": "EOD",
        "base_exit": eligible[-1]["close"],
        "bar": eligible[-1],
    }


def replay_signal(
    signal: dict, day_bars: list[dict], *, slippage_ticks: float = 2.0
) -> dict:
    entry = recover_entry(signal, day_bars)
    if entry is None:
        raise ReplayInputError(
            f"{signal['date']}: reconciled signal has no five-minute trigger crossing"
        )
    if entry["gap_open"] != signal["gap_open"]:
        raise ReplayInputError(
            f"{signal['date']}: recovered gap_open disagrees with ground truth"
        )
    fill = _ioc_fill(entry, signal["direction"], slippage_ticks)
    base = {
        "date": signal["date"].isoformat(),
        "direction": signal["direction"],
        "signal": True,
        "trigger": signal["entry_trigger"],
        "stop": signal["stop"],
        "target": signal["target"],
        "entry_bar_ts": entry["bar"]["ts"].isoformat(),
        "entry_market_proxy": entry["market_proxy"],
        "ioc_limit": fill["limit"],
        "gap_open": entry["gap_open"],
        "filled": fill["filled"],
        "slippage_ticks": slippage_ticks,
    }
    if not fill["filled"]:
        return {
            **base,
            "result": "CANCELLED",
            "exit_reason": "ENTRY_NOT_FILLED",
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    stop_wrong_side = (
        signal["stop"] >= fill["fill"]
        if signal["direction"] == "LONG"
        else signal["stop"] <= fill["fill"]
    )
    if stop_wrong_side:
        # Fail closed: the fixed structural stop cannot be placed as a
        # protective stop after this fill. No replay position is opened.
        return {
            **base,
            "filled": False,
            "ioc_parent_filled": True,
            "result": "CANCELLED",
            "exit_reason": "POST_FILL_INVALID_STOP",
            "base_entry_price": fill["base_fill"],
            "fill_entry_price": fill["fill"],
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    target_marketable_at_arrival = (
        fill["base_fill"] >= signal["target"]
        if signal["direction"] == "LONG"
        else fill["base_fill"] <= signal["target"]
    )
    if target_marketable_at_arrival:
        # The target child is already marketable when the IOC parent fills.
        # It exits at the arrival market rather than granting a fantasy fill
        # back at a now-worse target level.
        exit_info = {
            "reason": "TARGET_AT_ENTRY",
            "base_exit": fill["base_fill"],
            "bar": entry["bar"],
        }
    else:
        # Non-gap IOC orders arrive at the crossing bar close, so the crossing
        # bar's earlier high/low cannot causally stop or target the new
        # position. A gap-open entry fills at the bar open and therefore
        # includes that bar.
        exit_info = _resolve_exit(
            signal,
            day_bars,
            entry["bar"],
            include_entry_bar=entry["gap_open"],
            slippage_ticks=slippage_ticks,
        )
    slip = slippage_ticks * TICK_SIZE
    base_exit = exit_info["base_exit"]
    actual_exit = (
        base_exit - slip if signal["direction"] == "LONG" else base_exit + slip
    )
    signed = 1.0 if signal["direction"] == "LONG" else -1.0
    gross_pnl = signed * (base_exit - fill["base_fill"]) * POINT_VALUE
    entry_slippage_cost = (
        signed * (fill["fill"] - fill["base_fill"]) * POINT_VALUE
    )
    exit_slippage_cost = (
        signed * (base_exit - actual_exit) * POINT_VALUE
    )
    slippage_cost = entry_slippage_cost + exit_slippage_cost
    total_costs = slippage_cost + ROUND_TRIP_COMMISSION
    net_pnl = gross_pnl - total_costs
    return {
        **base,
        "result": "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN",
        "base_entry_price": fill["base_fill"],
        "fill_entry_price": fill["fill"],
        "base_exit_price": base_exit,
        "fill_exit_price": actual_exit,
        "exit_bar_ts": exit_info["bar"]["ts"].isoformat(),
        "exit_reason": exit_info["reason"],
        "gross_pnl": gross_pnl,
        "slippage_cost": slippage_cost,
        "commission": ROUND_TRIP_COMMISSION,
        "total_costs": total_costs,
        "net_pnl": net_pnl,
    }


def _metrics(rows: list[dict]) -> dict:
    signals = len(rows)
    fills = [row for row in rows if row["filled"]]
    wins = [row for row in fills if row["net_pnl"] > 0]
    losses = [row for row in fills if row["net_pnl"] < 0]
    gross_profit = sum(row["net_pnl"] for row in wins)
    gross_loss = -sum(row["net_pnl"] for row in losses)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in rows:
        equity += row["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    net = sum(row["net_pnl"] for row in rows)
    return {
        "n": signals,
        "fills": len(fills),
        "cancellations": signals - len(fills),
        "fill_rate": len(fills) / signals if signals else None,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(fills) if fills else None,
        "gross_pnl": sum(row["gross_pnl"] for row in rows),
        "slippage_cost": sum(row["slippage_cost"] for row in rows),
        "commission_cost": sum(row["commission"] for row in rows),
        "total_costs": sum(row["total_costs"] for row in rows),
        "net_pnl": net,
        "expectancy_per_signal": net / signals if signals else None,
        "expectancy_per_fill": net / len(fills) if fills else None,
        # JSON-safe null denotes an undefined/infinite PF when there are no
        # losing fills in the requested slice.
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "avg_win": (
            sum(row["net_pnl"] for row in wins) / len(wins) if wins else None
        ),
        "avg_loss": (
            sum(row["net_pnl"] for row in losses) / len(losses) if losses else None
        ),
        "max_drawdown": max_dd,
        "exit_reasons": {
            reason: sum(row.get("exit_reason") == reason for row in rows)
            for reason in (
                "TARGET",
                "TARGET_AT_ENTRY",
                "STOP",
                "EOD",
                "ENTRY_NOT_FILLED",
                "POST_FILL_INVALID_STOP",
            )
        },
    }


def run_replay(
    signals: list[dict],
    bars_5m: list[dict],
    *,
    study_start: date,
    study_end: date,
    slippage_ticks: float = 2.0,
) -> dict:
    by_date: dict[date, list[dict]] = defaultdict(list)
    for bar in bars_5m:
        by_date[bar["ts"].date()].append(bar)
    rows = [
        replay_signal(signal, by_date[signal["date"]], slippage_ticks=slippage_ticks)
        for signal in signals
    ]
    midpoint = study_start + (study_end - study_start) / 2
    h1 = [row for row in rows if date.fromisoformat(row["date"]) <= midpoint]
    h2 = [row for row in rows if date.fromisoformat(row["date"]) > midpoint]
    return {
        "schema_version": 1,
        "strategy": "MNQ_60M_322_FIRST_LIVE",
        "model": {
            "tick_size": TICK_SIZE,
            "point_value": POINT_VALUE,
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "ioc_market_proxy": "first_crossing_5m_bar_close",
            "entry_slippage_ticks": slippage_ticks,
            "exit_slippage_ticks": slippage_ticks,
            "round_trip_commission": ROUND_TRIP_COMMISSION,
            "same_bar_ambiguity": "STOP_FIRST",
            "non_gap_entry_bar_exit_eligibility": "NEXT_5M_BAR",
            "gap_open_entry_bar_exit_eligibility": "ENTRY_5M_BAR",
            "marketable_target_at_arrival": "IMMEDIATE_AT_MARKET_PROXY",
            "post_fill_wrong_side_stop": "FAIL_CLOSED",
            "unresolved_exit": "LAST_5M_CLOSE_BEFORE_4PM_ET",
        },
        "study_range": {
            "start": study_start.isoformat(),
            "end": study_end.isoformat(),
            "midpoint": midpoint.isoformat(),
            "h1": f"{study_start.isoformat()} through {midpoint.isoformat()}",
            "h2": f"after {midpoint.isoformat()} through {study_end.isoformat()}",
        },
        "overall": _metrics(rows),
        "halves": {"H1": _metrics(h1), "H2": _metrics(h2)},
        "directions": {
            direction: _metrics([row for row in rows if row["direction"] == direction])
            for direction in ("LONG", "SHORT")
        },
        "trades": rows,
    }


def build_sensitivity(
    signals: list[dict],
    bars_5m: list[dict],
    *,
    study_start: date,
    study_end: date,
) -> dict:
    return {
        str(ticks): run_replay(
            signals,
            bars_5m,
            study_start=study_start,
            study_end=study_end,
            slippage_ticks=float(ticks),
        )
        for ticks in (1, 2, 3, 4)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", required=True)
    parser.add_argument("--bars-5m", required=True)
    parser.add_argument("--study-start", type=date.fromisoformat, required=True)
    parser.add_argument("--study-end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    signals = load_signals(args.signals)
    bars = load_bars(args.bars_5m)
    sensitivity = build_sensitivity(
        signals,
        bars,
        study_start=args.study_start,
        study_end=args.study_end,
    )
    report = {
        "schema_version": 1,
        "base_case_slippage_ticks": 2,
        "base_case": sensitivity["2"],
        "slippage_sensitivity": {
            key: {
                "overall": value["overall"],
                "halves": value["halves"],
                "directions": value["directions"],
            }
            for key, value in sensitivity.items()
        },
    }
    Path(args.output).write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps(report["base_case"]["overall"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
