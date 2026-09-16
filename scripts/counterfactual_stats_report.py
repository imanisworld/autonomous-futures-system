#!/usr/bin/env python3
"""Aggregate already-produced counterfactual rows without re-running research logic.

This script deliberately does NOT recreate detector replay, regime substitution,
market-condition substitution, or fill simulation. Those rows must already have
been produced by an audited study. The reporter only makes the statistics
repeatable and explicit, including fill/no-fill counts, PF, H1/H2, chronological
drawdown, MAE/MFE when supplied, break-even round-trip cost, and labelled
hypothetical cost sensitivity.

Input JSONL contract (one candidate per row):
  cohort: str                 required (e.g. A, B, C, D0, D1, D2)
  sample_half: H1|H2          required; assigned by the audited study producer
  ts: offset-aware ISO-8601   required; used only for chronological ordering
  filled: bool                required
  pnl_dollars: number|null    required number when filled
  mae_r: number|null          optional, non-negative when present
  mfe_r: number|null          optional, non-negative when present

The reporter never assigns a candidate to a cohort or sample half; doing so here
would invent research logic that belongs in the study that produced the row.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{lineno}: row must be a JSON object")
                rows.append(value)
    return rows


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _timestamp(value: Any, *, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _validated_costs(values: Iterable[float]) -> list[float]:
    costs: list[float] = []
    for index, value in enumerate(values, start=1):
        cost = _number(value, label=f"hypothetical cost {index}")
        if cost < 0:
            raise ValueError("hypothetical round-trip cost cannot be negative")
        costs.append(cost)
    return costs


def validate_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=1):
        cohort = str(raw.get("cohort") or "").strip()
        if not cohort:
            raise ValueError(f"row {index}: cohort is required")
        sample_half = str(raw.get("sample_half") or "").strip().upper()
        if sample_half not in {"H1", "H2"}:
            raise ValueError(f"row {index}: sample_half must be H1 or H2")
        if not isinstance(raw.get("filled"), bool):
            raise ValueError(f"row {index}: filled must be boolean")

        row = dict(raw)
        row["cohort"] = cohort
        row["sample_half"] = sample_half
        row["_ts"] = _timestamp(raw.get("ts"), label=f"row {index} ts")

        if row["filled"]:
            if row.get("pnl_dollars") is None:
                raise ValueError(f"row {index}: filled row requires pnl_dollars")
            row["pnl_dollars"] = _number(
                row.get("pnl_dollars"), label=f"row {index} pnl_dollars"
            )
        elif row.get("pnl_dollars") is not None:
            raise ValueError(f"row {index}: no-fill row must not carry pnl_dollars")

        for key in ("mae_r", "mfe_r"):
            if row.get(key) is not None:
                row[key] = _number(row[key], label=f"row {index} {key}")
                if row[key] < 0:
                    raise ValueError(f"row {index}: {key} cannot be negative")
        clean.append(row)
    if not clean:
        raise ValueError("counterfactual input contains no rows")
    return clean


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    if gross_loss == 0:
        return None
    return round(gross_profit / gross_loss, 6)


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize_cohort(
    rows: list[dict[str, Any]], hypothetical_costs: Iterable[float]
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["_ts"])
    filled_rows = [row for row in ordered if row["filled"]]
    pnls = [float(row["pnl_dollars"]) for row in filled_rows]
    gross = round(sum(pnls), 2)
    fills = len(filled_rows)
    wins = sum(value > 0 for value in pnls)
    losses = sum(value < 0 for value in pnls)
    breakeven_cost = round(gross / fills, 4) if fills else None
    mae = [float(row["mae_r"]) for row in filled_rows if row.get("mae_r") is not None]
    mfe = [float(row["mfe_r"]) for row in filled_rows if row.get("mfe_r") is not None]
    h1_filled = [row for row in filled_rows if row["sample_half"] == "H1"]
    h2_filled = [row for row in filled_rows if row["sample_half"] == "H2"]
    costs = _validated_costs(hypothetical_costs)
    return {
        "candidates": len(ordered),
        "fills": fills,
        "no_fills": len(ordered) - fills,
        "fill_rate_percent": round((fills / len(ordered)) * 100.0, 2),
        "wins": wins,
        "losses": losses,
        "breakevens": sum(value == 0 for value in pnls),
        "win_rate_percent": round((wins / fills) * 100.0, 2) if fills else None,
        "gross_pnl_dollars": gross,
        "profit_factor": _profit_factor(pnls),
        "h1_fills": len(h1_filled),
        "h2_fills": len(h2_filled),
        "h1_pnl_dollars": round(
            sum(float(row["pnl_dollars"]) for row in h1_filled), 2
        ),
        "h2_pnl_dollars": round(
            sum(float(row["pnl_dollars"]) for row in h2_filled), 2
        ),
        "worst_trade_dollars": round(min(pnls), 2) if pnls else None,
        "max_drawdown_dollars": _max_drawdown(pnls),
        "mean_mae_r": _mean(mae),
        "mean_mfe_r": _mean(mfe),
        "break_even_round_trip_cost_dollars": breakeven_cost,
        "hypothetical_cost_sensitivity": {
            f"{cost:g}": round(gross - (cost * fills), 2) for cost in costs
        },
    }


def build_report(
    rows: Iterable[dict[str, Any]],
    *,
    hypothetical_costs: Iterable[float] = (0.0, 2.0, 4.0),
) -> dict[str, Any]:
    clean = validate_rows(rows)
    costs = _validated_costs(hypothetical_costs)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clean:
        grouped[row["cohort"]].append(row)
    return {
        "authority": "aggregation_only",
        "research_logic_replayed": False,
        "sample_split_source": "input.sample_half",
        "chronology_source": "input.ts",
        "commission_configured": False,
        "cost_note": (
            "Cost sensitivity is hypothetical; no commission value is inferred "
            "or configured by this reporter."
        ),
        "rows": len(clean),
        "cohorts": {
            cohort: summarize_cohort(grouped[cohort], costs)
            for cohort in sorted(grouped)
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="+", help="Counterfactual JSONL file(s), one candidate per row"
    )
    parser.add_argument(
        "--hypothetical-cost",
        type=float,
        action="append",
        dest="costs",
        help="Hypothetical round-trip cost in dollars; repeatable. Default: 0, 2, 4.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full JSON report")
    parser.add_argument("--out", help="Optional path for full JSON report")
    args = parser.parse_args(argv)

    costs = args.costs if args.costs is not None else [0.0, 2.0, 4.0]
    try:
        report = build_report(
            _load_jsonl([Path(value) for value in args.inputs]),
            hypothetical_costs=costs,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        for cohort, stats in report["cohorts"].items():
            print(
                f"{cohort}: fills={stats['fills']}/{stats['candidates']} "
                f"gross=${stats['gross_pnl_dollars']:.2f} "
                f"PF={stats['profit_factor']} "
                f"H1/H2=${stats['h1_pnl_dollars']:.2f}/${stats['h2_pnl_dollars']:.2f} "
                f"maxDD=${stats['max_drawdown_dollars']:.2f} "
                f"break_even_RT=${stats['break_even_round_trip_cost_dollars']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
