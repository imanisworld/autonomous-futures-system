#!/usr/bin/env python3
"""Decompose the canonical inverse ORB baseline by post-fill admissibility.

Research only. Reads the frozen 63-arm canonical proof, re-derives each arm's
inverse order with the production mirror, re-fills it with the real
PaperBroker under the frozen 8-tick IOC contract, and asks the production
post-fill validator whether that fill is admissible. It then splits the
baseline's own recorded P&L by that answer.

Nothing here re-prices, re-times or re-labels a trade: every dollar reported
comes from the canonical JSON's `net` field. The only thing computed is which
side of the post-fill gate each arm falls on.

No strategy, risk, replay, broker, config or deployment change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
from collections import Counter
from dataclasses import replace
from pathlib import Path

from context.mnq_orb_breakout_inverse_paper import mirror_order
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from execution.post_fill_validation import validate_post_fill

BASELINE = Path(__file__).with_name("inverse_orb_canonical_ioc_proof_2026-09-07.json")

TICK = 0.25
POINT_VALUE = 2.0  # MNQ: 0.25 tick = $0.50
INVERSE_MARKETABLE_TICKS = 8.0


def _order_for(row: dict) -> BracketOrder:
    """The source ORB order exactly as the audit lane constructs it."""
    return BracketOrder(
        instrument="MNQ",
        strategy="orb_breakout",
        direction=row["source_direction"],
        entry=row["source_entry"],
        stop=row["source_stop"],
        target=row["source_target"],
        rr_ratio=2.2,
        min_rr_ratio=2.0,
        max_stop_ticks=120.0,
        post_fill_validation_required=True,
    )


def _paper_fill(inverse: BracketOrder, market: float):
    """The canonical baseline's own fill model: 8-tick marketable IOC."""
    return PaperBroker(
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        runner_mode=False,
        breakeven_at_1r=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": INVERSE_MARKETABLE_TICKS},
    ).execute_bracket(
        replace(inverse, post_fill_validation_required=False), market_price=market
    )


def _cell(name: str, rows: list[dict]) -> dict:
    nets = [r["net"] for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "cell": name,
        "n": len(rows),
        "net": round(sum(nets), 2),
        "wins_by_pnl": len(wins),
        "losses_by_pnl": len(nets) - len(wins),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "exit_reasons": dict(Counter(r["exit_reason"] for r in rows)),
        "recorded_result_labels": dict(Counter(r["result"] for r in rows)),
        "label_contradictions": sum(
            1 for r in rows
            if (r["result"] == "LOSS" and r["net"] > 0) or (r["result"] == "WIN" and r["net"] < 0)
        ),
        "detachment_ticks_at_fill": _spread(
            [abs(r["fill_market"] - r["source_entry"]) / TICK for r in rows]
        ),
    }


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "median": round(statistics.median(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "share_beyond_tolerance": round(
            sum(1 for v in values if v > INVERSE_MARKETABLE_TICKS) / len(values), 3
        ),
    }


def _halves(rows: list[dict]) -> dict:
    """Chronological split on the date median, the inventory's convention."""
    ordered = sorted(rows, key=lambda r: r["bar_ts"])
    mid = len(ordered) // 2
    return {
        "h1_net": round(sum(r["net"] for r in ordered[:mid]), 2),
        "h2_net": round(sum(r["net"] for r in ordered[mid:]), 2),
    }


def build_report() -> dict:
    raw = BASELINE.read_bytes()
    baseline = json.loads(raw)

    rejected: list[dict] = []
    admitted: list[dict] = []
    unfilled = 0
    disagreements = 0

    for row in baseline["rows"]:
        if row["status"] != "FILLED":
            unfilled += 1
            continue
        inverse = mirror_order(_order_for(row))
        fill = _paper_fill(inverse, row["fill_market"])
        if fill.result != "OPEN":
            # The re-derived fill disagrees with the baseline's own status.
            disagreements += 1
            continue
        checks = validate_post_fill(inverse, fill.entry_price)
        enriched = {
            **row,
            "modelled_fill_price": fill.entry_price,
            "failed_checks": sorted(checks.failed_checks) if checks else [],
        }
        (rejected if checks and checks.failed_checks else admitted).append(enriched)

    failure_counts: Counter = Counter()
    for row in rejected:
        failure_counts.update(row["failed_checks"])

    return {
        "generated_for": "inverse ORB canonical 63-arm baseline",
        "baseline_file": BASELINE.name,
        "baseline_file_sha256": hashlib.sha256(raw).hexdigest(),
        "population_fingerprint": baseline["manifest"]["fingerprint_sha256"],
        "arms": len(baseline["rows"]),
        "unfilled_arms": unfilled,
        "refill_disagreements": disagreements,
        "post_fill_failure_counts": dict(failure_counts),
        "cells": [
            _cell("rejected_by_post_fill_validation", rejected),
            _cell("admitted_by_post_fill_validation", admitted),
            _cell("all_filled_canonical_baseline", rejected + admitted),
        ],
        "admitted_halves": _halves(admitted),
        "admitted_by_session": {
            session: round(
                sum(r["net"] for r in admitted if r["session"] == session), 2
            )
            for session in sorted({r["session"] for r in admitted})
        },
        "notes": [
            "Every dollar is the canonical JSON's own `net`; nothing is re-priced.",
            "Admissibility is the production validator's answer on the re-derived fill.",
            "A rejected arm is one the DEMO lane would refuse to hold, because the "
            "fill landed on the wrong side of its own static stop.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = build_report()
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
