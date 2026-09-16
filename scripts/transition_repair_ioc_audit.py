#!/usr/bin/env python3
"""Exact evidence-only replay for the proposed Transition repair.

Counterfactual under test (MNQ only):
- existing transition_failed_breakdown_reclaim candidates
- session == new_york
- decision-close IOC entry using the candidate's planned entry as the limit anchor
- real PaperBroker IOC fill / structural bracket-validity behavior
- 32-tick MNQ IOC tolerance
- 1 adverse tick on entry
- 400-tick planned protective stop from the candidate's planned entry
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
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker

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
# Only supplies PaperBroker's required complete bracket. It is deliberately so
# distant that this probe has no economic target; the actual exit is stop/30m.
DUMMY_TARGET_TICKS = 100_000.0
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
                out[_dt(bar["timestamp"])] = bar
    return out


def planned_ioc_fill(row: dict[str, Any]) -> dict[str, Any]:
    """Open one candidate through the real PaperBroker IOC path.

    Planned entry and decision-close market are intentionally separate. This
    preserves genuine IOC cancellations and, critically, PaperBroker's
    structural bracket-validity rejection when a marketable fill lands beyond
    its own stop/target. That guard is non-negotiable after the inverse-ORB
    forensic finding.
    """
    direction = str(row["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"invalid direction {direction!r}")
    sign = 1 if direction == "LONG" else -1
    plan_entry = float(row["entry"])
    decision_close_raw = (row.get("gates") or {}).get("decision_close")
    if decision_close_raw is None:
        raise ValueError(f"missing decision_close at {row.get('bar_ts')}")
    market = float(decision_close_raw)
    stop = plan_entry - sign * STOP_TICKS * TICK_SIZE
    target = plan_entry + sign * DUMMY_TARGET_TICKS * TICK_SIZE
    limit_px = plan_entry + sign * IOC_TOLERANCE_TICKS * TICK_SIZE

    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=ENTRY_SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": IOC_TOLERANCE_TICKS},
    )
    opened = broker.execute_bracket(
        BracketOrder(
            instrument="MNQ",
            direction=direction,
            entry=plan_entry,
            stop=stop,
            target=target,
            rr_ratio=DUMMY_TARGET_TICKS / STOP_TICKS,
            strategy=str(row.get("strategy") or "transition_failed_breakdown_reclaim"),
            contracts=1,
            min_rr_ratio=0.0,
            post_fill_validation_required=False,
        ),
        market_price=market,
    )
    if opened.result == "CANCELLED":
        return {
            "status": "NO_FILL",
            "reason": opened.exit_reason or "ENTRY_NOT_FILLED",
            "planned_entry": plan_entry,
            "decision_close": market,
            "limit": limit_px,
            "stop": stop,
            "target": target,
            "sign": sign,
            "execution_audit": opened.execution_audit,
        }
    if opened.result != "OPEN":
        raise RuntimeError(f"unexpected PaperBroker IOC result {opened.result!r}")
    return {
        "status": "FILLED",
        "fill": float(opened.entry_price),
        "planned_entry": plan_entry,
        "decision_close": market,
        "limit": limit_px,
        "stop": stop,
        "target": target,
        "sign": sign,
        "execution_audit": opened.execution_audit,
    }


def resolve_one(row: dict[str, Any], bars: dict[datetime, dict[str, Any]]) -> dict[str, Any]:
    decision_ts = _dt(row["bar_ts"])
    exit_ts = _dt(row["control"]["30m"]["exit_bar_ts"])
    expected_exit = decision_ts + timedelta(minutes=HOLD_MINUTES)
    if exit_ts != expected_exit:
        raise ValueError(
            f"30m exit timestamp mismatch at {row['bar_ts']}: "
            f"artifact={exit_ts.isoformat()} expected={expected_exit.isoformat()}"
        )

    entry = planned_ioc_fill(row)
    if entry["status"] == "NO_FILL":
        return {
            "result": "NO_FILL",
            "exit_reason": entry["reason"],
            "entry": None,
            "planned_entry": entry["planned_entry"],
            "decision_close": entry["decision_close"],
            "limit": entry["limit"],
            "stop": entry["stop"],
            "exit": None,
            "exit_ts": decision_ts.isoformat(),
            "net": None,
        }

    fill = float(entry["fill"])
    plan_entry = float(entry["planned_entry"])
    sign = int(entry["sign"])
    stop = float(entry["stop"])

    path: list[tuple[datetime, dict[str, Any]]] = []
    for step in range(1, HOLD_MINUTES // BAR_MINUTES + 1):
        ts = decision_ts + timedelta(minutes=BAR_MINUTES * step)
        bar = bars.get(ts)
        if bar is None:
            raise KeyError(f"missing required 5m bar {ts.isoformat()} for {row['bar_ts']}")
        path.append((ts, bar))

    # With no economic target in this variant, a stop touch is the only intrabar
    # terminal condition. The stop fill is one adverse tick, matching the shared
    # PaperBroker slippage convention.
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
                "decision_close": entry["decision_close"],
                "limit": entry["limit"],
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
        "decision_close": entry["decision_close"],
        "limit": entry["limit"],
        "stop": stop,
        "exit": exit_px,
        "exit_ts": path[-1][0].isoformat(),
        "net": net,
    }


def stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    fills = [t for t in trades if t.get("net") is not None]
    vals = [float(t["net"]) for t in fills]
    no_fill_reasons = Counter(
        str(t.get("exit_reason") or "UNKNOWN") for t in trades if t.get("result") == "NO_FILL"
    )
    if not vals:
        return {
            "attempts": len(trades),
            "fills": 0,
            "no_fills": sum(t.get("result") == "NO_FILL" for t in trades),
            "no_fill_reasons": dict(no_fill_reasons),
            "n": 0,
        }
    gp = sum(v for v in vals if v > 0)
    gl = -sum(v for v in vals if v < 0)
    mid = len(vals) // 2
    equity = peak = max_dd = 0.0
    for value in vals:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "attempts": len(trades),
        "fills": len(fills),
        "no_fills": sum(t.get("result") == "NO_FILL" for t in trades),
        "no_fill_reasons": dict(no_fill_reasons),
        "n": len(vals),
        "wins": sum(v > 0 for v in vals),
        "losses": sum(v < 0 for v in vals),
        "net": round(sum(vals), 2),
        "pf": round(gp / gl, 6) if gl else None,
        "h1": round(sum(vals[:mid]), 2),
        "h2": round(sum(vals[mid:]), 2),
        "worst": round(min(vals), 2),
        "max_drawdown": round(max_dd, 2),
        "stop_losses": sum(t["exit_reason"] == "STOP_HIT" for t in fills),
    }


def run_lane(rows: list[dict[str, Any]], bars: dict[datetime, dict[str, Any]]) -> dict[str, Any]:
    all_attempts: list[dict[str, Any]] = []
    sequential: list[dict[str, Any]] = []
    skipped = Counter()
    busy_until: datetime | None = None
    day_count: dict[str, int] = defaultdict(int)

    # Resolve every eligible candidate first. Any required raw bar missing from
    # a FILLED attempt aborts the audit instead of silently reducing the sample.
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        trade = resolve_one(row, bars)
        trade["bar_ts"] = row["bar_ts"]
        trade["date"] = row.get("date")
        all_attempts.append(trade)
        resolved.append((row, trade))

    for row, trade in resolved:
        decision_ts = _dt(row["bar_ts"])
        if busy_until is not None and decision_ts <= busy_until:
            skipped["POSITION_ALREADY_OPEN"] += 1
            continue
        if trade["result"] == "NO_FILL":
            sequential.append(trade)
            continue
        day = str(row.get("date") or decision_ts.date().isoformat())
        if day_count[day] >= MAX_TRADES_PER_DAY:
            skipped["DAILY_TRADE_LIMIT"] += 1
            continue
        sequential.append(trade)
        day_count[day] += 1
        busy_until = _dt(trade["exit_ts"])

    plan_close_deltas_ticks = [
        round((float(r["gates"]["decision_close"]) - float(r["entry"])) / TICK_SIZE, 4)
        for r in rows
    ]
    return {
        "eligible_candidates": len(rows),
        "plan_to_decision_close_delta_ticks": {
            "min": min(plan_close_deltas_ticks),
            "max": max(plan_close_deltas_ticks),
            "nonzero": sum(v != 0 for v in plan_close_deltas_ticks),
        },
        "all_candidates_diagnostic": stats(all_attempts),
        "sequential_max1_max3day": stats(sequential),
        "skipped": dict(skipped),
        "planned_stop_ticks": STOP_TICKS,
        "planned_stop_risk_dollars": round(STOP_TICKS * 0.50, 2),
        "note": (
            "realized stop loss varies slightly with plan-to-market IOC entry delta; "
            "the report's worst trade is binding rather than a hard-coded $200 assumption"
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
            "entry": "real PaperBroker ioc_limit at decision-close market, planned-entry anchored limit",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "entry_slippage_ticks": ENTRY_SLIPPAGE_TICKS,
            "stop_ticks_from_planned_entry": STOP_TICKS,
            "exit": "30m max hold or protective stop, whichever occurs first",
            "exit_slippage_ticks": EXIT_SLIPPAGE_TICKS,
            "commission_round_trip": COMMISSION,
            "max_open_positions": 1,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "pessimistic_structural_bracket_validity": True,
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
