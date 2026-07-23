"""Research-only honest-fill replay for the reconciled 4HR Re-Trigger detector.

The executable detector decides *whether* a setup exists.  This module models
the later entry and bracket without importing the live strategy, broker, risk,
configuration, or deployment paths.

The IOC model intentionally mirrors ``PaperBroker(entry_fill_model="ioc_limit")``:
the first 5-minute bar that crosses the trigger is the decision bar; its close
is the observable market proxy when the order arrives; MNQ accepts that IOC
only while the close is within 32 ticks (8 points) of the trigger.  A fill is at
the close plus/minus adverse slippage, capped at the IOC limit.  The completed
decision bar is never reused to resolve the newly opened bracket.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from research.reconcile_4hr_retrigger import load_bars_jsonl


TICK_SIZE = 0.25
POINT_VALUE = 2.0
IOC_TOLERANCE_TICKS = 32.0
COMMISSION_ROUND_TRIP = 1.24


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _last_completed_1h(
    bars_1h: Iterable[dict[str, Any]], entry_bar_ts: datetime
) -> dict[str, Any] | None:
    """Return the latest top-of-hour bar completed when the entry bar begins.

    Five-minute OHLC identifies the first crossing *bar*, not the exact
    intrabar tick.  Using its open timestamp is conservative and matches the
    rules examples: a crossing in the 9:55 bar still uses the 8:00-9:00 hour;
    a crossing in the 10:00 bar uses the completed 9:00-10:00 hour.
    """
    eligible = [
        bar
        for bar in bars_1h
        if bar["ts"] + timedelta(hours=1) <= entry_bar_ts
    ]
    return max(eligible, key=lambda bar: bar["ts"]) if eligible else None


def replay_one(
    *,
    eval_date: date,
    signal: dict[str, Any],
    bars_5m: list[dict[str, Any]],
    bars_1h: list[dict[str, Any]],
    slippage_ticks: float,
    entry_model: str = "ioc_limit",
) -> dict[str, Any]:
    """Replay one resolved detector setup with causal 5-minute information."""
    if entry_model not in {"ioc_limit", "market"}:
        raise ValueError(f"unsupported entry_model: {entry_model}")
    direction = signal["direction"]
    trigger = float(signal["entry_trigger"])
    target = float(signal["target"])
    window_open = datetime.fromisoformat(signal["entry_window_open"])
    window_close = datetime.fromisoformat(signal["entry_window_close"])
    session_end = window_open.replace(hour=16, minute=0)
    session = sorted(
        (
            bar
            for bar in bars_5m
            if window_open <= bar["ts"] < session_end
        ),
        key=lambda bar: bar["ts"],
    )
    row: dict[str, Any] = {
        "date": eval_date.isoformat(),
        "direction": direction,
        "signal": True,
        "entry_trigger": trigger,
        "target": target,
        "slippage_ticks": slippage_ticks,
        "entry_model": entry_model,
        "filled": False,
        "status": None,
        "gross_pnl": 0.0,
        "slippage_cost": 0.0,
        "commission": 0.0,
        "total_costs": 0.0,
        "net_pnl": 0.0,
    }

    crossing_index = next(
        (
            index
            for index, bar in enumerate(session)
            if bar["ts"] < window_close
            and (
                bar["high"] >= trigger
                if direction == "LONG"
                else bar["low"] <= trigger
            )
        ),
        None,
    )
    if crossing_index is None:
        row["status"] = "NO_TRIGGER"
        return row

    crossing = session[crossing_index]
    market = float(crossing["close"])
    tolerance = IOC_TOLERANCE_TICKS * TICK_SIZE
    if entry_model == "ioc_limit" and (
        direction == "LONG"
        and market > trigger + tolerance
        or direction == "SHORT"
        and market < trigger - tolerance
    ):
        row.update(
            status="IOC_CANCELLED",
            entry_bar_ts=crossing["ts"].isoformat(),
            decision_market_price=market,
        )
        return row

    slip = slippage_ticks * TICK_SIZE
    if direction == "LONG":
        entry_fill = (
            min(trigger + tolerance, market + slip)
            if entry_model == "ioc_limit"
            else market + slip
        )
    else:
        entry_fill = (
            max(trigger - tolerance, market - slip)
            if entry_model == "ioc_limit"
            else market - slip
        )

    stop_bar = _last_completed_1h(bars_1h, crossing["ts"])
    if stop_bar is None:
        row.update(
            status="STOP_REFERENCE_UNAVAILABLE",
            entry_bar_ts=crossing["ts"].isoformat(),
            decision_market_price=market,
        )
        return row
    stop = float(stop_bar["low"] if direction == "LONG" else stop_bar["high"])
    bracket_valid = (
        stop < entry_fill < target
        if direction == "LONG"
        else target < entry_fill < stop
    )
    if not bracket_valid:
        non_protective_stop = (
            stop >= entry_fill if direction == "LONG" else stop <= entry_fill
        )
        target_already_passed = (
            entry_fill >= target if direction == "LONG" else entry_fill <= target
        )
        if non_protective_stop:
            invalid_bracket_reason = "NON_PROTECTIVE_STOP"
        elif target_already_passed:
            invalid_bracket_reason = "TARGET_ALREADY_PASSED"
        else:  # Defensive: the strict bracket predicate should exhaust both cases.
            invalid_bracket_reason = "INVALID_ORDERING"
        row.update(
            status="INVALID_BRACKET_AT_FILL",
            invalid_bracket_reason=invalid_bracket_reason,
            entry_bar_ts=crossing["ts"].isoformat(),
            decision_market_price=market,
            modeled_entry_fill=entry_fill,
            stop=stop,
            stop_bar_ts=stop_bar["ts"].isoformat(),
        )
        return row

    row.update(
        filled=True,
        status="OPEN",
        entry_bar_ts=crossing["ts"].isoformat(),
        decision_market_price=market,
        entry_fill=entry_fill,
        stop=stop,
        stop_bar_ts=stop_bar["ts"].isoformat(),
    )

    # Causality: the decision bar is complete before the IOC arrives.  Begin
    # bracket evaluation on the next bar, even if its earlier range touched an
    # ordered level.
    post_fill = session[crossing_index + 1 :]
    raw_exit: float | None = None
    exit_fill: float | None = None
    exit_reason: str | None = None
    exit_ts: datetime | None = None
    for bar in post_fill:
        stop_hit = (
            bar["low"] <= stop
            if direction == "LONG"
            else bar["high"] >= stop
        )
        target_hit = (
            bar["high"] >= target
            if direction == "LONG"
            else bar["low"] <= target
        )
        # Pessimistic ordering when a subsequent 5-minute bar contains both.
        if stop_hit:
            raw_exit = stop
            exit_fill = stop - slip if direction == "LONG" else stop + slip
            exit_reason = "STOP"
            exit_ts = bar["ts"]
            break
        if target_hit:
            raw_exit = target
            # Requirement is adverse slippage on *every* exit, including target.
            exit_fill = target - slip if direction == "LONG" else target + slip
            exit_reason = "TARGET"
            exit_ts = bar["ts"]
            break

    if raw_exit is None:
        if not post_fill:
            row.update(filled=False, status="EOD_BAR_UNAVAILABLE")
            return row
        eod = post_fill[-1]
        raw_exit = float(eod["close"])
        exit_fill = raw_exit - slip if direction == "LONG" else raw_exit + slip
        exit_reason = "EOD"
        exit_ts = eod["ts"]

    sign = 1.0 if direction == "LONG" else -1.0
    gross = sign * (raw_exit - market) * POINT_VALUE
    net_before_commission = sign * (exit_fill - entry_fill) * POINT_VALUE
    slippage_cost = gross - net_before_commission
    net = net_before_commission - COMMISSION_ROUND_TRIP
    row.update(
        status="FILLED",
        exit_reason=exit_reason,
        exit_bar_ts=exit_ts.isoformat() if exit_ts else None,
        raw_exit_price=raw_exit,
        exit_fill=exit_fill,
        gross_pnl=round(gross, 10),
        slippage_cost=round(slippage_cost, 10),
        commission=COMMISSION_ROUND_TRIP,
        total_costs=round(slippage_cost + COMMISSION_ROUND_TRIP, 10),
        net_pnl=round(net, 10),
        profitable=net > 0,
    )
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = [row for row in rows if row["filled"]]
    wins = [row for row in fills if row["net_pnl"] > 0]
    losses = [row for row in fills if row["net_pnl"] < 0]
    gross = sum(row["gross_pnl"] for row in fills)
    costs = sum(row["total_costs"] for row in fills)
    net = sum(row["net_pnl"] for row in fills)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(fills, key=lambda item: item["date"]):
        equity += row["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    positive = sum(row["net_pnl"] for row in wins)
    negative = abs(sum(row["net_pnl"] for row in losses))
    return {
        "n": len(rows),
        "fills": len(fills),
        "no_fills": len(rows) - len(fills),
        "no_fill_reasons": dict(
            sorted(Counter(row["status"] for row in rows if not row["filled"]).items())
        ),
        "fill_rate": len(fills) / len(rows) if rows else None,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(fills) if fills else None,
        "exit_reasons": dict(
            sorted(Counter(row["exit_reason"] for row in fills).items())
        ),
        "gross_pnl": round(gross, 2),
        "total_costs": round(costs, 2),
        "net_pnl": round(net, 2),
        "expectancy_per_signal": round(net / len(rows), 4) if rows else None,
        "expectancy_per_fill": round(net / len(fills), 4) if fills else None,
        "profit_factor": (
            round(positive / negative, 4)
            if negative
            else (math.inf if positive else None)
        ),
        "avg_win": round(positive / len(wins), 4) if wins else None,
        "avg_loss": (
            round(sum(row["net_pnl"] for row in losses) / len(losses), 4)
            if losses
            else None
        ),
        "max_drawdown": round(max_dd, 2),
    }


def build_report(
    *,
    reconciliation: dict[str, Any],
    bars_5m: list[dict[str, Any]],
    bars_1h: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_map = reconciliation["detector_signals"]
    start = date.fromisoformat(reconciliation["study_range"]["start"])
    end = date.fromisoformat(reconciliation["study_range"]["end"])
    midpoint = datetime.combine(start, datetime.min.time()) + (
        datetime.combine(end, datetime.min.time())
        - datetime.combine(start, datetime.min.time())
    ) / 2

    sensitivity: dict[str, Any] = {}
    for slippage_ticks in (1.0, 2.0, 3.0, 4.0):
        rows = [
            replay_one(
                eval_date=date.fromisoformat(day),
                signal=signal,
                bars_5m=bars_5m,
                bars_1h=bars_1h,
                slippage_ticks=slippage_ticks,
            )
            for day, signal in sorted(signal_map.items())
        ]
        h1 = [row for row in rows if date.fromisoformat(row["date"]) < midpoint.date()]
        h2 = [row for row in rows if date.fromisoformat(row["date"]) >= midpoint.date()]
        sensitivity[str(int(slippage_ticks))] = {
            "overall": summarize(rows),
            "halves": {"H1": summarize(h1), "H2": summarize(h2)},
            "direction": {
                side: summarize([row for row in rows if row["direction"] == side])
                for side in ("LONG", "SHORT")
            },
            "trades": rows,
        }

    return {
        "schema_version": 1,
        "research_only": True,
        "strategy": "4HR Re-Trigger",
        "instrument": "MNQ",
        "study_range": reconciliation["study_range"],
        "chronological_midpoint": midpoint.isoformat(),
        "baseline_slippage_ticks": 2,
        "assumptions": {
            "tick_size": TICK_SIZE,
            "point_value": POINT_VALUE,
            "contracts": 1,
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "ioc_market_proxy": "first trigger-crossing 5m bar close",
            "ioc_fill": "market close +/- adverse slippage, capped at trigger +/- 32 ticks",
            "ioc_cancel": "crossing-bar close beyond the 32-tick cap; no retry/chase",
            "entry_window": "[09:30, 11:00) ET",
            "stop": "fixed low/high of last top-of-hour 1H bar completed before crossing bar opens",
            "post_fill_causality": "bracket evaluation begins on next 5m bar",
            "same_bar_ambiguity": "stop first",
            "exit_slippage": "adverse on stop, target, and EOD exits",
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "unresolved_exit": "15:55 ET bar close with adverse slippage",
            "half_split": "calendar midpoint of reconciled study range; midpoint date belongs to H2",
        },
        "slippage_sensitivity": sensitivity,
        "baseline": sensitivity["2"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconciliation", required=True)
    parser.add_argument("--bars-5m", required=True)
    parser.add_argument("--bars-1h", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    reconciliation = json.loads(Path(args.reconciliation).read_text())
    if not reconciliation.get("summary", {}).get("passed"):
        raise ValueError("replay requires a passing detector reconciliation")
    report = build_report(
        reconciliation=reconciliation,
        bars_5m=load_bars_jsonl(args.bars_5m),
        bars_1h=load_bars_jsonl(args.bars_1h),
    )
    Path(args.output).write_text(
        json.dumps(report, indent=2, allow_nan=False, default=_iso) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
