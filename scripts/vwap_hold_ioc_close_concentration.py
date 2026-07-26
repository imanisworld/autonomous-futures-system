#!/usr/bin/env python3
"""VWAP-hold IOC-close re-scoring: independent verification + winner-concentration
addendum (2026-07-26).

Operator decision being implemented (verbatim): "VWAP Hold IOC reference price:
choose `close` as canonical. Reason: that matches the existing production/replay
call sites. The evidence should conform to the executable system rather than
invent a separate `open` convention. Do not change runtime to match the old
study." Authorized action (verbatim): "rerun/re-score the existing locked
348-arm Hold study using arrival-bar close as the IOC marketability reference;
same population, same exits, same costs; no rule changes."

This script does NOT reimplement the fill or exit model. It imports the exact,
unmodified fill-determination and exit-resolution functions from
scripts/vwap_hold_evidence_package.py (which itself imports load_arms/load_bars
from scripts/vwap_hold_paired_fill_comparison.py, the PR #283/#307 frozen-
population loader) and:

  1. Independently re-derives the 348-arm population and its sha256 fingerprint,
     asserting it matches the PR #283/#307 frozen manifest exactly.
  2. Independently recomputes the ioc_close entry x {static, runner,
     partial_2ct_approx} x {1,2,3}-tick matrix and asserts every field matches
     scripts/vwap_hold_evidence_package_results.json's matrix["ioc_close"]
     byte-for-byte (raises loudly on any mismatch instead of silently
     accepting one).
  3. Emits per-trade (chronologically ordered) net-P&L records for each of the
     9 ioc_close cells — the one thing the aggregate JSON does not carry —
     and computes top-1/top-3/top-5 winner concentration (dollar contribution
     and % share of net P&L), matching this repo's existing convention (see
     docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md "Top-N winner
     concentration").
  4. Computes a max-drawdown-vs-expectancy ratio per cell, matching the same
     document's "Is max drawdown controlled relative to expectancy?" framing.

scripts/vwap_hold_evidence_package_results.json is NOT modified by this script
(read-only import + independent recomputation only). Output is written to
scripts/vwap_hold_ioc_close_concentration_results.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.vwap_hold_evidence_package import (  # noqa: E402
    ioc_fill,
    resolve_via_broker,
    cell_metrics,
    max_drawdown,
    COMMISSION_RT,
)
from scripts.vwap_hold_paired_fill_comparison import load_arms, load_bars, fingerprint  # noqa: E402

TICK_VALUE_MNQ = 0.50
EXPECTED_N = 348
EXPECTED_SHA256 = "18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880"
EXISTING_RESULTS_PATH = REPO / "scripts" / "vwap_hold_evidence_package_results.json"


def load_existing_matrix() -> dict:
    return json.loads(EXISTING_RESULTS_PATH.read_text())


def top_n_concentration(rows: list[dict], n: int) -> dict:
    """rows: per-trade dicts with 'pnl_net' and identifying fields, for
    RESOLVED trades only (matches cell_metrics' 'resolved' population, i.e.
    the denominator for 'share of net P&L' is the cell's net_pnl, which sums
    over resolved trades only -- unfilled arms contribute $0 and are already
    excluded upstream)."""
    winners = sorted((r for r in rows if r["pnl_net"] > 0), key=lambda r: -r["pnl_net"])
    total_net = sum(r["pnl_net"] for r in rows)
    top = winners[:n]
    contribution = sum(r["pnl_net"] for r in top)
    return {
        "n": n,
        "trades": [
            {"bar_ts": r["bar_ts"], "direction": r["direction"], "session": r["session"],
             "pnl_net": round(r["pnl_net"], 2)}
            for r in top
        ],
        "contribution": round(contribution, 2),
        "share_of_net_pnl": round(contribution / total_net, 4) if total_net else None,
    }


def main() -> None:
    # 1. independent population re-derivation + fingerprint assertion
    arms = load_arms()
    fp = fingerprint(arms)
    assert len(arms) == EXPECTED_N, f"population size drift: got {len(arms)}, expected {EXPECTED_N}"
    assert fp == EXPECTED_SHA256, f"population fingerprint drift: got {fp}, expected {EXPECTED_SHA256}"
    n = len(arms)

    existing = load_existing_matrix()
    assert existing["population_n"] == n, "existing JSON population_n mismatch"

    # 2. independent ioc_close fill determination (unchanged, imported function)
    fill_rows = []
    for arm in arms:
        bars = load_bars(arm["armed_at"].date().isoformat())
        fill_rows.append({**arm, **ioc_fill(arm, bars, "close")})

    filled_count = sum(1 for r in fill_rows if r["status"] == "FILLED")

    exit_modes = ("static", "runner")
    resolved_rows: dict[str, list[dict]] = {}
    for exit_mode in exit_modes:
        out = []
        for arm, fr in zip(arms, fill_rows):
            if fr["status"] != "FILLED":
                out.append({**arm, "status": fr["status"], "outcome": "NO_FILL", "pnl_gross": 0.0})
                continue
            bars = load_bars(arm["armed_at"].date().isoformat())
            res = resolve_via_broker(arm, fr["fill_price"], fr["fill_ts"], bars, exit_mode)
            out.append({**arm, "status": "FILLED", "outcome": res["outcome"], "pnl_gross": res["pnl"]})
        resolved_rows[exit_mode] = out

    partial_rows = [
        {**s, "pnl_gross": (s["pnl_gross"] + r["pnl_gross"]) if s["status"] == "FILLED" else 0.0}
        for s, r in zip(resolved_rows["static"], resolved_rows["runner"])
    ]
    resolved_rows["partial_2ct_approx"] = partial_rows

    mismatches = []
    report: dict = {
        "operator_decision": (
            "VWAP Hold IOC reference price: choose close as canonical (matches "
            "production/replay call sites webhook/runner.py, execution/mnq_strat_evidence.py, "
            "replay/replay_engine.py). Do not change runtime. Re-score the existing locked "
            "348-arm Hold study using arrival-bar close; same population, same exits, same costs; "
            "no rule changes."
        ),
        "population": {
            "n": n, "sha256": fp,
            "matches_pr283_pr307_frozen_manifest": True,
            "filled_ioc_close": filled_count,
            "fill_rate_ioc_close": round(filled_count / n, 4),
        },
        "cells": {},
    }

    for exit_name, rows in resolved_rows.items():
        report["cells"][exit_name] = {}
        for cost_ticks in (1, 2, 3):
            cost = COMMISSION_RT + cost_ticks * TICK_VALUE_MNQ
            resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
            per_trade = [
                {"bar_ts": r["bar_ts"], "direction": r["direction"], "session": r["session"],
                 "outcome": r["outcome"], "pnl_net": r["pnl_gross"] - cost}
                for r in resolved
            ]
            metrics = cell_metrics(rows, n, cost_ticks)

            # cross-check against the existing (already-committed) results JSON
            existing_cell = existing["matrix"]["ioc_close"][exit_name][f"{cost_ticks}_tick"]
            for key in ("armed", "filled", "resolved", "net_pnl", "net_expectancy_per_armed_signal",
                        "net_expectancy_per_fill", "profit_factor", "win_rate", "max_drawdown",
                        "positive_both_halves"):
                if metrics.get(key) != existing_cell.get(key):
                    mismatches.append({
                        "exit": exit_name, "cost_ticks": cost_ticks, "field": key,
                        "recomputed": metrics.get(key), "existing_json": existing_cell.get(key),
                    })

            dd = metrics["max_drawdown"]
            exp_per_fill = metrics["net_expectancy_per_fill"]
            net_pnl = metrics["net_pnl"]
            report["cells"][exit_name][f"{cost_ticks}_tick"] = {
                **metrics,
                "max_drawdown_vs_expectancy_per_fill_ratio": (
                    round(dd / exp_per_fill, 2) if exp_per_fill and exp_per_fill > 0 else None
                ),
                "max_drawdown_vs_net_pnl_pct": (
                    round(dd / net_pnl, 4) if net_pnl else None
                ),
                "top_1": top_n_concentration(per_trade, 1),
                "top_3": top_n_concentration(per_trade, 3),
                "top_5": top_n_concentration(per_trade, 5),
            }

    report["cross_check_against_committed_evidence_package_json"] = {
        "source": "scripts/vwap_hold_evidence_package_results.json (untouched, read-only)",
        "fields_checked_per_cell": [
            "armed", "filled", "resolved", "net_pnl", "net_expectancy_per_armed_signal",
            "net_expectancy_per_fill", "profit_factor", "win_rate", "max_drawdown",
            "positive_both_halves",
        ],
        "mismatches": mismatches,
        "verdict": "EXACT MATCH on every field, every cell" if not mismatches else "MISMATCH FOUND — see 'mismatches'",
    }

    out_path = Path(__file__).parent / "vwap_hold_ioc_close_concentration_results.json"
    out_path.write_text(json.dumps(report, indent=1, default=str))
    print(f"wrote {out_path}")
    print(f"cross-check: {report['cross_check_against_committed_evidence_package_json']['verdict']}")
    if mismatches:
        print(json.dumps(mismatches, indent=2))
        sys.exit(1)

    print("\n=== ioc_close concentration summary (2-tick baseline) ===")
    for exit_name in ("static", "runner", "partial_2ct_approx"):
        c = report["cells"][exit_name]["2_tick"]
        print(f"{exit_name:20s} net=${c['net_pnl']:>9.2f} exp/armed=${c['net_expectancy_per_armed_signal']:>7.4f} "
              f"PF={c['profit_factor']} maxDD=${c['max_drawdown']} DD/expPerFill={c['max_drawdown_vs_expectancy_per_fill_ratio']} "
              f"top1={c['top_1']['share_of_net_pnl']} top3={c['top_3']['share_of_net_pnl']} top5={c['top_5']['share_of_net_pnl']}")


if __name__ == "__main__":
    main()
