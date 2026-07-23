#!/usr/bin/env python3
"""Isolated VWAP-hold fill-model comparison (2026-07-23, operator-locked spec).

Research only. No production edits, rule-document rewrites, configuration
changes, broker actions, deployments, or automatic strategy reclassification.

This does NOT redesign the fill models verified in PR #283
(scripts/vwap_hold_paired_fill_comparison.py) — it reuses that exact,
already-reproduced code path (same PaperBroker calls, same arm loader, same
cost model) and adds the five things the operator required to be locked
before the result can be trusted as an isolated test:

  1. Freeze the exact 348-signal population — verified byte-identical
     (sha256) against the PR #283 fingerprint, then materialized to a
     manifest file. No arm is regenerated through current strategy code:
     `load_arms()` only reads persisted historical journal rows written in
     the 622-day replay batch, never invokes strategy/signal_engine.py.
  2/3. Define the two fill models precisely (below and in the accompanying
     doc) — extracted directly from execution/paper_broker.py source, not
     re-derived or changed.
  4. Freeze proximity-gate behavior — the primary comparison carries the
     production-default DISABLED state (config/settings.py
     vwap_entry_max_distance_ticks default = 0.0 = "never gates"; the field
     was introduced default-off in PR #92 on 2026-06-26 and the only attempt
     to enable it, PR #95, was closed unmerged — so it has never been ON for
     any day in this arm population's 2024-07-02..2026-06-25 span). This
     script applies NO proximity filter to either leg. A sensitivity variant
     is out of scope here per the operator's instruction to keep it separate.
  5. Primary metric = net expectancy PER ARMED SIGNAL (net $ / n=348,
     unfilled arms contribute $0 to the numerator and 1 to the denominator).
     Expectancy per fill (per resolved trade) is reported alongside but is
     NOT the verdict metric — it structurally favors whichever leg rejects
     more trades.

Fill-model definitions (source: execution/paper_broker.py, verified against
the running code, not paraphrased from memory):

IOC-limit leg ("old_ioc", entry_fill_model="ioc_limit"):
  - Limit price: order.entry + tol (LONG) or order.entry - tol (SHORT),
    where tol = ENTRY_TOLERANCE_TICKS_MNQ (32 ticks = 8.0 pts, the live pin)
    converted to price.
  - Reference/marketability check: a SINGLE snapshot price, `market_price`,
    passed once at order-arrival time. This script (matching PR #283) passes
    the arrival bar's OPEN — not its close, despite the broker docstring
    saying "close"; that discrepancy is inherited from the verified script
    and stated here rather than silently changed.
  - Time-in-force / validity duration: effectively instantaneous. There is
    no persistence across bars and no check against the bar's high/low
    range — only the one reference price is tested. Unmarketable at that
    instant => immediate self-cancel (CANCELLED / ENTRY_NOT_FILLED), exactly
    once, no retry.
  - Touch vs trade-through: neither in the usual sense — it is a direct
    comparison of the single reference price against the tolerance-capped
    limit (LONG unmarketable if market > limit_px; SHORT if market < limit_px).
  - Fill price if marketable: the better of (reference price, limit price)
    — min(limit_px, market) for LONG, max(limit_px, market) for SHORT.
    slippage_ticks=0.0 in this comparison, so no additional slippage is
    layered onto the IOC fill price itself (tolerance already prices in the
    adverse room).
  - Partial fills: none. Fill is all-or-nothing for the full 1-contract size.
  - Later-bar fill: impossible — the check happens once, at the arrival bar.

Market leg ("new_market", the tranche-1 fill mechanism):
  - Decision timestamp: armed_at = signal bar_ts + 15 minutes (the time the
    setup is actually actionable, matching the live alert-to-decision lag).
  - First executable price: the open of the first 5-minute bar at/after
    armed_at.
  - Gap handling: if that open is already at/through the entry level
    (gap >= 0 in the direction of the trade), fills immediately at
    open + 1 adverse tick — this is the gap-through case, using only the
    bar's open, never the unknowable intrabar path that preceded it.
  - If the open has not yet reached the level (gap < 0): the order rests
    and fills on the first subsequent bar within a 20-minute window whose
    high (LONG) or low (SHORT) touches the level; fill price = level + 1
    adverse tick. This IS a touch-based fill (uses the bar's high/low to
    know a touch occurred) — that is a legitimate use of OHLC data (whether
    a level was reached during a bar is knowable after the fact) and is
    distinct from claiming knowledge of intrabar SEQUENCING, which this
    model never uses (it does not decide fill-vs-stop-first within one bar
    for the ENTRY leg; that pessimistic-both-hit logic is reserved for
    EXIT-bar resolution in PaperBroker.resolve_position).
  - No fill within the 20-minute touch window => NO_FILL.
  - Slippage: exactly 1 tick adverse, embedded once in the fill price
    itself, applied identically on both the gap-fill and touch-fill paths.
  - Partial fills: none.

Cost overlay (both legs, applied uniformly at settlement, not a fill-model
difference): $1.24 commission round-turn + 2 ticks round-turn slippage
($2.24/contract MNQ). This is a blanket transaction-cost tax layered on top
of whatever price the fill model produced — it is not additional entry
slippage and does not double-count the market leg's embedded 1-tick entry
slippage (that tick prices where the order would realistically fill; the
round-turn overlay separately prices commission + bid/ask on both legs of
every resolved trade, applied equally to both fill-model legs for a fair
comparison).

Wording note (per operator instruction): this script does not decide, and
this report does not conclude, whether vwap_hold or vwap_rejection should be
retired, merged, or redesigned. That determination is explicitly deferred to
the follow-on overlap audit.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path

REPO = Path("/Users/djb.a.e/MAINVSCODE/autonomous-futures-system")
sys.path.insert(0, str(REPO))

from scripts.vwap_hold_paired_fill_comparison import (  # noqa: E402
    COST_RT,
    fingerprint,
    load_arms,
    run_leg,
)

FROZEN_SHA256 = "18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880"
PROXIMITY_GATE_TICKS_DEFAULT = 0.0  # config/settings.py — 0 = disabled ("never gates")


def build_manifest(arms: list[dict]) -> dict:
    fp = fingerprint(arms)
    if fp != FROZEN_SHA256:
        raise AssertionError(
            f"Arm population drifted from the PR #283 frozen fingerprint: "
            f"got {fp}, expected {FROZEN_SHA256}. STOP — do not proceed with "
            f"a comparison over a population that no longer matches the "
            f"frozen n=348 set."
        )
    return {
        "n": len(arms),
        "sha256": fp,
        "sha256_matches_pr283_frozen_population": True,
        "fingerprint_definition": "sha256 over sorted (bar_ts, direction, entry, stop, target) tuples",
        "source": "logs/retest_baseline_off/MNQ/journal_*.jsonl, decision==TRADE and risk_check.result==APPROVED and setup.strategy==vwap_hold",
        "regeneration_note": "read-only over persisted historical journal rows; never invokes strategy/signal_engine.py",
        "proximity_gate_ticks": PROXIMITY_GATE_TICKS_DEFAULT,
        "proximity_gate_state": "DISABLED (production default; field has been off for this population's entire 2024-07-02..2026-06-25 span)",
        "directions": sorted({a["direction"] for a in arms}),
        "first_bar_ts": arms[0]["bar_ts"],
        "last_bar_ts": arms[-1]["bar_ts"],
        "rows": [
            {"bar_ts": a["bar_ts"], "direction": a["direction"], "entry": a["entry"],
             "stop": a["stop"], "target": a["target"], "session": a["session"]}
            for a in arms
        ],
    }


def summarize_isolated(rows: list[dict], n_armed: int) -> dict:
    """Primary metric = net expectancy per armed signal (n_armed denominator,
    unfilled arms contribute $0). Per-fill expectancy reported as secondary."""
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls_after_cost = [r["pnl"] - COST_RT for r in resolved]
    net_after_cost = sum(pnls_after_cost)
    wins = [p for p in pnls_after_cost if p > 0]
    losses = [p for p in pnls_after_cost if p < 0]
    return {
        "n_armed": n_armed,
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "resolved": len(resolved),
        "net_after_cost": round(net_after_cost, 2),
        "PRIMARY_expectancy_per_armed_signal": round(net_after_cost / n_armed, 4) if n_armed else None,
        "secondary_expectancy_per_fill": round(statistics.fmean(pnls_after_cost), 2) if pnls_after_cost else None,
        "win_rate_of_fills": round(len(wins) / len(resolved), 3) if resolved else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
    }


def main() -> None:
    arms = load_arms()
    manifest = build_manifest(arms)
    manifest_path = Path(__file__).parent / "vwap_hold_isolated_fill_model_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"FROZEN population verified: n={manifest['n']} sha256={manifest['sha256']}")
    print(f"proximity gate: {manifest['proximity_gate_state']}")
    print(f"manifest written: {manifest_path}")

    old_rows = run_leg(arms, "old_ioc")
    new_rows = run_leg(arms, "new_market")
    n = len(arms)

    result = {
        "manifest": {k: v for k, v in manifest.items() if k != "rows"},
        "manifest_path": str(manifest_path.name),
        "old_ioc": summarize_isolated(old_rows, n),
        "new_market": summarize_isolated(new_rows, n),
        "note": (
            "PRIMARY_expectancy_per_armed_signal is the decision metric per "
            "operator lock #5. secondary_expectancy_per_fill is reported but "
            "is NOT the verdict metric. No retire/redesign decision is made "
            "here for vwap_hold or vwap_rejection."
        ),
    }
    out_path = Path(__file__).parent / "vwap_hold_isolated_fill_model_comparison_results.json"
    out_path.write_text(json.dumps(result, indent=1))

    for name in ("old_ioc", "new_market"):
        s = result[name]
        print(
            f"{name:9s} filled={s['filled']:3d}/{s['n_armed']} "
            f"net_after_cost=${s['net_after_cost']:>9.2f} "
            f"PRIMARY(per-armed)=${s['PRIMARY_expectancy_per_armed_signal']:>7.4f} "
            f"secondary(per-fill)=${s['secondary_expectancy_per_fill']} "
            f"WR={s['win_rate_of_fills']} PF={s['profit_factor']}"
        )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
