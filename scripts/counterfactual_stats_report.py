#!/usr/bin/env python3
"""Aggregate already-produced counterfactual rows without re-running research logic.

This script deliberately does NOT recreate detector replay, regime substitution,
market-condition substitution, fill simulation, sample splitting, or trade
ordering. Those decisions belong to the audited study that produced the rows.
The reporter only aggregates explicit provenance and performance fields.

Input JSONL contract (one candidate per row):
  cohort: str                 required (e.g. A, B, C, D0, D1, D2)
  sample_half: H1|H2          required; assigned by the audited study producer
  sequence: int >= 0          required; unique within cohort, used for drawdown
  ts: offset-aware ISO-8601   required provenance timestamp
  filled: bool                required; means entry filled, not necessarily terminal
  result: WIN|LOSS|NO_FILL|EXPIRED
                              optional for legacy rows; required for EXPIRED
  pnl_dollars: number|null    required number for terminal WIN/LOSS rows;
                              null for NO_FILL and EXPIRED
  mae_r: number|null          optional, non-negative when present
  mfe_r: number|null          optional, non-negative when present

The reporter never assigns a candidate to a cohort, sample half, or performance
order. Missing half coverage is surfaced explicitly instead of being silently
interpreted as a zero-result half. Filled-but-unresolved EXPIRED rows are
counted separately and excluded from terminal P&L, PF, WR, drawdown, MAE/MFE,
and hypothetical-cost calculations, matching the preserved 2026-09-16 study.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_EXPLICIT_RESULTS = {"WIN", "LOSS", "NO_FILL", "EXPIRED"}
_TERMINAL_RESULTS = {"WIN", "LOSS", "TERMINAL"}


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


def _sequence(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    return value


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
    seen_sequences: dict[str, set[int]] = defaultdict(set)
    for index, raw in enumerate(rows, start=1):
        cohort = str(raw.get("cohort") or "").strip()
        if not cohort:
            raise ValueError(f"row {index}: cohort is required")
        sample_half = str(raw.get("sample_half") or "").strip().upper()
        if sample_half not in {"H1", "H2"}:
            raise ValueError(f"row {index}: sample_half must be H1 or H2")
        sequence = _sequence(raw.get("sequence"), label=f"row {index} sequence")
        if sequence in seen_sequences[cohort]:
            raise ValueError(
                f"row {index}: duplicate sequence {sequence} in cohort {cohort}"
            )
        seen_sequences[cohort].add(sequence)
        if not isinstance(raw.get("filled"), bool):
            raise ValueError(f"row {index}: filled must be boolean")

        row = dict(raw)
        row["cohort"] = cohort
        row["sample_half"] = sample_half
        row["sequence"] = sequence
        row["_ts"] = _timestamp(raw.get("ts"), label=f"row {index} ts")

        explicit_result = raw.get("result")
        if explicit_result is None:
            # Backward-compatible interpretation for #592-era rows, which had
            # no result field and could only represent terminal or no-fill.
            if row["filled"]:
                if row.get("pnl_dollars") is None:
                    raise ValueError(
                        f"row {index}: filled row without result requires pnl_dollars"
                    )
                row["_result"] = "TERMINAL"
            else:
                if row.get("pnl_dollars") is not None:
                    raise ValueError(
                        f"row {index}: no-fill row must not carry pnl_dollars"
                    )
                row["_result"] = "NO_FILL"
        else:
            result = str(explicit_result).strip().upper()
            if result not in _EXPLICIT_RESULTS:
                raise ValueError(
                    f"row {index}: result must be WIN, LOSS, NO_FILL, or EXPIRED"
                )
            row["result"] = result
            row["_result"] = result
            if result in {"WIN", "LOSS"}:
                if row["filled"] is not True:
                    raise ValueError(f"row {index}: {result} row must have filled=true")
                if row.get("pnl_dollars") is None:
                    raise ValueError(f"row {index}: {result} row requires pnl_dollars")
            elif result == "NO_FILL":
                if row["filled"] is not False:
                    raise ValueError(f"row {index}: NO_FILL row must have filled=false")
                if row.get("pnl_dollars") is not None:
                    raise ValueError(f"row {index}: NO_FILL row must have null pnl_dollars")
            elif result == "EXPIRED":
                if row["filled"] is not True:
                    raise ValueError(f"row {index}: EXPIRED row must have filled=true")
                if row.get("pnl_dollars") is not None:
                    raise ValueError(f"row {index}: EXPIRED row must have null pnl_dollars")

        if row["_result"] in _TERMINAL_RESULTS:
            row["pnl_dollars"] = _number(
                row.get("pnl_dollars"), label=f"row {index} pnl_dollars"
            )
            if row["_result"] == "WIN" and row["pnl_dollars"] < 0:
                raise ValueError(f"row {index}: WIN row cannot have negative pnl_dollars")
            if row["_result"] == "LOSS" and row["pnl_dollars"] > 0:
                raise ValueError(f"row {index}: LOSS row cannot have positive pnl_dollars")

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
    ordered = sorted(rows, key=lambda row: row["sequence"])
    terminal_rows = [row for row in ordered if row["_result"] in _TERMINAL_RESULTS]
    expired_rows = [row for row in ordered if row["_result"] == "EXPIRED"]
    no_fill_rows = [row for row in ordered if row["_result"] == "NO_FILL"]
    pnls = [float(row["pnl_dollars"]) for row in terminal_rows]
    gross = round(sum(pnls), 2)
    fills = len(terminal_rows)  # preserved reference meaning: terminal filled trades
    wins = sum(value > 0 for value in pnls)
    losses = sum(value < 0 for value in pnls)
    breakeven_cost = round(gross / fills, 4) if fills else None
    mae = [float(row["mae_r"]) for row in terminal_rows if row.get("mae_r") is not None]
    mfe = [float(row["mfe_r"]) for row in terminal_rows if row.get("mfe_r") is not None]
    h1_rows = [row for row in ordered if row["sample_half"] == "H1"]
    h2_rows = [row for row in ordered if row["sample_half"] == "H2"]
    h1_terminal = [row for row in h1_rows if row["_result"] in _TERMINAL_RESULTS]
    h2_terminal = [row for row in h2_rows if row["_result"] in _TERMINAL_RESULTS]
    h1_expired = [row for row in h1_rows if row["_result"] == "EXPIRED"]
    h2_expired = [row for row in h2_rows if row["_result"] == "EXPIRED"]
    halves_present = sorted({row["sample_half"] for row in ordered})
    costs = _validated_costs(hypothetical_costs)
    return {
        "candidates": len(ordered),
        "fills": fills,
        "terminal_fills": fills,
        "entry_filled_total": fills + len(expired_rows),
        "expired_open": len(expired_rows),
        "no_fills": len(no_fill_rows),
        "fill_rate_percent": round((fills / len(ordered)) * 100.0, 2),
        "entry_fill_rate_percent": round(
            ((fills + len(expired_rows)) / len(ordered)) * 100.0, 2
        ),
        "wins": wins,
        "losses": losses,
        "breakevens": sum(value == 0 for value in pnls),
        "win_rate_percent": round((wins / fills) * 100.0, 2) if fills else None,
        "gross_pnl_dollars": gross,
        "profit_factor": _profit_factor(pnls),
        "sample_halves_present": halves_present,
        "half_coverage_complete": halves_present == ["H1", "H2"],
        "h1_candidates": len(h1_rows),
        "h2_candidates": len(h2_rows),
        "h1_fills": len(h1_terminal),
        "h2_fills": len(h2_terminal),
        "h1_expired_open": len(h1_expired),
        "h2_expired_open": len(h2_expired),
        "h1_pnl_dollars": round(
            sum(float(row["pnl_dollars"]) for row in h1_terminal), 2
        ),
        "h2_pnl_dollars": round(
            sum(float(row["pnl_dollars"]) for row in h2_terminal), 2
        ),
        "worst_trade_dollars": round(min(pnls), 2) if pnls else None,
        "gross_max_drawdown_dollars": _max_drawdown(pnls),
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
        "performance_order_source": "input.sequence",
        "timestamp_role": "provenance_only",
        "performance_basis": "terminal_gross_before_hypothetical_costs",
        "expired_semantics": "filled-but-unresolved rows counted separately and excluded from terminal performance",
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
            coverage = "complete" if stats["half_coverage_complete"] else "INCOMPLETE"
            print(
                f"{cohort}: fills={stats['fills']}/{stats['candidates']} "
                f"expired={stats['expired_open']} "
                f"no_fill={stats['no_fills']} "
                f"gross=${stats['gross_pnl_dollars']:.2f} "
                f"PF={stats['profit_factor']} "
                f"H1/H2=${stats['h1_pnl_dollars']:.2f}/${stats['h2_pnl_dollars']:.2f} "
                f"half_coverage={coverage} "
                f"gross_maxDD=${stats['gross_max_drawdown_dollars']:.2f} "
                f"break_even_RT=${stats['break_even_round_trip_cost_dollars']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
