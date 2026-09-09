"""Temporary audit probe for the operator-requested 4HR 300-tick cap.

This is intentionally failing on the draft branch so CI prints the exact
committed-artifact metrics. It must be removed before merge.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
TICK = 0.25


def _metrics(cap: float) -> dict:
    rows = []
    with gzip.open(CANDIDATES, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("lane") != "4hr_mnq":
                continue
            stop_ticks = abs(float(row["entry"]) - float(row["stop"])) / TICK
            if stop_ticks > cap or float(row["rr"]) < 1.0:
                continue
            rows.append(row)

    rows.sort(key=lambda r: (r.get("date", ""), r.get("bar_ts", "")))
    if not rows:
        return {"cap": cap, "rows": 0}
    if "bracket" not in rows[0]:
        return {"cap": cap, "rows": len(rows), "row_keys": sorted(rows[0].keys())}

    resolved = [r for r in rows if (r.get("bracket") or {}).get("status") == "RESOLVED"]
    nets = [float(r["bracket"]["net"]) for r in resolved]
    grosses = [float(r["bracket"]["gross"]) for r in resolved]
    gp_net = sum(v for v in nets if v > 0)
    gl_net = -sum(v for v in nets if v < 0)
    gp_gross = sum(v for v in grosses if v > 0)
    gl_gross = -sum(v for v in grosses if v < 0)

    mid = len(resolved) // 2
    h1 = resolved[:mid]
    h2 = resolved[mid:]
    return {
        "cap": cap,
        "admitted": len(rows),
        "resolved": len(resolved),
        "dates": [r["date"] for r in rows],
        "net": round(sum(nets), 2),
        "gross": round(sum(grosses), 2),
        "pf_net": round(gp_net / gl_net, 6) if gl_net else None,
        "pf_gross": round(gp_gross / gl_gross, 6) if gl_gross else None,
        "wins": sum(v > 0 for v in nets),
        "losses": sum(v < 0 for v in nets),
        "h1_n": len(h1),
        "h1_net": round(sum(float(r["bracket"]["net"]) for r in h1), 2),
        "h2_n": len(h2),
        "h2_net": round(sum(float(r["bracket"]["net"]) for r in h2), 2),
        "over_150_losses": sum(float(r["bracket"]["net"]) < -150.0 for r in resolved),
    }


def test_emit_4hr_300_cap_evidence():
    report = {"cap400": _metrics(400.0), "cap300": _metrics(300.0)}
    pytest.fail("EVIDENCE_PROBE=" + json.dumps(report, sort_keys=True))
