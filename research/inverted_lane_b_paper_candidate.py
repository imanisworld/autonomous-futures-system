#!/usr/bin/env python3
"""Frozen validation pass for the exact inverted Lane B close-momentum rule.

Research only. Reads the original cache and a separately stored untouched OOS
extension. It does not import runtime, risk, broker, or deployment code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.lane_b_mnq_close_momentum import (
    COMMISSION_RT,
    ET,
    POINT_VALUE,
    TICK_SIZE,
    Trade,
    UTC,
    BoundaryBar,
    _build_trades,
    _classify_sessions,
    _metrics,
)


OLD_ROOT = REPO / "data" / "replay_polygon_5m" / "MNQ"
OOS_ROOT = REPO / "data" / "research_oos" / "inverted_lane_b_2026_07" / "MNQ"
RESULTS_PATH = REPO / "scripts" / "inverted_lane_b_paper_candidate_results.json"
TRADES_PATH = REPO / "scripts" / "inverted_lane_b_paper_candidate_trades.jsonl"
OLD_END = date(2026, 6, 26)
COMMISSION_STRESS = 2.00

EXPECTED = {
    "trades": 490,
    "gross": 4858.50,
    "net": 3643.30,
    "expectancy": 7.4353,
    "profit_factor": 1.1941,
    "h1_net": 3474.90,
    "h2_net": 168.40,
    "long_net": 2838.44,
    "short_net": 804.86,
    "period_nets": [402.94, 3071.96, 120.44, 47.96],
    "holdout_net": 47.96,
    "cost_nets": [3643.30, 3153.30, 2663.30, 2173.30],
    "top_1": 546.52,
    "top_5": 2194.10,
    "net_without_top_5": 1449.20,
}


def _read_sessions(roots: Iterable[Path]) -> dict[date, dict[time, BoundaryBar]]:
    sessions: dict[date, dict[time, BoundaryBar]] = defaultdict(dict)
    for root in roots:
        for path in sorted(root.glob("MNQ_*.jsonl")):
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                ts = ts.astimezone(ET)
                key = ts.timetz().replace(tzinfo=None)
                bar = BoundaryBar(
                    ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    market_condition=str(row.get("market_condition") or "UNKNOWN"),
                )
                existing = sessions[ts.date()].get(key)
                if existing is not None and existing != bar:
                    raise ValueError(f"conflicting duplicate bar at {ts.isoformat()}")
                sessions[ts.date()][key] = bar
    return dict(sessions)


def _inverse_rows(
    sessions: dict[date, dict[time, BoundaryBar]],
    *,
    slippage_ticks: int = 1,
    commission: float = COMMISSION_RT,
) -> tuple[list[Trade], dict]:
    full, shortened, missing, weekends = _classify_sessions(sessions)
    original, exclusions = _build_trades(sessions, full, slippage_ticks)
    slip_points = slippage_ticks * TICK_SIZE
    inverse: list[Trade] = []
    for row in original:
        direction = "SHORT" if row.direction == "LONG" else "LONG"
        signed = 1.0 if direction == "LONG" else -1.0
        entry = row.raw_entry + signed * slip_points
        exit_price = row.raw_exit - signed * slip_points
        gross = signed * (row.raw_exit - row.raw_entry) * POINT_VALUE
        net_before_commission = signed * (exit_price - entry) * POINT_VALUE
        inverse.append(
            replace(
                row,
                direction=direction,
                entry=entry,
                exit=exit_price,
                gross_pnl=gross,
                slippage_cost=gross - net_before_commission,
                commission=commission,
                net_pnl=net_before_commission - commission,
                slippage_ticks_per_side=slippage_ticks,
            )
        )
    return inverse, {
        "full_sessions": len(full),
        "shortened_sessions": len(shortened),
        "missing_weekdays": len(missing),
        "weekend_dates": len(weekends),
        "exclusions": exclusions,
    }


def _metric(rows: list[Trade]) -> dict:
    out = _metrics(rows)
    if rows:
        out["max_drawdown_abs"] = abs(out["max_drawdown"])
    return out


def _group(rows: list[Trade], key) -> dict:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for row in rows:
        buckets[str(key(row))].append(row)
    return {name: _metric(lane) for name, lane in sorted(buckets.items())}


def _concentration(rows: list[Trade]) -> dict:
    winners = sorted((row.net_pnl for row in rows if row.net_pnl > 0), reverse=True)
    total = sum(row.net_pnl for row in rows)
    result = {}
    for n in (1, 5, 10):
        contribution = sum(winners[:n])
        result[f"top_{n}_winner_contribution"] = round(contribution, 2)
        result[f"top_{n}_pct_of_winner_dollars"] = (
            round(contribution / sum(winners), 6) if winners else None
        )
        result[f"net_without_top_{n}"] = round(total - contribution, 2)
    return result


def _tail_path(rows: list[Trade]) -> dict:
    if not rows:
        return {}
    longest = current = 0
    equity = peak = 0.0
    peak_index = 0
    peak_day = rows[0].day
    max_dd = 0.0
    max_dd_peak_index = max_dd_trough_index = 0
    max_recovery_observations = max_recovery_days = 0
    open_peak_index = 0
    open_peak_day = rows[0].day
    underwater = False
    recovery_episodes = []

    for index, row in enumerate(rows):
        if row.net_pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
        equity += row.net_pnl
        if equity >= peak:
            if underwater:
                observations = index - open_peak_index
                days = (row.day - open_peak_day).days
                recovery_episodes.append((observations, days))
                max_recovery_observations = max(max_recovery_observations, observations)
                max_recovery_days = max(max_recovery_days, days)
            peak = equity
            peak_index = index
            peak_day = row.day
            open_peak_index = index
            open_peak_day = row.day
            underwater = False
        else:
            underwater = True
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
                max_dd_peak_index = peak_index
                max_dd_trough_index = index

    terminal_unrecovered = underwater
    terminal_observations = len(rows) - 1 - open_peak_index if underwater else 0
    terminal_days = (rows[-1].day - open_peak_day).days if underwater else 0
    if underwater:
        max_recovery_observations = max(max_recovery_observations, terminal_observations)
        max_recovery_days = max(max_recovery_days, terminal_days)
    return {
        "largest_loss": round(min(row.net_pnl for row in rows), 2),
        "longest_losing_streak": longest,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_peak_date": rows[max_dd_peak_index].day.isoformat(),
        "max_drawdown_trough_date": rows[max_dd_trough_index].day.isoformat(),
        "max_recovery_observations": max_recovery_observations,
        "max_recovery_calendar_days": max_recovery_days,
        "terminal_drawdown_unrecovered": terminal_unrecovered,
        "terminal_underwater_observations": terminal_observations,
        "terminal_underwater_calendar_days": terminal_days,
        "completed_recovery_episodes": len(recovery_episodes),
    }


def _chronological_periods(rows: list[Trade], count: int = 4) -> dict:
    return {
        f"P{i + 1}": _metric(rows[i * len(rows) // count : (i + 1) * len(rows) // count])
        for i in range(count)
    }


def _rolling_months(rows: list[Trade], months: int) -> dict:
    if not rows:
        return {}
    first_idx = rows[0].day.year * 12 + rows[0].day.month - 1
    last_idx = rows[-1].day.year * 12 + rows[-1].day.month - 1
    output = {}
    for end_idx in range(first_idx + months - 1, last_idx + 1):
        start_idx = end_idx - months + 1
        lane = [
            row
            for row in rows
            if start_idx <= row.day.year * 12 + row.day.month - 1 <= end_idx
        ]
        end_year, end_month_zero = divmod(end_idx, 12)
        label = f"{end_year:04d}-{end_month_zero + 1:02d}"
        output[label] = _metric(lane)
    return output


def _tree_hash(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    files = sorted(root.glob("MNQ_*.jsonl"))
    rows = 0
    for path in files:
        digest.update(path.name.encode())
        digest.update(b"\0")
        payload = path.read_bytes()
        digest.update(payload)
        rows += payload.count(b"\n")
    return len(files), rows, digest.hexdigest()


def _serialize(row: Trade, sample: str) -> dict:
    result = dict(row.__dict__)
    result["day"] = row.day.isoformat()
    result["sample"] = sample
    return result


def _reconcile(old_rows: list[Trade]) -> dict:
    midpoint = len(old_rows) // 2
    split = int(len(old_rows) * .75)
    periods = _chronological_periods(old_rows)
    costs = {}
    old_sessions = _read_sessions([OLD_ROOT])
    for ticks in (1, 2, 3, 4):
        rows, _ = _inverse_rows(old_sessions, slippage_ticks=ticks)
        costs[ticks] = _metric(rows)["net_pnl"]
    metric = _metric(old_rows)
    concentration = _concentration(old_rows)
    actual = {
        "trades": len(old_rows),
        "gross": metric["gross_pnl"],
        "net": metric["net_pnl"],
        "expectancy": metric["expectancy"],
        "profit_factor": metric["profit_factor"],
        "h1_net": _metric(old_rows[:midpoint])["net_pnl"],
        "h2_net": _metric(old_rows[midpoint:])["net_pnl"],
        "long_net": _metric([r for r in old_rows if r.direction == "LONG"])["net_pnl"],
        "short_net": _metric([r for r in old_rows if r.direction == "SHORT"])["net_pnl"],
        "period_nets": [periods[f"P{i}"]["net_pnl"] for i in range(1, 5)],
        "holdout_net": _metric(old_rows[split:])["net_pnl"],
        "cost_nets": [costs[i] for i in range(1, 5)],
        "top_1": concentration["top_1_winner_contribution"],
        "top_5": concentration["top_5_winner_contribution"],
        "net_without_top_5": concentration["net_without_top_5"],
    }
    differences = {}
    for key, expected in EXPECTED.items():
        got = actual[key]
        if isinstance(expected, list):
            differences[key] = [round(a - b, 8) for a, b in zip(got, expected)]
        else:
            differences[key] = round(got - expected, 8)
    passed = all(
        all(value == 0 for value in diff) if isinstance(diff, list) else diff == 0
        for diff in differences.values()
    )
    return {"passed": passed, "expected": EXPECTED, "actual": actual, "differences": differences}


def _sample_block(rows: list[Trade]) -> dict:
    midpoint = len(rows) // 2
    return {
        "overall": _metric(rows),
        "half": {
            "H1": _metric(rows[:midpoint]),
            "H2": _metric(rows[midpoint:]),
        },
        "direction": _group(rows, lambda row: row.direction),
        "year": _group(rows, lambda row: row.day.year),
        "quarter": _group(
            rows, lambda row: f"{row.day.year}-Q{((row.day.month - 1) // 3) + 1}"
        ),
        "chronological_periods": _chronological_periods(rows),
        "concentration": _concentration(rows),
        "tail_path": _tail_path(rows),
    }


def run() -> dict:
    old_sessions = _read_sessions([OLD_ROOT])
    combined_sessions = _read_sessions([OLD_ROOT, OOS_ROOT])
    old_rows, old_coverage = _inverse_rows(old_sessions)
    combined_rows, combined_coverage = _inverse_rows(combined_sessions)
    oos_rows = [row for row in combined_rows if row.day > OLD_END]
    if [row for row in combined_rows if row.day <= OLD_END] != old_rows:
        raise AssertionError("adding OOS data changed the old sample")
    reconciliation = _reconcile(old_rows)
    if not reconciliation["passed"]:
        raise RuntimeError(f"frozen inverse did not reconcile: {reconciliation['differences']}")

    cost_sensitivity = {}
    for ticks in (1, 2, 3, 4):
        rows, _ = _inverse_rows(combined_sessions, slippage_ticks=ticks)
        cost_sensitivity[f"{ticks}_ticks"] = _metric(rows)
    commission_rows, _ = _inverse_rows(
        combined_sessions, slippage_ticks=1, commission=COMMISSION_STRESS
    )
    cost_sensitivity["1_tick_commission_2_00"] = _metric(commission_rows)

    earliest_six = combined_rows[: min(len(combined_rows), 126)]
    latest_six = combined_rows[-min(len(combined_rows), 126) :]
    results = {
        "study": "Exact inverted Lane B paper-candidate validation",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "preregistration_commit": "853206b",
        "classification_ceiling": "PROMISING BUT UNPROVEN",
        "frozen_rule_changed": False,
        "reproduction": reconciliation,
        "data": {
            "old": {
                "root": str(OLD_ROOT.relative_to(REPO)),
                "tree": _tree_hash(OLD_ROOT),
                "coverage": old_coverage,
            },
            "untouched_oos": {
                "root": str(OOS_ROOT.relative_to(REPO)),
                "tree": _tree_hash(OOS_ROOT),
                "first_trade_date": oos_rows[0].day.isoformat() if oos_rows else None,
                "last_trade_date": oos_rows[-1].day.isoformat() if oos_rows else None,
                "vendor_overlap_bars_compared": 2136,
                "vendor_overlap_ohlcv_mismatches": 0,
                "fetch": {
                    "symbol": "MNQ",
                    "start": "2026-06-27",
                    "end": "2026-07-26",
                    "timeframe_minutes": 5,
                    "warmup_days": 10,
                },
            },
            "combined_coverage": combined_coverage,
        },
        "samples": {
            "old": _sample_block(old_rows),
            "untouched_oos": _sample_block(oos_rows),
            "combined": _sample_block(combined_rows),
        },
        "temporal_stability": {
            "rolling_3_month": _rolling_months(combined_rows, 3),
            "rolling_6_month": _rolling_months(combined_rows, 6),
            "earliest_126_trades": _metric(earliest_six),
            "latest_126_trades": _metric(latest_six),
        },
        "cost_sensitivity_combined": cost_sensitivity,
    }
    with RESULTS_PATH.open("w") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with TRADES_PATH.open("w") as handle:
        for row in combined_rows:
            sample = "old" if row.day <= OLD_END else "untouched_oos"
            handle.write(json.dumps(_serialize(row, sample), sort_keys=True) + "\n")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    results = run()
    print(
        json.dumps(
            {
                name: {
                    "trades": block["overall"]["resolved"],
                    "net": block["overall"]["net_pnl"],
                    "pf": block["overall"]["profit_factor"],
                }
                for name, block in results["samples"].items()
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
