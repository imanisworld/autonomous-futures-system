"""Tests for scripts/inverse_orb_baseline_post_fill_decomposition.py.

Pure helpers on synthetic rows, plus the load-bearing invariants of the real
report: the split must be exhaustive, must not re-price anything, and must
reproduce the frozen baseline's own fill/no-fill status.
"""
from __future__ import annotations

import pytest

from scripts import inverse_orb_baseline_post_fill_decomposition as decomp


def _row(net: float, result: str, exit_reason: str = "STOP_HIT", *,
         fill_market: float = 100.0, source_entry: float = 100.0,
         bar_ts: str = "2025-01-01T00:00:00+00:00", session: str = "new_york") -> dict:
    return {
        "net": net, "result": result, "exit_reason": exit_reason,
        "fill_market": fill_market, "source_entry": source_entry,
        "bar_ts": bar_ts, "session": session,
    }


def test_cell_counts_wins_by_pnl_not_by_recorded_label():
    """The defect this script exists to expose: LOSS labels carrying profit."""
    cell = decomp._cell("x", [_row(82.02, "LOSS"), _row(10.0, "LOSS"), _row(-5.0, "LOSS")])
    assert cell["n"] == 3
    assert cell["wins_by_pnl"] == 2
    assert cell["losses_by_pnl"] == 1
    assert cell["recorded_result_labels"] == {"LOSS": 3}
    assert cell["label_contradictions"] == 2


def test_cell_flags_a_win_label_carrying_a_negative_net_too():
    cell = decomp._cell("x", [_row(-3.0, "WIN")])
    assert cell["label_contradictions"] == 1


def test_cell_profit_factor_is_none_when_there_are_no_losers():
    assert decomp._cell("x", [_row(5.0, "WIN"), _row(1.0, "WIN")])["profit_factor"] is None


def test_cell_net_is_the_sum_of_recorded_nets():
    assert decomp._cell("x", [_row(1.5, "WIN"), _row(-0.5, "LOSS")])["net"] == 1.0


def test_detachment_is_measured_in_ticks_from_the_nominal_entry():
    # 2.5 points away = 10 ticks, on both sides of the entry.
    cell = decomp._cell("x", [
        _row(1.0, "WIN", fill_market=102.5, source_entry=100.0),
        _row(1.0, "WIN", fill_market=97.5, source_entry=100.0),
    ])
    spread = cell["detachment_ticks_at_fill"]
    assert spread["median"] == 10.0
    assert spread["min"] == spread["max"] == 10.0
    assert spread["share_beyond_tolerance"] == 1.0


def test_detachment_share_is_relative_to_the_frozen_eight_tick_tolerance():
    cell = decomp._cell("x", [
        _row(1.0, "WIN", fill_market=100.5, source_entry=100.0),   # 2 ticks, inside
        _row(1.0, "WIN", fill_market=105.0, source_entry=100.0),   # 20 ticks, outside
    ])
    assert cell["detachment_ticks_at_fill"]["share_beyond_tolerance"] == 0.5


def test_spread_of_nothing_is_none():
    assert decomp._spread([]) is None


def test_halves_split_chronologically_not_by_input_order():
    rows = [
        _row(5.0, "WIN", bar_ts="2025-03-01T00:00:00+00:00"),
        _row(-1.0, "LOSS", bar_ts="2025-01-01T00:00:00+00:00"),
    ]
    assert decomp._halves(rows) == {"h1_net": -1.0, "h2_net": 5.0}


# ── invariants of the real report ────────────────────────────────────────────


@pytest.fixture(scope="module")
def report() -> dict:
    return decomp.build_report()


def test_refill_reproduces_the_frozen_baselines_own_fill_status(report):
    """If this drifts, the decomposition is describing a different population."""
    assert report["refill_disagreements"] == 0
    assert report["arms"] == 63
    assert report["unfilled_arms"] == 6


def test_the_two_cells_partition_the_filled_arms(report):
    cells = {c["cell"]: c for c in report["cells"]}
    rejected = cells["rejected_by_post_fill_validation"]
    admitted = cells["admitted_by_post_fill_validation"]
    total = cells["all_filled_canonical_baseline"]
    assert rejected["n"] + admitted["n"] == total["n"] == 57
    assert round(rejected["net"] + admitted["net"], 2) == total["net"]


def test_the_baseline_headline_is_unchanged_by_this_script(report):
    """Guards against silently re-pricing the frozen proof."""
    total = {c["cell"]: c for c in report["cells"]}["all_filled_canonical_baseline"]
    assert total["net"] == 1026.64
    assert total["profit_factor"] == 5.28


def test_every_dollar_of_profit_sits_in_the_rejected_cell(report):
    """The finding itself, pinned."""
    cells = {c["cell"]: c for c in report["cells"]}
    assert cells["rejected_by_post_fill_validation"]["net"] > 0
    assert cells["admitted_by_post_fill_validation"]["net"] < 0
    assert cells["rejected_by_post_fill_validation"]["losses_by_pnl"] == 0


def test_the_rejected_cell_is_mostly_stop_outs_recorded_as_losses(report):
    rejected = {c["cell"]: c for c in report["cells"]}["rejected_by_post_fill_validation"]
    assert rejected["exit_reasons"]["STOP_HIT"] >= 37
    assert rejected["label_contradictions"] >= 37


def test_admissible_subset_fails_walk_forward_and_every_session(report):
    assert report["admitted_halves"]["h1_net"] < 0
    assert all(net < 0 for net in report["admitted_by_session"].values())


def test_stop_direction_is_the_dominant_rejection_reason(report):
    assert report["post_fill_failure_counts"]["stop_direction"] >= 37
