#!/usr/bin/env python3
"""Exact evidence-only replay for the proposed Transition repair.

Counterfactual under test (MNQ only):
- existing transition_failed_breakdown_reclaim candidates
- session == new_york
- decision-close IOC entry
- 1 adverse tick on entry
- 400-tick planned protective stop (100 MNQ points)
- 1 adverse tick on stop exit
- otherwise flatten 30 minutes after the decision, with 1 adverse exit tick
- $1.48 round-turn commission
- one open position at a time
- max 3 filled trades per day

This script changes no strategy/config/risk/broker/deployment code. It requires
raw 5-minute corpora and FAILS CLOSED if a required bar is missing. The global
MNQ max_stop_ticks=120 is intentionally not changed; 400 ticks is a research
counterfactual only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CANDIDATE_FILE = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
LANE_CORPUS = {
    "transition_mnq": "replay_polygon_5m",
    "transition_mnq_audit": "replay_corpus_v1_5m",
}
TICK_SIZE = 0.25
POINT_VALUE = 2.0
IOC_TOLERANCE_TICKS = 32.0
ENTRY_SLIPPAGE_TICKS = 1.0
EXIT_SLIPPAGE_TICKS = 1.0
STOP_TICKS = 400.0
COMMISSION = 1.48
MAX_TRADES_PER_DAY = 3
BAR_MINUTES = 5
HOLD_MINUTES = 30


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_candidates(path: Path, lane: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            control = ((row.get("control") or {}).get("30m") or {})
            if (
                row.get("lane") == lane
                and row.get("instrument") == "MNQ"
                and row.get("session") == "new_york"
                and control.get("exit_bar_ts") is not None
            ):
                rows.append(row)
    return sorted(rows, key=lambda r: (_dt(r["bar_ts"]), r.get("date", "")))


def load_bars(data_root: Path, corpus: str) -> dict[datetime, dict[str, Any]]:
    base = data_root / corpus / "MNQ"
    files = sorted(base.glob("MNQ_*.jsonl"))
    if not files:
        raise FileNotFoundError(
            f"required raw 5m corpus missing: {base}. "
            "Do not substitute summary statistics or coarser bars."
        )
    out: dict[datetime, dict[str, Any]] = {}
    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                bar = json.loads(line)
                ts = _dt(bar["timestamp"])
                out[ts] = bar
    return out


def _planned_ioc_fill(row: dict[str, Any]) -> tuple[float, float, int]:
    direction = str(row["direction"]).upper()
    sign = 1 if direction == "LONG" else -1
    plan_entry = float(row["entry"])
    decision_close = float((row.get("gates") or {}).get("decision_close"))
    if not math.isclose(plan_entry, decision_close, abs_tol=1e-9):
        raise ValueError(
            f"entry/decision-close mismatch at {row['bar_ts']}: "
            f"entry={plan_entry} close={decision_close}"
        )
    tol = IOC_TOLERANCE_TICKS * TICK_SIZE
    market = decision_close
    if direction == "LONG":
        limit_px = plan_entry + tol
        if market > limit_px:
            raise ValueError("unexpected IOC no-fill: market above long limit")
        fill = min(limit_px, market + ENTRY_SLIPPAGE_TICKS * TICK_SIZE)
    else:
        limit_px = plan_entry - tol
        if market < limit_px:
            raise ValueError("unexpected IOC no-fill: market below short limit")
        fill = max(limit_px, market - ENTRY_SLIPPAGE_TICKS * TICK_SIZE)
    return fill, plan_entry, sign


def resolve_one(row: dict[str, Any], bars: dict[datetime, dict[str, Any]]) -> dict[str, Any]:
    decision_ts = _dt(row["bar_ts"])
    exit_ts = _dt(row["control"]["30m"]["exit_bar_ts"])
    expected_exit = decision_ts + timedelta(minutes=HOLD_MINUTES)
    if exit_ts != expected_exit:
        raise ValueError(
            f"30m exit timestamp mismatch at {row['bar_ts']}: "
            f"artifact={exit_ts.isoformat()} expected={expected_exit.isoformat()}"
        )

    fill, plan_entry, sign = _planned_ioc_fill(row)
    # Planned stop stays 400 ticks from the signal's decision-close entry.
    # With 1-tick adverse entry + 1-tick adverse stop fill, realized worst loss
    # is 402 ticks before commission. We report that rather than hiding it.
    stop = plan_entry - sign * STOP_TICKS * TICK_SIZE

    path: list[tuple[datetime, dict[str, Any]]] = []
    for step in range(1, HOLD_MINUTES // BAR_MINUTES + 1):
        ts = decision_ts + timedelta(minutes=BAR_MINUTES * step)
        bar = bars.get(ts)
        if bar is None:
            raise KeyError(f"missing required 5m bar {ts.isoformat()} for {row['bar_ts']}")
        path.append((ts, bar))

    for ts, bar in path:
        high = float(bar["high"])
        low = float(bar["low"])
        stop_hit = low <= stop if sign > 0 else high >= stop
        if stop_hit:
            exit_px = stop - sign * EXIT_SLIPPAGE_TICKS * TICK_SIZE
            gross = sign * (exit_px - fill) * POINT_VALUE
            return {
                "result": "LOSS",
                "exit_reason": "STOP_HIT",
                "entry": fill,
                "planned_entry": plan_entry,
                "stop": stop,
                "exit": exit_px,
                "exit_ts": ts.isoformat(),
                "net": round(gross - COMMISSION, 2),
            }

    close = float(path[-1][1]["close"])
    exit_px = close - sign * EXIT_SLIPPAGE_TICKS * TICK_SIZE
    gross = sign * (exit_px - fill) * POINT_VALUE
    net = round(gross - COMMISSION, 2)
    return {
        "result": "WIN" if net > 0 else ("LOSS" if net < 0 else "BREAKEVEN"),
        "exit_reason": "TIME_30M",
        "entry": fill,
        "planned_entry": plan_entry,
        "stop": stop,
        "exit": exit_px,
        "exit_ts": path[-1][0].isoformat(),
        "net": net,
    }


def stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [float(t["net"]) for t in trades]
    if not vals:
        return {"n": 0}
    gp = sum(v for v in vals if v > 0)
    gl = -sum(v for v in vals if v < 0)
    mid = len(vals) // 2
    equity = peak = max_dd = 0.0
    for value in vals:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "n": len(vals),
        "wins": sum(v > 0 for v in vals),
        "losses": sum(v < 0 for v in vals),
        "net": round(sum(vals), 2),
        "pf": round(gp / gl, 6) if gl else None,
        "h1": round(sum(vals[:mid]), 2),
        "h2": round(sum(vals[mid:]), 2),
        "worst": round(min(vals), 2),
        "max_drawdown": round(max_dd, 2),
        "stop_losses": sum(t["exit_reason"] == "STOP_HIT" for t in trades),
    }


def run_lane(rows: list[dict[str, Any]], bars: dict[datetime, dict[str, Any]]) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    sequential: list[dict[str, Any]] = []
    skipped = Counter()
    busy_until: datetime | None = None
    day_count: dict[str, int] = defaultdict(int)

    # Resolve every row first. Any missing bar aborts the audit instead of
    # silently reducing the sample.
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        trade = resolve_one(row, bars)
        trade["bar_ts"] = row["bar_ts"]
        trade["date"] = row.get("date")
        all_trades.append(trade)
        resolved.append((row, trade))

    for row, trade in resolved:
        decision_ts = _dt(row["bar_ts"])
        if busy_until is not None and decision_ts <= busy_until:
            skipped["POSITION_ALREADY_OPEN"] += 1
            continue
        day = str(row.get("date") or decision_ts.date().isoformat())
        if day_count[day] >= MAX_TRADES_PER_DAY:
            skipped["DAILY_TRADE_LIMIT"] += 1
            continue
        sequential.append(trade)
        day_count[day] += 1
        busy_until = _dt(trade["exit_ts"])

    return {
        "eligible_candidates": len(rows),
        "all_candidates_diagnostic": stats(all_trades),
        "sequential_max1_max3day": stats(sequential),
        "skipped": dict(skipped),
        "planned_stop_ticks": STOP_TICKS,
        "planned_stop_risk_dollars": round(STOP_TICKS * 0.50, 2),
        "realized_full_stop_loss_after_slippage_and_commission": round(
            -((STOP_TICKS + ENTRY_SLIPPAGE_TICKS + EXIT_SLIPPAGE_TICKS) * 0.50) - COMMISSION,
            2,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--candidates", type=Path, default=CANDIDATE_FILE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "verdict": "PROMISING BUT UNPROVEN",
        "model": {
            "instrument": "MNQ",
            "session": "new_york",
            "entry": "decision-close IOC",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "entry_slippage_ticks": ENTRY_SLIPPAGE_TICKS,
            "stop_ticks": STOP_TICKS,
            "exit": "30m max hold or protective stop, whichever occurs first",
            "exit_slippage_ticks": EXIT_SLIPPAGE_TICKS,
            "commission_round_trip": COMMISSION,
            "max_open_positions": 1,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "global_max_stop_ticks_unchanged": 120,
        },
        "lanes": {},
    }

    for lane, corpus in LANE_CORPUS.items():
        rows = load_candidates(args.candidates, lane)
        if not rows:
            raise RuntimeError(f"no eligible candidates for {lane}")
        bars = load_bars(args.data_root, corpus)
        report["lanes"][lane] = run_lane(rows, bars)

    binding = [v["sequential_max1_max3day"] for v in report["lanes"].values()]
    survives = all(
        b.get("n", 0) > 0
        and b.get("net", 0) > 0
        and (b.get("pf") or 0) > 1
        and b.get("h1", 0) > 0
        and b.get("h2", 0) > 0
        for b in binding
    )
    report["repair_candidate_survives_basic_stability"] = survives
    report["verdict"] = "PROMISING BUT UNPROVEN" if survives else "BROKEN"

    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
