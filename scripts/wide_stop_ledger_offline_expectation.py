#!/usr/bin/env python3
"""Build step 1: the wide-stop lane's pre-registered offline expectation.

Spec §6 requires a written expectation, computed BEFORE the lane starts, so the
forward record is measured against something fixed rather than tuned after the
fact. It replays the approved/amended cells through the production `ioc_limit`
entry at the frozen contract (8 ticks, 1 adverse tick, decision-bar close):

  wide_stop_4k  4HR MNQ,   stop <= 300 ticks and R:R >= 1.0
  wide_stop_6k  3-2-2 MNQ, stop <= 600 ticks, no R:R floor

The 4HR cap was reduced from 400 to 300 ticks by operator instruction on
2026-09-08. The replacement historical cell was recomputed from the committed
candidate artifact before merge: 32 trades, +$1,901.14 net, PF 2.313, with both
chronological halves positive (+$1,164.82 / +$736.32).

**Scope, stated rather than implied.** This establishes the *entry* side —
which candidates the lane admits, which fill under the frozen IOC, at what
price, and which are refused because the fill lands outside its own bracket.
It does NOT establish forward P&L: the exit expectation is the committed
historical bracket cell, and the forward lane still has to earn its own record.

Per the §6 build precondition, invalid-at-fill outcomes are counted as their
own line and never blended into the expectation. Since #508 `PaperBroker`
refuses those fills on every entry path, this reports what the broker already
enforces rather than re-implementing the rule.

Input is `scripts/edge_decomposition_audit_results_candidates.jsonl.gz`, a
committed artifact — this runs from a fresh clone and reads no gitignored bars.

Research only. No strategy, risk, replay, broker, config or deployment change.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import statistics
from pathlib import Path

from context import wide_stop_ledger_paper as contract
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker

CANDIDATES = Path(__file__).with_name("edge_decomposition_audit_results_candidates.jsonl.gz")
TICK = 0.25
SLIPPAGE_TICKS = 1.0

#: Historical bracket cells used as the exit-side expectation. The 4HR cell
#: was recomputed from the committed candidate artifact after the operator's
#: 2026-09-08 cap reduction; 3-2-2 is unchanged.
BRACKET_CELLS = {
    "wide_stop_4k": {
        "lane": "4hr_mnq", "admitted": 32, "bracket_net": 1901.14,
        "profit_factor": 2.313232, "h1": 1164.82, "h2": 736.32,
        "losses_over_150": 2,
        "note": "300-tick operator amendment; prior 400-tick cell was 36 trades / +$3,076.72 / PF 3.125",
    },
    "wide_stop_6k": {
        "lane": "322_mnq", "admitted": 24, "bracket_net": 1790.0, "profit_factor": 9.9,
        "note": "PF is a 1-loss artifact; not evidence until >= 5 losses are observed",
    },
}


def _cell_members(lane: str, ledger: contract.Ledger) -> list[dict]:
    """The candidates the family caps admit."""
    rows = []
    with gzip.open(CANDIDATES, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row["lane"] != lane:
                continue
            stop_ticks = abs(float(row["entry"]) - float(row["stop"])) / TICK
            if stop_ticks > ledger.max_stop_ticks:
                continue
            if float(row["rr"]) < ledger.min_rr_ratio:
                continue
            rows.append({**row, "stop_ticks": stop_ticks})
    return rows


def _ioc_entry(row: dict) -> dict:
    """Production `ioc_limit` at the frozen lane contract."""
    market = (row.get("gates") or {}).get("decision_close")
    if market is None:
        return {"status": "NO_DECISION_CLOSE"}
    broker = PaperBroker(
        starting_balance=1_000_000.0,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={contract.INSTRUMENT: contract.MARKETABLE_TICKS},
    )
    fill = broker.execute_bracket(
        BracketOrder(
            instrument=row["instrument"], direction=row["direction"],
            entry=row["entry"], stop=row["stop"], target=row["target"],
            rr_ratio=row["rr"], strategy=row["strategy"],
            contracts=contract.CONTRACTS,
        ),
        market_price=float(market),
    )
    if fill.result == "OPEN":
        return {
            "status": "FILLED",
            "fill_price": float(fill.entry_price),
            "detachment_ticks": abs(float(fill.entry_price) - float(row["entry"])) / TICK,
        }
    return {"status": "NO_FILL", "reason": fill.exit_reason}


def build_report() -> dict:
    ledgers = {}
    for name, ledger in contract.LEDGERS.items():
        cell = BRACKET_CELLS[name]
        rows = _cell_members(cell["lane"], ledger)
        entries = [{**row, **_ioc_entry(row)} for row in rows]
        filled = [e for e in entries if e["status"] == "FILLED"]
        invalid = [
            e for e in entries
            if e["status"] == "NO_FILL" and e.get("reason") == "ENTRY_BRACKET_INVALID_AT_FILL"
        ]
        unmarketable = [
            e for e in entries
            if e["status"] == "NO_FILL" and e.get("reason") != "ENTRY_BRACKET_INVALID_AT_FILL"
        ]
        detach = [e["detachment_ticks"] for e in filled]
        ledgers[name] = {
            "ledger": name,
            "starting_balance": ledger.starting_balance,
            "family_stop_cap_ticks": ledger.max_stop_ticks,
            "min_rr_ratio": ledger.min_rr_ratio,
            "cell_size": len(rows),
            "expected_cell_size": cell["admitted"],
            "cell_matches_memo": len(rows) == cell["admitted"],
            "ioc_filled": len(filled),
            "ioc_unmarketable": len(unmarketable),
            # Counted separately and never blended into the expectation (§6).
            "invalid_at_fill": len(invalid),
            "invalid_at_fill_dates": [e["date"] for e in invalid],
            "fill_rate": round(len(filled) / len(rows), 3) if rows else None,
            "median_detachment_ticks": round(statistics.median(detach), 1) if detach else None,
            "max_detachment_ticks": round(max(detach), 1) if detach else None,
            "bracket_expectation": cell,
        }
    return {
        "purpose": "spec §6 build step 1 — pre-registered offline expectation",
        "entry_contract": {
            "fill_model": "ioc_limit",
            "marketable_ticks": contract.MARKETABLE_TICKS,
            "reference_price": "decision-bar close",
            "adverse_slippage_ticks": SLIPPAGE_TICKS,
            "contracts": contract.CONTRACTS,
            "exit": "documented static bracket, no runner",
            "commission_round_trip": contract.COMMISSION_ROUND_TRIP,
        },
        "establishes": "entry-side admission and fill behaviour plus a fixed historical bracket benchmark",
        "does_not_establish": [
            "forward P&L — the historical bracket cell is a benchmark, not forward evidence",
            "that IOC realises historical bracket P&L",
        ],
        "ledgers": ledgers,
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
