"""Temporary evidence-only Transition repair probe. Intentionally fails CI.

Tests the strongest broad repair lead from the first pass:
MNQ + New York session + stop-only protection + 30-minute maximum hold.
No target is used in this counterfactual because the documented fixed bracket
is the diagnosed failure stage. No runtime/config/strategy code changes.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
LANES = ("transition_mnq", "transition_mnq_audit")
# 1 MNQ tick = $0.50. Grid is capped at $300 risk / 600 ticks.
STOP_TICKS = (60, 80, 100, 120, 160, 200, 240, 300, 400, 500, 600)
TICK_SIZE = 0.25
DOLLARS_PER_POINT = 2.0
ROUND_TURN_COMMISSION = 1.48


def rows(lane):
    out = []
    with gzip.open(CANDIDATES, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("lane") == lane and r.get("instrument") == "MNQ" and r.get("session") == "new_york":
                c = (r.get("control") or {}).get("30m") or {}
                if c.get("net") is not None and c.get("mae_pts") is not None:
                    out.append(r)
    return sorted(out, key=lambda r: (r.get("date", ""), r.get("bar_ts", "")))


def summarize(vals):
    if not vals:
        return {"n": 0}
    gp = sum(v for v in vals if v > 0)
    gl = -sum(v for v in vals if v < 0)
    mid = len(vals) // 2
    return {
        "n": len(vals),
        "net": round(sum(vals), 2),
        "mean": round(sum(vals) / len(vals), 4),
        "pf": round(gp / gl, 6) if gl else None,
        "wins": sum(v > 0 for v in vals),
        "losses": sum(v < 0 for v in vals),
        "h1": round(sum(vals[:mid]), 2),
        "h2": round(sum(vals[mid:]), 2),
        "worst": round(min(vals), 2),
    }


def stop_only_30m(rs, stop_ticks):
    """Stop first if 30m MAE reaches the fixed cap; otherwise exit at 30m."""
    stop_points = stop_ticks * TICK_SIZE
    stop_net = -(stop_points * DOLLARS_PER_POINT) - ROUND_TURN_COMMISSION
    vals = []
    stopped = 0
    for r in rs:
        c = r["control"]["30m"]
        mae = float(c["mae_pts"])
        if mae >= stop_points:
            vals.append(stop_net)
            stopped += 1
        else:
            vals.append(float(c["net"]))
    return {
        **summarize(vals),
        "stop_ticks": stop_ticks,
        "stop_points": stop_points,
        "max_stop_risk_dollars": round(stop_ticks * 0.50, 2),
        "stopped": stopped,
        "stop_rate": round(stopped / len(vals), 4) if vals else None,
    }


def test_emit_transition_stop_grid():
    report = {}
    for lane in LANES:
        rs = rows(lane)
        baseline = summarize([float(r["control"]["30m"]["net"]) for r in rs])
        report[lane] = {
            "ny_30m_baseline_no_stop": baseline,
            "stop_grid": {str(t): stop_only_30m(rs, t) for t in STOP_TICKS},
        }
    pytest.fail("TRANSITION_STOP_GRID=" + json.dumps(report, sort_keys=True))
