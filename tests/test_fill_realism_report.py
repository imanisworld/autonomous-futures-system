from __future__ import annotations

import json

from scripts.fill_realism_report import (
    is_nofill,
    naive_would_fill,
    pair_journal,
    tol_points,
)


def test_naive_would_fill_long_short():
    # LONG buy-limit at entry+tol fills iff close <= entry+tol
    assert naive_would_fill("LONG", 100.0, 99.0, 0.5) is True      # close below entry
    assert naive_would_fill("LONG", 100.0, 101.0, 0.5) is False    # close ran above
    # SHORT sell-limit at entry-tol fills iff close >= entry-tol
    assert naive_would_fill("SHORT", 100.0, 101.0, 0.5) is True    # close above entry
    assert naive_would_fill("SHORT", 100.0, 98.0, 0.5) is False    # close ran below (trend-day miss)
    assert naive_would_fill("LONG", None, 100.0, 0.5) is None


def test_is_nofill_only_for_entry_cancel():
    assert is_nofill("CANCELLED", "execution_failed:CANCELLED") is True
    assert is_nofill("CANCELLED", "some ENTRY_NOT_FILLED path") is True
    assert is_nofill("CANCELLED", "auto-reconcile: phantom cleared") is False  # phantom, not no-fill
    assert is_nofill("WIN", "") is False
    assert is_nofill("LOSS", "") is False


def test_tol_points_per_root():
    assert tol_points("MESU6") == tol_points("MES")  # root-normalized
    assert tol_points("MES") > 0


def test_pair_journal_links_decision_to_outcome(tmp_path):
    f = tmp_path / "journal_2026-06-24.jsonl"
    recs = [
        {"type": "BAR_CLAIM", "instrument": "MES"},
        {"instrument": "MES", "decision": "TRADE",
         "setup": {"strategy": "vwap_hold", "direction": "SHORT", "entry": 7444.25},
         "context": {"close": 7430.0}},
        {"type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "CANCELLED", "exit_reason": "execution_failed:CANCELLED"}},
        {"instrument": "MNQ", "decision": "TRADE",
         "setup": {"strategy": "orb_breakout", "direction": "LONG", "entry": 30000.0},
         "context": {"close": 29999.0}},
        {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN"}},
    ]
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    rows = pair_journal([str(f)])
    assert len(rows) == 2
    short = next(r for r in rows if r["instrument"] == "MES")
    assert short["strategy"] == "vwap_hold" and short["actual_nofill"] is True
    long = next(r for r in rows if r["instrument"] == "MNQ")
    assert long["actual_nofill"] is False
