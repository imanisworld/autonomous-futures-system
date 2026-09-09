"""Tests for the evidence-only Transition IOC repair replay harness."""
from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

import pytest

from scripts import transition_repair_ioc_audit as audit

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
LANES = ("transition_mnq", "transition_mnq_audit")


def _eligible_rows(lane):
    rows = []
    with gzip.open(CANDIDATES, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            c30 = ((row.get("control") or {}).get("30m") or {})
            if (
                row.get("lane") == lane
                and row.get("instrument") == "MNQ"
                and row.get("session") == "new_york"
                and c30.get("exit_bar_ts") is not None
            ):
                rows.append(row)
    return rows


def _row(direction="LONG", close=200.0):
    decision = audit._dt("2026-05-04T15:05:00+00:00")
    return {
        "bar_ts": decision.isoformat(),
        "date": "2026-05-04",
        "direction": direction,
        "entry": close,
        "gates": {"decision_close": close},
        "control": {"30m": {"exit_bar_ts": (decision + timedelta(minutes=30)).isoformat()}},
    }


def _bars(*, final_close=210.0, stop_hit=False):
    decision = audit._dt("2026-05-04T15:05:00+00:00")
    out = {}
    for step in range(1, 7):
        ts = decision + timedelta(minutes=5 * step)
        out[ts] = {"open": 200.0, "high": 211.0, "low": 190.0, "close": final_close}
    if stop_hit:
        out[decision + timedelta(minutes=10)]["low"] = 99.0
    return out


def test_all_committed_eligible_transition_entries_equal_decision_close():
    for lane in LANES:
        rows = _eligible_rows(lane)
        assert rows, lane
        for row in rows:
            assert float(row["entry"]) == pytest.approx(float(row["gates"]["decision_close"]))


def test_decision_close_ioc_time_exit_math():
    result = audit.resolve_one(_row(), _bars(final_close=210.0))
    assert result["exit_reason"] == "TIME_30M"
    assert result["entry"] == pytest.approx(200.25)
    assert result["exit"] == pytest.approx(209.75)
    assert result["net"] == pytest.approx(17.52)


def test_decision_close_ioc_stop_is_pessimistic_and_slipped():
    result = audit.resolve_one(_row(), _bars(stop_hit=True))
    assert result["exit_reason"] == "STOP_HIT"
    assert result["stop"] == pytest.approx(100.0)
    assert result["exit"] == pytest.approx(99.75)
    assert result["net"] == pytest.approx(-202.48)


def test_missing_raw_bar_fails_closed():
    bars = _bars()
    missing = audit._dt("2026-05-04T15:20:00+00:00")
    bars.pop(missing)
    with pytest.raises(KeyError, match="missing required 5m bar"):
        audit.resolve_one(_row(), bars)
