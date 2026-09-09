"""Temporary CI probe for 3-2-2 MNQ stop-cap repair.

Evidence-only. No runtime/config change. The test intentionally fails so CI
prints a deterministic comparison across a small pre-declared stop-cap grid.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from context import wide_stop_ledger_paper as contract
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
TICK = 0.25
CAPS = (300.0, 400.0, 500.0, 600.0)


def _rows(cap: float) -> list[dict]:
    out = []
    with gzip.open(CANDIDATES, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("lane") != "322_mnq":
                continue
            stop_ticks = abs(float(row["entry"]) - float(row["stop"])) / TICK
            if stop_ticks <= cap:
                out.append({**row, "stop_ticks_recalc": stop_ticks})
    return sorted(out, key=lambda r: (r.get("date", ""), r.get("bar_ts", "")))


def _ioc(row: dict) -> str:
    market = (row.get("gates") or {}).get("decision_close")
    if market is None:
        return "NO_DECISION_CLOSE"
    broker = PaperBroker(
        starting_balance=1_000_000.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": 8.0},
    )
    fill = broker.execute_bracket(
        BracketOrder(
            instrument=row["instrument"], direction=row["direction"],
            entry=row["entry"], stop=row["stop"], target=row["target"],
            rr_ratio=row["rr"], strategy=row["strategy"], contracts=1,
        ),
        market_price=float(market),
    )
    if fill.result == "OPEN":
        return "FILLED"
    if fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL":
        return "INVALID_AT_FILL"
    return "UNMARKETABLE"


def _metrics(cap: float) -> dict:
    rows = _rows(cap)
    resolved = [r for r in rows if (r.get("bracket") or {}).get("status") == "RESOLVED"]
    nets = [float(r["bracket"]["net"]) for r in resolved]
    gp = sum(v for v in nets if v > 0)
    gl = -sum(v for v in nets if v < 0)
    mid = len(resolved) // 2
    ioc = [_ioc(r) for r in rows]
    return {
        "cap_ticks": cap,
        "max_risk_dollars_1c": cap * 0.50,
        "admitted": len(rows),
        "resolved": len(resolved),
        "net": round(sum(nets), 2),
        "profit_factor": round(gp / gl, 6) if gl else None,
        "wins": sum(v > 0 for v in nets),
        "losses": sum(v < 0 for v in nets),
        "h1_net": round(sum(float(r["bracket"]["net"]) for r in resolved[:mid]), 2),
        "h2_net": round(sum(float(r["bracket"]["net"]) for r in resolved[mid:]), 2),
        "worst_net": round(min(nets), 2) if nets else None,
        "ioc_filled": ioc.count("FILLED"),
        "ioc_unmarketable": ioc.count("UNMARKETABLE"),
        "ioc_invalid_at_fill": ioc.count("INVALID_AT_FILL"),
        "ioc_fill_rate": round(ioc.count("FILLED") / len(rows), 3) if rows else None,
    }


def test_emit_322_cap_repair_grid():
    report = {str(int(cap)): _metrics(cap) for cap in CAPS}
    pytest.fail("EVIDENCE_PROBE=" + json.dumps(report, sort_keys=True))
