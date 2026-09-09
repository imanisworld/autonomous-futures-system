#!/usr/bin/env python3
"""Evidence-only MES 1-2-2 candidate-fallback counterfactual.

This does not replay bars or change runtime behavior. It reuses the exact
candidate outcomes from PR #373 and asks a narrower question:

If candidate fallback had been allowed only after a higher-ranked candidate
failed ENTRY_DETACHED_FROM_PRICE, what would the already-known MES strat_122
outcomes have looked like?

The script deliberately reports three treatments separately:
  1. current production-executable rows (control),
  2. ENTRY_DETACHED_FROM_PRICE fallback only -- the behavior the existing
     candidate-loop fallback can express before the permission gate,
  3. permission-blocked rows only, and all seven preemptions, as diagnostics.

No strategy rules, stops, targets, fills, risk gates, ranking, permissions,
config, broker code, or deployment are changed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "mes_122_fallback_counterfactual_source_2026-09-08.json"


def _max_drawdown(pnls: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _metrics(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda row: row["date"])
    pnls = [float(row["pnl"]) for row in ordered]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    midpoint = len(pnls) // 2
    return {
        "n": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "net": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if wins and losses else None,
        "h1_net": round(sum(pnls[:midpoint]), 2),
        "h2_net": round(sum(pnls[midpoint:]), 2),
        "max_drawdown": _max_drawdown(pnls),
        "dates": [row["date"] for row in ordered],
    }


def analyze(source: Path = SOURCE) -> dict:
    data = json.loads(source.read_text())
    provenance = data["provenance"]
    executable = list(data["executable_rows"])
    preempted = list(data["preempted_rows"])

    if provenance["executable_count"] != len(executable):
        raise ValueError("source executable_count does not match snapshot")
    if provenance["preempted_count"] != len(preempted):
        raise ValueError("source preempted_count does not match snapshot")
    if provenance["known_candidate_count"] != (
        provenance["executable_count"]
        + provenance["preempted_count"]
        + provenance["blocked_no_engine_decision_count"]
    ):
        raise ValueError("source population buckets do not sum to known_candidate_count")

    entry_detached = [
        row for row in preempted
        if row.get("failed_gates") == ["ENTRY_DETACHED_FROM_PRICE"]
    ]
    permission = [
        row for row in preempted
        if "STRATEGY_NOT_PAPER_ELIGIBLE" in row.get("failed_gates", [])
    ]

    control = _metrics(executable)
    existing_candidate_fallback = _metrics(executable + entry_detached)
    permission_only = _metrics(executable + permission)
    all_preemptions = _metrics(executable + preempted)

    # Reproduce PR #373 before publishing any treatment number.
    if control != {
        "n": 16,
        "wins": 5,
        "losses": 11,
        "net": 120.0,
        "profit_factor": 1.421053,
        "h1_net": 11.25,
        "h2_net": 108.75,
        "max_drawdown": 121.25,
        "dates": [row["date"] for row in sorted(executable, key=lambda row: row["date"])],
    }:
        raise AssertionError("PR #373 executable control no longer reproduces")

    return {
        "provenance": provenance,
        "control_current_executable": control,
        "entry_detached_fallback_only": {
            "recovered_rows": entry_detached,
            "metrics": existing_candidate_fallback,
        },
        "permission_blocked_only_diagnostic": {
            "recovered_rows": permission,
            "metrics": permission_only,
        },
        "all_preemptions_diagnostic": {
            "recovered_rows": preempted,
            "metrics": all_preemptions,
        },
        "finding": (
            "The material historical improvement comes from candidate-loop fallback after "
            "ENTRY_DETACHED_FROM_PRICE on the higher-ranked competing setup, not from the "
            "later strategy-permission denial path. This is a counterfactual only; a full "
            "engine replay is required before any runtime/config change."
        ),
    }


def main() -> None:
    print(json.dumps(analyze(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
