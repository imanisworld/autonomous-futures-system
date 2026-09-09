"""Temporary CI probe for 3-2-2 MNQ repair attribution.

Evidence-only. No runtime/config change. The test intentionally fails so CI
prints deterministic stop-cap and market-condition comparisons.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
TICK = 0.25
CAPS = (300.0, 400.0, 500.0, 600.0)


def _all_rows() -> list[dict]:
    out = []
    with gzip.open(CANDIDATES, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("lane") != "322_mnq":
                continue
            stop_ticks = abs(float(row["entry"]) - float(row["stop"])) / TICK
            out.append({**row, "stop_ticks_recalc": stop_ticks})
    return sorted(out, key=lambda r: (r.get("date", ""), r.get("bar_ts", "")))


def _rows(cap: float) -> list[dict]:
    return [r for r in _all_rows() if r["stop_ticks_recalc"] <= cap]


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


def _summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if (r.get("bracket") or {}).get("status") == "RESOLVED"]
    nets = [float(r["bracket"]["net"]) for r in resolved]
    gp = sum(v for v in nets if v > 0)
    gl = -sum(v for v in nets if v < 0)
    mid = len(resolved) // 2
    ioc = [_ioc(r) for r in rows]
    return {
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


def _metrics(cap: float) -> dict:
    out = _summarize(_rows(cap))
    out.update({"cap_ticks": cap, "max_risk_dollars_1c": cap * 0.50})
    return out


def _condition_splits(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        condition = str((row.get("gates") or {}).get("market_condition") or "UNKNOWN")
        buckets.setdefault(condition, []).append(row)
    return {name: _summarize(bucket) for name, bucket in sorted(buckets.items())}


def test_emit_322_repair_attribution():
    all_rows = _all_rows()
    report = {
        "caps": {str(int(cap)): _metrics(cap) for cap in CAPS},
        "condition_all": _condition_splits(all_rows),
        "condition_cap500": _condition_splits(_rows(500.0)),
        "condition_cap600": _condition_splits(_rows(600.0)),
    }
    pytest.fail("EVIDENCE_PROBE=" + json.dumps(report, sort_keys=True))
