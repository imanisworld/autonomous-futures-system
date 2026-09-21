"""Futures intraday-momentum replication (fim-v0.1), research-only."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.futures_contracts import TICK_SIZE, TICK_VALUE
from research.futures_non_strat_coverage import (
    HALF_SPLIT,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

STUDY_ID = "FUTURES_INTRADAY_MOMENTUM_REPLICATION"
STUDY_VERSION = "fim-v0.1"
COMMISSION_RT = 1.24
COST_CELLS = {"base": 1.0, "stress": 2.0}
NULL_SEED = 56021
NULL_PERMUTATIONS = 5000
SIGNAL_BAR_INDEX = 71  # 15:25-15:30 ET
ENTRY_BAR_INDEX = 72   # 15:30-15:35 ET
EXIT_BAR_INDEX = 77    # 15:55-16:00 ET


@dataclass(frozen=True)
class TradeRow:
    instrument: str
    session_date: str
    half: str
    direction: str
    rod_return: float
    decision_open: float
    fill_entry: float
    exit_anchor: float
    fill_exit: float
    slippage_label: str
    slippage_ticks: float
    gross_pnl: float
    net_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _point_value(instrument: str) -> float:
    return TICK_VALUE[instrument] / TICK_SIZE[instrument]


def build_trade(
    instrument: str,
    session_date: date,
    prior_close: float,
    bars: Sequence,
    *,
    slippage_label: str,
    slippage_ticks: float,
) -> TradeRow | None:
    signal_close = float(bars[SIGNAL_BAR_INDEX].close)
    rod_return = signal_close / float(prior_close) - 1.0
    if rod_return == 0.0:
        return None
    direction = "LONG" if rod_return > 0 else "SHORT"
    decision_open = float(bars[ENTRY_BAR_INDEX].open)
    exit_anchor = float(bars[EXIT_BAR_INDEX].close)
    slip = slippage_ticks * TICK_SIZE[instrument]
    if direction == "LONG":
        fill_entry = decision_open + slip
        fill_exit = exit_anchor - slip
        gross = (fill_exit - fill_entry) * _point_value(instrument)
    else:
        fill_entry = decision_open - slip
        fill_exit = exit_anchor + slip
        gross = (fill_entry - fill_exit) * _point_value(instrument)
    return TradeRow(
        instrument=instrument,
        session_date=session_date.isoformat(),
        half="H1" if session_date < HALF_SPLIT else "H2",
        direction=direction,
        rod_return=rod_return,
        decision_open=decision_open,
        fill_entry=fill_entry,
        exit_anchor=exit_anchor,
        fill_exit=fill_exit,
        slippage_label=slippage_label,
        slippage_ticks=slippage_ticks,
        gross_pnl=gross,
        net_pnl=gross - COMMISSION_RT,
    )


def collect_rows(instrument: str) -> tuple[list[TradeRow], dict[str, int]]:
    files = session_files(instrument)
    if not files:
        raise SystemExit(f"no replay files for {instrument}")
    days = sorted(files)
    excluded = roll_excluded_sessions(days)
    loaded: dict[date, list] = {}
    skipped = {"incomplete": 0, "roll": 0, "no_prior": 0, "zero_signal": 0}
    for day in days:
        bars, _ = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = bars

    complete = sorted(loaded)
    rows: list[TradeRow] = []
    for i, day in enumerate(complete):
        if day in excluded:
            skipped["roll"] += 1
            continue
        if i == 0 or complete[i - 1] in excluded:
            skipped["no_prior"] += 1
            continue
        prior = loaded[complete[i - 1]]
        current = loaded[day]
        prior_close = float(prior[EXIT_BAR_INDEX].close)
        made = False
        for label, ticks in COST_CELLS.items():
            row = build_trade(
                instrument,
                day,
                prior_close,
                current,
                slippage_label=label,
                slippage_ticks=ticks,
            )
            if row is not None:
                rows.append(row)
                made = True
        if not made:
            skipped["zero_signal"] += 1
    return rows, skipped


def _pf(values: Sequence[float]) -> float | None:
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg == 0:
        return None if pos == 0 else float("inf")
    return pos / neg


def _max_dd(values: Sequence[float]) -> float:
    equity = peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _summary(rows: Sequence[TradeRow]) -> dict[str, Any]:
    pnl = [r.net_pnl for r in rows]
    months: dict[str, float] = {}
    for r in rows:
        key = r.session_date[:7]
        months[key] = months.get(key, 0.0) + r.net_pnl
    positive_months = [v for v in months.values() if v > 0]
    top_share = (
        max(positive_months) / sum(positive_months)
        if positive_months and sum(positive_months) > 0
        else None
    )
    return {
        "n": len(rows),
        "longs": sum(r.direction == "LONG" for r in rows),
        "shorts": sum(r.direction == "SHORT" for r in rows),
        "net": round(sum(pnl), 2),
        "expectancy": round(statistics.fmean(pnl), 4) if pnl else None,
        "pf": round(_pf(pnl), 5) if _pf(pnl) not in (None, float("inf")) else _pf(pnl),
        "win_rate": round(sum(v > 0 for v in pnl) / len(pnl), 5) if pnl else None,
        "max_drawdown": round(_max_dd(pnl), 2),
        "rod_abs_median": round(statistics.median(abs(r.rod_return) for r in rows), 7) if rows else None,
        "rod_abs_p90": round(_quantile([abs(r.rod_return) for r in rows], 0.9), 7) if rows else None,
        "top_positive_month_share": round(top_share, 5) if top_share is not None else None,
    }


def permutation_null_p95(rows: Sequence[TradeRow]) -> float | None:
    if not rows:
        return None
    # Recover the realized unsigned last-30m move net of no costs, then shuffle
    # the observed direction labels. Cost stays fixed for every permutation.
    point = _point_value(rows[0].instrument)
    cost = 2.0 * rows[0].slippage_ticks * TICK_VALUE[rows[0].instrument] + COMMISSION_RT
    moves = [(r.exit_anchor - r.decision_open) * point for r in rows]
    signs = [1 if r.direction == "LONG" else -1 for r in rows]
    rng = random.Random(NULL_SEED)
    totals: list[float] = []
    for _ in range(NULL_PERMUTATIONS):
        perm = signs[:]
        rng.shuffle(perm)
        totals.append(sum(s * m - cost for s, m in zip(perm, moves)))
    return round(_quantile(totals, 0.95), 2)


def gate(cell: dict[str, Any]) -> dict[str, Any]:
    h1, h2 = cell["halves"]["H1"], cell["halves"]["H2"]
    checks = {
        "sample": h1["n"] >= 100 and h2["n"] >= 100,
        "base_halves": h1["net"] > 0 and h2["net"] > 0 and h1["pf"] is not None and h2["pf"] is not None and h1["pf"] > 1.10 and h2["pf"] > 1.10,
        "stress_halves": True,
        "beats_null": cell["total"]["net"] > cell["null_p95"],
        "concentration": cell["total"]["top_positive_month_share"] is not None and cell["total"]["top_positive_month_share"] < 0.60,
    }
    return {"checks": checks, "numeric_pass": all(checks.values())}


def build_report(by_instrument: dict[str, list[TradeRow]], skipped: dict[str, dict[str, int]]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "null_seed": NULL_SEED,
        "null_permutations": NULL_PERMUTATIONS,
        "instruments": {},
    }
    for instrument, rows in by_instrument.items():
        cells: dict[str, Any] = {}
        for label in COST_CELLS:
            cell_rows = [r for r in rows if r.slippage_label == label]
            halves = {
                h: _summary([r for r in cell_rows if r.half == h])
                for h in ("H1", "H2")
            }
            cells[label] = {
                "total": _summary(cell_rows),
                "halves": halves,
                "null_p95": permutation_null_p95(cell_rows),
            }
        # Cross-cell gate: sample/PF/null/concentration from base, stress profitability from stress.
        base, stress = cells["base"], cells["stress"]
        checks = {
            "sample": base["halves"]["H1"]["n"] >= 100 and base["halves"]["H2"]["n"] >= 100,
            "base_halves": all(
                base["halves"][h]["net"] > 0
                and base["halves"][h]["pf"] is not None
                and base["halves"][h]["pf"] > 1.10
                for h in ("H1", "H2")
            ),
            "stress_halves": all(stress["halves"][h]["net"] > 0 for h in ("H1", "H2")),
            "beats_null": base["total"]["net"] > base["null_p95"],
            "concentration": base["total"]["top_positive_month_share"] is not None
            and base["total"]["top_positive_month_share"] < 0.60,
        }
        report["instruments"][instrument] = {
            "skipped": skipped[instrument],
            "cells": cells,
            "gate": {"checks": checks, "numeric_pass": all(checks.values())},
        }
    cross = all(report["instruments"][i]["gate"]["numeric_pass"] for i in ("MNQ", "MES"))
    report["gate"] = {
        "cross_instrument_numeric_pass": cross,
        "classification": "PROMISING BUT UNPROVEN" if cross else "WAIT",
        "passes_for_paper": False,
        "reason": "research-only time-exit replication has no protective stop",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", action="append", choices=["MNQ", "MES"])
    parser.add_argument("--out", type=Path, default=ROOT / "logs" / "fim_v01")
    args = parser.parse_args()
    instruments = args.instrument or ["MNQ", "MES"]
    by_instrument: dict[str, list[TradeRow]] = {}
    skipped: dict[str, dict[str, int]] = {}
    for instrument in instruments:
        rows, meta = collect_rows(instrument)
        by_instrument[instrument] = rows
        skipped[instrument] = meta
    report = build_report(by_instrument, skipped)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "rows.jsonl").open("w") as handle:
        for instrument in instruments:
            for row in by_instrument[instrument]:
                handle.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")
    (args.out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
