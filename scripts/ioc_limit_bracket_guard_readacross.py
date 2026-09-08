#!/usr/bin/env python3
"""Which lanes inherit PaperBroker's unguarded `ioc_limit` entry path?

Research only. `PaperBroker` rejects an entry that fills beyond its own stop or
target — `ENTRY_BRACKET_INVALID_AT_FILL` — but only on the `stop_market`
(resting) path. The `ioc_limit` path applies no such check, so a marketable IOC
whose reference price has run far to the *favourable* side books a position
whose static stop sits between the fill and the target. That "stop" then pays
out. See `docs/ioc-limit-bracket-guard-readacross-2026-09-08.md`.

This measures, per lane, how many IOC fills would be invalid at fill, and what
the phantom P&L is if each resolves on its own stop.

Both inputs are committed artifacts (`scripts/*.json*`), so this runs from a
fresh clone — it does not read the gitignored `data/replay_polygon_5m/`.

No strategy, risk, replay, broker, config or deployment change.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import statistics
from pathlib import Path

from context.mnq_orb_breakout_inverse_paper import mirror_order
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker

HERE = Path(__file__).parent
CANDIDATES = HERE / "edge_decomposition_audit_results_candidates.jsonl.gz"
INVERSE_BASELINE = HERE / "inverse_orb_canonical_ioc_proof_2026-09-07.json"

TICK = 0.25
POINT_VALUE = 2.0  # MNQ/MES: 0.25 tick = $0.50
COMMISSION_RT = 1.48
SLIPPAGE_TICKS = 1.0

# The wide-stop family the hypothetical-ledger lane spec covers, plus the
# inverse ORB lane for scale.
WIDE_STOP_LANES = ("4hr_mnq", "322_mnq", "miyagi_mnq")


def bracket_valid_at_fill(direction: str, fill: float, stop: float, target: float) -> bool:
    """The rule PaperBroker enforces for a resting entry and not for an IOC."""
    if direction == "LONG":
        return stop < fill < target
    return target < fill < stop


def _ioc_fill(order: BracketOrder, market: float, tolerance_ticks: float):
    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": tolerance_ticks, "MES": tolerance_ticks},
    )
    return broker.execute_bracket(order, market_price=float(market))


def _phantom_pnl(direction: str, fill: float, stop: float) -> float:
    """P&L booked if an invalid-bracket position resolves on its own stop."""
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (stop - fill) * POINT_VALUE - COMMISSION_RT


def _assess(name: str, orders: list[tuple[BracketOrder, float, dict]],
            tolerance_ticks: float) -> dict:
    filled = 0
    detachment: list[float] = []
    invalid: list[dict] = []
    for order, market, meta in orders:
        fill = _ioc_fill(order, market, tolerance_ticks)
        if fill.result == "CANCELLED":
            continue
        filled += 1
        entry_px = float(fill.entry_price)
        detachment.append(abs(entry_px - order.entry) / TICK)
        if bracket_valid_at_fill(order.direction, entry_px, order.stop, order.target):
            continue
        invalid.append({
            **meta,
            "direction": order.direction,
            "entry": order.entry,
            "fill": entry_px,
            "stop": order.stop,
            "detachment_ticks": round(abs(entry_px - order.entry) / TICK),
            "stop_distance_ticks": round(abs(order.entry - order.stop) / TICK),
            "rr": order.rr_ratio,
            "phantom_pnl_if_stop_hit": round(_phantom_pnl(order.direction, entry_px, order.stop), 2),
        })
    return {
        "lane": name,
        "candidates": len(orders),
        "ioc_filled": filled,
        "invalid_at_fill": len(invalid),
        "invalid_share": round(len(invalid) / filled, 3) if filled else None,
        "median_detachment_ticks": round(statistics.median(detachment), 1) if detachment else None,
        "max_detachment_ticks": round(max(detachment), 1) if detachment else None,
        "phantom_pnl_total": round(sum(r["phantom_pnl_if_stop_hit"] for r in invalid), 2),
        "invalid_rows": invalid,
    }


def _wide_stop_orders(lane: str) -> list[tuple[BracketOrder, float, dict]]:
    orders = []
    with gzip.open(CANDIDATES, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row["lane"] != lane:
                continue
            market = (row.get("gates") or {}).get("decision_close")
            if market is None:
                continue
            orders.append((
                BracketOrder(
                    instrument=row["instrument"], direction=row["direction"],
                    entry=row["entry"], stop=row["stop"], target=row["target"],
                    rr_ratio=row["rr"], strategy=row["strategy"], contracts=1,
                ),
                float(market),
                {"date": row["date"]},
            ))
    return orders


def _inverse_orb_orders() -> list[tuple[BracketOrder, float, dict]]:
    baseline = json.loads(INVERSE_BASELINE.read_text())
    orders = []
    for row in baseline["rows"]:
        source = BracketOrder(
            instrument="MNQ", strategy="orb_breakout", direction=row["source_direction"],
            entry=row["source_entry"], stop=row["source_stop"], target=row["source_target"],
            rr_ratio=2.2, min_rr_ratio=2.0, max_stop_ticks=120.0,
        )
        orders.append((mirror_order(source), float(row["fill_market"]), {"date": row["date"]}))
    return orders


def build_report(tolerance_ticks: float = 8.0) -> dict:
    lanes = [_assess(lane, _wide_stop_orders(lane), tolerance_ticks) for lane in WIDE_STOP_LANES]
    lanes.append(_assess("orb_breakout_inverse_mnq", _inverse_orb_orders(), tolerance_ticks))
    return {
        "question": (
            "Which lanes book an ioc_limit fill whose static bracket is invalid at "
            "that fill, because PaperBroker guards only its stop_market path?"
        ),
        "tolerance_ticks": tolerance_ticks,
        "slippage_ticks": SLIPPAGE_TICKS,
        "reference_price": "decision-bar close",
        "guarded_path": "PaperBroker.stop_market — ENTRY_BRACKET_INVALID_AT_FILL",
        "unguarded_path": "PaperBroker.ioc_limit — no bracket validity check",
        "lanes": lanes,
        "notes": [
            "Severity scales with detachment relative to stop distance, not with the "
            "IOC tolerance: the tolerance bounds only the adverse side.",
            "scripts/edge_decomposition_audit.py applies its own _bracket_valid_at_fill, "
            "so the audit cells these candidates come from already exclude these fills.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tolerance-ticks", type=float, default=8.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = build_report(args.tolerance_ticks)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(json.dumps({**report, "lanes": [
        {k: v for k, v in lane.items() if k != "invalid_rows"} for lane in report["lanes"]
    ]}, indent=2))
