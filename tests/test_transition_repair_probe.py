"""Temporary evidence-only Transition repair probe. Intentionally fails CI.

Tests only broad, pre-trade-known filters and max-hold exits on the committed
edge-decomposition candidate artifact. No runtime/config/strategy code changes.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
LANES = ("transition_mnq", "transition_mnq_audit", "transition_mes_audit")
HORIZONS = ("30m", "60m", "120m", "EOD")


def _dt(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def rows(lane):
    out = []
    with gzip.open(CANDIDATES, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("lane") == lane:
                out.append(r)
    return sorted(out, key=lambda r: (r.get("date", ""), r.get("bar_ts", "")))


def _summ(vals):
    vals = [float(v) for v in vals if v is not None]
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


def bracket_summary(rs):
    vals = []
    for r in rs:
        b = r.get("bracket") or {}
        if b.get("status") == "RESOLVED" and b.get("net") is not None:
            vals.append(float(b["net"]))
    return _summ(vals)


def control_summary(rs):
    out = {}
    for h in HORIZONS:
        vals = []
        for r in rs:
            c = (r.get("control") or {}).get(h)
            if c and c.get("net") is not None:
                vals.append(float(c["net"]))
        out[h] = _summ(vals)
    return out


def hybrid_summary(rs):
    """Protective bracket OR max-hold exit, whichever is first."""
    out = {}
    for h in HORIZONS:
        vals = []
        bracket_first = time_first = 0
        for r in rs:
            b = r.get("bracket") or {}
            c = (r.get("control") or {}).get(h) or {}
            if b.get("status") != "RESOLVED" or b.get("net") is None or c.get("net") is None:
                continue
            bt, ct = _dt(b.get("exit_bar_ts")), _dt(c.get("exit_bar_ts"))
            if bt is not None and ct is not None and bt <= ct:
                vals.append(float(b["net"])); bracket_first += 1
            else:
                vals.append(float(c["net"])); time_first += 1
        out[h] = {**_summ(vals), "bracket_first": bracket_first, "time_first": time_first}
    return out


def _bucket(rs, keyfunc):
    buckets = {}
    for r in rs:
        key = str(keyfunc(r) or "UNKNOWN")
        buckets.setdefault(key, []).append(r)
    return {
        k: {
            "bracket": bracket_summary(v),
            "control": control_summary(v),
            "hybrid": hybrid_summary(v),
        }
        for k, v in sorted(buckets.items())
    }


def test_emit_transition_repair_probe():
    report = {}
    for lane in LANES:
        rs = rows(lane)
        report[lane] = {
            "n": len(rs),
            "sample_keys": sorted(rs[0].keys()) if rs else [],
            "all": {
                "bracket": bracket_summary(rs),
                "control": control_summary(rs),
                "hybrid": hybrid_summary(rs),
            },
            "by_session": _bucket(rs, lambda r: r.get("session")),
            "by_condition": _bucket(rs, lambda r: (r.get("gates") or {}).get("market_condition")),
            "by_grade": _bucket(rs, lambda r: (r.get("gates") or {}).get("confluence_grade")),
        }
    pytest.fail("TRANSITION_REPAIR_PROBE=" + json.dumps(report, sort_keys=True))
