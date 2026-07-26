#!/usr/bin/env python3
"""VWAP-hold IOC-close re-scoring: independent verification + winner-concentration
addendum (2026-07-26, amended 2026-07-26 for the NY-only canonical-population gap).

Operator decision being implemented (verbatim): "VWAP Hold IOC reference price:
choose `close` as canonical. Reason: that matches the existing production/replay
call sites. The evidence should conform to the executable system rather than
invent a separate `open` convention. Do not change runtime to match the old
study." Authorized action (verbatim): "rerun/re-score the existing locked
348-arm Hold study using arrival-bar close as the IOC marketability reference;
same population, same exits, same costs; no rule changes."

**Amendment (same day, post-PR-#345-review)**: the strategy being classified
is "VWAP Hold — MNQ NY" — `risk_rules.yaml`'s global `allowed_sessions:
[new_york]` gate applies to every strategy live, so only the New York-session
subset of the 348-arm population was ever live-eligible under current config.
The original version of this script reported only the blended 348-arm
(london+new_york+asian) matrix as the headline. This version computes BOTH:
  - `blended_348`: the full population, kept and clearly labeled as
    robustness/provenance context only — NOT the canonical live-relevant
    evidence.
  - `ny_only`: the `session == "new_york"` subset (n=107 armed), the
    CANONICAL evidence for "VWAP Hold — MNQ NY" classification purposes.
  - `non_ny_context`: the london+asian complement (n=241), reported only as
    supplementary context to show what was diluting/inflating the blended
    number (not a deliverable in its own right).
A partition-integrity check asserts ny_only + non_ny_context arms exactly
reconstruct the blended 348 population with no overlap and no loss.

This script does NOT reimplement the fill or exit model. It imports the exact,
unmodified fill-determination and exit-resolution functions from
scripts/vwap_hold_evidence_package.py (which itself imports load_arms/load_bars
from scripts/vwap_hold_paired_fill_comparison.py, the PR #283/#307 frozen-
population loader) and:

  1. Independently re-derives the 348-arm population and its sha256 fingerprint,
     asserting it matches the PR #283/#307 frozen manifest exactly.
  2. Independently recomputes the ioc_close entry x {static, runner,
     partial_2ct_approx} x {1,2,3}-tick matrix for the BLENDED population and
     asserts every field matches
     scripts/vwap_hold_evidence_package_results.json's matrix["ioc_close"]
     byte-for-byte (raises loudly on any mismatch instead of silently
     accepting one).
  3. Computes the identical 9-cell matrix for the NY-only subset (no existing
     JSON to cross-check against — this is new evidence, not a re-derivation
     of something already committed).
  4. Emits per-trade (chronologically ordered) net-P&L records for every cell
     in both populations and computes top-1/top-3/top-5 winner concentration
     (dollar contribution and % share of net P&L), matching this repo's
     existing convention (see
     docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md "Top-N winner
     concentration").
  5. Computes max-drawdown-vs-expectancy and max-drawdown-vs-net-P&L ratios
     per cell, matching the same document's "Is max drawdown controlled
     relative to expectancy?" framing.

Does NOT choose or recommend a canonical exit mode (static/runner/partial) —
that question stays open, exactly as before this amendment.

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


def compute_ioc_close_report(arms: list[dict], *, cross_check: dict | None = None) -> dict:
    """Full ioc_close entry x {static, runner, partial_2ct_approx} x
    {1,2,3}-tick matrix for an arbitrary arm population, reusing the
    unmodified fill/exit-resolution functions. If cross_check is given (the
    parsed existing evidence-package JSON), each cell is asserted to match it
    field-for-field and mismatches are recorded rather than silently
    swallowed."""
    n = len(arms)

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

    mismatches: list[dict] = []
    report: dict = {
        "population": {
            "n": n,
            "filled_ioc_close": filled_count,
            "fill_rate_ioc_close": round(filled_count / n, 4) if n else None,
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

            if cross_check is not None:
                existing_cell = cross_check["matrix"]["ioc_close"][exit_name][f"{cost_ticks}_tick"]
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

    if cross_check is not None:
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

    return report


def main() -> None:
    # 1. independent population re-derivation + fingerprint assertion
    arms = load_arms()
    fp = fingerprint(arms)
    assert len(arms) == EXPECTED_N, f"population size drift: got {len(arms)}, expected {EXPECTED_N}"
    assert fp == EXPECTED_SHA256, f"population fingerprint drift: got {fp}, expected {EXPECTED_SHA256}"

    existing = load_existing_matrix()
    assert existing["population_n"] == len(arms), "existing JSON population_n mismatch"

    ny_arms = [a for a in arms if a["session"] == "new_york"]
    non_ny_arms = [a for a in arms if a["session"] != "new_york"]

    # partition-integrity check: ny + non_ny must exactly reconstruct the
    # blended population, no overlap, no loss
    key = lambda a: (a["bar_ts"], a["direction"], a["entry"], a["stop"], a["target"])
    full_keys = sorted(key(a) for a in arms)
    split_keys = sorted(key(a) for a in ny_arms) + sorted(key(a) for a in non_ny_arms)
    assert sorted(split_keys) == full_keys, "NY/non-NY split does not exactly partition the blended population"
    assert len(ny_arms) + len(non_ny_arms) == len(arms), "NY/non-NY counts do not sum to blended n"

    from collections import Counter
    session_counts = dict(Counter(a["session"] for a in arms))

    print(f"blended population: n={len(arms)}, sha256={fp}")
    print(f"session breakdown: {session_counts}")
    print(f"NY-only: n={len(ny_arms)} ({len(ny_arms)/len(arms):.1%} of blended)")
    print(f"non-NY (london+asian): n={len(non_ny_arms)}")

    blended_report = compute_ioc_close_report(arms, cross_check=existing)
    ny_report = compute_ioc_close_report(ny_arms)
    non_ny_report = compute_ioc_close_report(non_ny_arms)

    top = {
        "operator_decision": (
            "VWAP Hold IOC reference price: choose close as canonical (matches "
            "production/replay call sites webhook/runner.py, execution/mnq_strat_evidence.py, "
            "replay/replay_engine.py). Do not change runtime. Re-score the existing locked "
            "348-arm Hold study using arrival-bar close; same population, same exits, same costs; "
            "no rule changes."
        ),
        "amendment_note": (
            "The strategy is 'VWAP Hold — MNQ NY' (Strategy_Inventory.md name; "
            "risk_rules.yaml allowed_sessions:[new_york] gates every strategy live). "
            "The blended 348-arm population is only 30.7% New York (107/348) — the "
            "remaining 69.3% (london+asian) could never have been live-eligible under "
            "current config. blended_348 below is kept as robustness/provenance context "
            "only. ny_only is the canonical, live-relevant evidence for the "
            "'VWAP Hold — MNQ NY' classification. non_ny_context is supplementary only."
        ),
        "population_fingerprint": {
            "blended_n": len(arms), "sha256": fp,
            "matches_pr283_pr307_frozen_manifest": True,
            "session_breakdown": session_counts,
            "ny_only_n": len(ny_arms),
            "ny_only_share_of_blended": round(len(ny_arms) / len(arms), 4),
            "non_ny_n": len(non_ny_arms),
            "partition_integrity_check": "PASS — ny_only + non_ny_context exactly reconstruct blended_348, no overlap, no loss",
        },
        "blended_348": blended_report,
        "ny_only": ny_report,
        "non_ny_context": non_ny_report,
    }

    out_path = Path(__file__).parent / "vwap_hold_ioc_close_concentration_results.json"
    out_path.write_text(json.dumps(top, indent=1, default=str))
    print(f"\nwrote {out_path}")
    print(f"blended cross-check: {blended_report['cross_check_against_committed_evidence_package_json']['verdict']}")
    if blended_report["cross_check_against_committed_evidence_package_json"]["mismatches"]:
        print(json.dumps(blended_report["cross_check_against_committed_evidence_package_json"]["mismatches"], indent=2))
        sys.exit(1)

    print("\n=== NY-ONLY ioc_close summary (2-tick baseline, CANONICAL) ===")
    for exit_name in ("static", "runner", "partial_2ct_approx"):
        c = ny_report["cells"][exit_name]["2_tick"]
        print(f"{exit_name:20s} armed={c['armed']:3d} filled={c['filled']:3d} resolved={c['resolved']:3d} "
              f"net=${c['net_pnl']:>9.2f} exp/armed=${c['net_expectancy_per_armed_signal']:>7.4f} "
              f"exp/fill=${c['net_expectancy_per_fill']:>7.2f} PF={c['profit_factor']} WR={c['win_rate']} "
              f"maxDD=${c['max_drawdown']} both_halves={c['positive_both_halves']} "
              f"top1={c['top_1']['share_of_net_pnl']} top3={c['top_3']['share_of_net_pnl']} top5={c['top_5']['share_of_net_pnl']}")

    print("\n=== BLENDED-348 ioc_close summary (2-tick baseline, robustness/provenance only) ===")
    for exit_name in ("static", "runner", "partial_2ct_approx"):
        c = blended_report["cells"][exit_name]["2_tick"]
        print(f"{exit_name:20s} net=${c['net_pnl']:>9.2f} exp/armed=${c['net_expectancy_per_armed_signal']:>7.4f} "
              f"PF={c['profit_factor']} both_halves={c['positive_both_halves']}")

    print("\n=== NON-NY (london+asian) ioc_close summary (2-tick baseline, context only) ===")
    for exit_name in ("static", "runner", "partial_2ct_approx"):
        c = non_ny_report["cells"][exit_name]["2_tick"]
        print(f"{exit_name:20s} net=${c['net_pnl']:>9.2f} exp/armed=${c['net_expectancy_per_armed_signal']:>7.4f} "
              f"PF={c['profit_factor']} both_halves={c['positive_both_halves']}")


if __name__ == "__main__":
    main()
