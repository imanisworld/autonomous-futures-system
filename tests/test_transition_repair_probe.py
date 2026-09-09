"""Temporary evidence-only Transition repair probe. Intentionally fails CI.

Inspect the committed candidate artifact to determine whether it contains enough
information to rerun the promising Transition repair under decision-close IOC
without inventing price-path data. No runtime/config/strategy changes.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
LANES = ("transition_mnq", "transition_mnq_audit")


def first_ny_row(lane):
    with gzip.open(CANDIDATES, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("lane") == lane and r.get("instrument") == "MNQ" and r.get("session") == "new_york":
                return r
    return None


def test_emit_transition_candidate_shape():
    report = {}
    for lane in LANES:
        r = first_ny_row(lane)
        report[lane] = {
            "top_keys": sorted(r.keys()) if r else [],
            "bar_ts": r.get("bar_ts") if r else None,
            "direction": r.get("direction") if r else None,
            "entry": r.get("entry") if r else None,
            "gates": r.get("gates") if r else None,
            "control_30m": ((r.get("control") or {}).get("30m")) if r else None,
            "extra": r.get("extra") if r else None,
        }
    pytest.fail("TRANSITION_CANDIDATE_SHAPE=" + json.dumps(report, sort_keys=True, default=str))
