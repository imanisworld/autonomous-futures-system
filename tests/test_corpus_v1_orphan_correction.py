"""Regression tests for scripts/corpus_v1_apply_orphan_correction.py -- the
fold-in step that merges scripts/corpus_v1_orphan_resolution.py's
carry-forward resolutions back into the Corpus v1 raw trades and recomputes
the closure-record totals (operator-required correction, 2026-07-25: the
record must show 747/747 resolved, not 724/747, after the 23 day-boundary
orphans are resolved).

These tests exercise apply_correction()'s join/merge logic in isolation with
small synthetic inputs -- they do not require the (gitignored) Corpus v1
journal/candle data on disk. End-to-end correctness against the real corpus
is verified by running the script directly (see docs/corpus-v1-clean-baseline-
report-2026-07-25.md's Correction history item 5 for the actual numbers).
"""

from __future__ import annotations

import pytest

from scripts.corpus_v1_apply_orphan_correction import apply_correction


def _raw(date, instrument, strategy, result, pnl, unjoinable=False):
    return {
        "date": date, "instrument": instrument, "strategy": strategy,
        "result": result, "pnl": pnl, "unjoinable_legacy": unjoinable,
    }


def _resolution(date, instrument, strategy, result, pnl_dollars, resolution="RESOLVED"):
    return {
        "date": date, "instrument": instrument, "strategy": strategy,
        "direction": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0,
        "contracts": 1, "paper_order_id": "PAPER-x", "resolution": resolution,
        "resolved_on": "2026-01-02", "days_to_resolve": 1, "bars_scanned": 10,
        "result": result, "exit_price": 105.0, "pnl_ticks": 20.0,
        "pnl_dollars": pnl_dollars,
    }


def test_folds_a_resolved_orphan_into_the_raw_trades():
    raw = [
        _raw("2026-01-01", "MNQ", "orb_reclaim", "WIN", 100.0),
        _raw("2026-01-01", "MNQ", "orb_reclaim", None, None),  # the orphan
    ]
    resolutions = {("2026-01-01", "MNQ", "orb_reclaim"): _resolution("2026-01-01", "MNQ", "orb_reclaim", "LOSS", -50.0)}

    corrected = apply_correction(raw, resolutions)

    assert len(corrected) == 2
    assert corrected[0]["result"] == "WIN"  # untouched row unaffected
    orphan_row = corrected[1]
    assert orphan_row["result"] == "LOSS"
    assert orphan_row["pnl"] == -50.0
    assert orphan_row["corrected_from_orphan"] is True


def test_leaves_already_resolved_and_unjoinable_rows_untouched():
    raw = [
        _raw("2026-01-01", "MNQ", "orb_reclaim", "WIN", 100.0),
        _raw("2026-01-01", "MNQ", "orb_reclaim", "LOSS", -30.0),
        _raw("2026-01-01", "MNQ", "orb_reclaim", None, None, unjoinable=True),
    ]
    corrected = apply_correction(raw, {})

    assert corrected == raw  # nothing to resolve, nothing should change


def test_raises_if_an_open_trade_has_no_matching_resolution():
    raw = [_raw("2026-01-01", "MNQ", "orb_reclaim", None, None)]
    with pytest.raises(AssertionError, match="No orphan resolution found"):
        apply_correction(raw, {})


def test_raises_if_a_resolution_has_no_matching_open_trade():
    raw = [_raw("2026-01-01", "MNQ", "orb_reclaim", "WIN", 100.0)]
    resolutions = {("2026-01-01", "MNQ", "orb_reclaim"): _resolution("2026-01-01", "MNQ", "orb_reclaim", "LOSS", -50.0)}
    with pytest.raises(AssertionError, match="no matching open raw_trades row"):
        apply_correction(raw, resolutions)


def test_raises_on_ambiguous_duplicate_open_trades_for_the_same_key():
    raw = [
        _raw("2026-01-01", "MNQ", "orb_reclaim", None, None),
        _raw("2026-01-01", "MNQ", "orb_reclaim", None, None),  # duplicate key, ambiguous
    ]
    resolutions = {("2026-01-01", "MNQ", "orb_reclaim"): _resolution("2026-01-01", "MNQ", "orb_reclaim", "LOSS", -50.0)}
    with pytest.raises(AssertionError, match="ambiguous"):
        apply_correction(raw, resolutions)


def test_never_touches_a_row_that_already_resolved_inline():
    """A trade that resolved WIN/LOSS through the normal path must never be
    reinterpreted as an orphan even if a stale resolution file happens to
    share its (date, instrument, strategy) key -- the unused-resolution
    check should catch that mismatch rather than silently overwriting."""
    raw = [_raw("2026-01-01", "MNQ", "orb_reclaim", "WIN", 100.0)]
    resolutions = {("2026-01-01", "MNQ", "orb_reclaim"): _resolution("2026-01-01", "MNQ", "orb_reclaim", "LOSS", -999.0)}
    with pytest.raises(AssertionError):
        apply_correction(raw, resolutions)
