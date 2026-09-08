"""Offline payload parity for the frozen 63-arm inverse IOC population.

Runs the production Tradovate builder with every network boundary stubbed.
This proves deterministic order construction, never venue liquidity or fills.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

from context.mnq_orb_breakout_inverse_paper import mirror_order
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from execution.post_fill_validation import validate_post_fill
from execution.tradovate_broker import TradovateBroker, TradovateConfig, _rr_preserving_entry_cap

BASELINE = Path(__file__).with_name("inverse_orb_canonical_ioc_proof_2026-09-07.json")


def capture_demo_payload(order: BracketOrder) -> dict:
    """Exercise execute_bracket; no broker credentials or network are used."""
    captured = {}
    broker = TradovateBroker(config=TradovateConfig(env="demo", expected_account_id=999))
    broker._account_id = 999

    def capture(path, body):
        assert path == "/order/placeOSO"
        captured.update(body)
        return {"errorText": "OFFLINE_PARITY_CAPTURE"}

    env = {
        "BROKER": "tradovate", "TRADOVATE_ENV": "demo",
        "LIVE_TRADING_ENABLED": "false", "TRADOVATE_EXPECTED_ACCOUNT_ID": "999",
        # Conflicting global defaults must not change this order's contract.
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ": "32",
        "TRADOVATE_ENTRY_EXECUTION_MODE": "market", "EXIT_MODE": "runner_live",
    }
    with (
        patch.dict(os.environ, env),
        patch("requests.sessions.Session.request", side_effect=AssertionError("network prohibited")),
        patch("execution.tradovate_supervisor.tradovate_order_ready", return_value=True),
        patch.object(broker, "_authenticate", return_value=True),
        patch.object(broker, "get_account_balance", return_value=50000.0),
        patch.object(broker, "_find_contract_id", return_value=123),
        patch.object(broker, "_post", side_effect=capture),
    ):
        broker.execute_bracket(order)
    if not captured:
        raise AssertionError("production builder did not produce an inverse IOC payload")
    return captured


def build_report() -> dict:
    baseline_bytes = BASELINE.read_bytes()
    baseline = json.loads(baseline_bytes)
    rows = []
    failed_checks = Counter()
    for index, row in enumerate(baseline["rows"]):
        source = BracketOrder(
            instrument="MNQ", strategy="orb_breakout", direction=row["source_direction"],
            entry=row["source_entry"], stop=row["source_stop"], target=row["source_target"],
            rr_ratio=2.2, min_rr_ratio=2.0, max_stop_ticks=120.0,
            post_fill_validation_required=True,
        )
        inverse = mirror_order(source)
        body = capture_demo_payload(inverse)
        market = row["fill_market"]
        fills = market <= body["price"] if inverse.direction == "LONG" else market >= body["price"]
        paper = PaperBroker(
            slippage_ticks=1.0, pessimistic_both_hit=True, runner_mode=False,
            breakeven_at_1r=False, entry_fill_model="ioc_limit",
            entry_tolerance_ticks_by_root={"MNQ": 8.0},
        ).execute_bracket(replace(inverse, post_fill_validation_required=False), market_price=market)
        paper_fills = paper.result == "OPEN"
        rr_cap = _rr_preserving_entry_cap(inverse, "MNQ")
        old_limit = min(body["price"], rr_cap) if inverse.direction == "LONG" else max(body["price"], rr_cap)
        old_fills = market <= old_limit if inverse.direction == "LONG" else market >= old_limit
        checks = validate_post_fill(inverse, paper.entry_price) if paper_fills else None
        if checks:
            failed_checks.update(checks.failed_checks)
        expected = row["status"] == "FILLED"
        rows.append({
            "arm": index + 1, "bar_ts": row["bar_ts"],
            "baseline_filled": expected, "paper_filled": paper_fills, "demo_limit_accepts": fills,
            "entry_parity": expected == paper_fills == fills,
            "legacy_rr_clamp_accepts": old_fills,
            "static_bracket_parity": (
                body["bracket1"]["price"] == row["inverse_target"]
                and body["bracket2"]["stopPrice"] == row["inverse_stop"]
                and body["orderQty"] == 1 and body["timeInForce"] == "IOC"
            ),
            "post_fill_failed_checks": list(checks.failed_checks) if checks else [],
        })
    return {
        "baseline_file_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "population_fingerprint": baseline["manifest"]["fingerprint_sha256"],
        "arms": len(rows), "fills": sum(r["demo_limit_accepts"] for r in rows),
        "no_fills": sum(not r["demo_limit_accepts"] for r in rows),
        "entry_mismatches": sum(not r["entry_parity"] for r in rows),
        "bracket_mismatches": sum(not r["static_bracket_parity"] for r in rows),
        "legacy_rr_clamp_fill_differences": sum(r["legacy_rr_clamp_accepts"] != r["baseline_filled"] for r in rows),
        "post_fill_rejected_fills": sum(bool(r["post_fill_failed_checks"]) for r in rows),
        "post_fill_failure_counts": dict(failed_checks),
        "comparison_commission_round_trip": 1.48,
        "verdict": "HOLD",
        "limitations": [
            "Deterministic limit eligibility only; actual DEMO quotes, liquidity and IOC/OSO acceptance remain unverified.",
            "External post-fill risk checks remain enforced; entry parity does not imply outcome parity.",
            "Original 63-arm baseline is unchanged; the previously documented 111-arm population remains unreproduced.",
        ],
        "rows": rows,
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
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    else:
        print(rendered, end="")
