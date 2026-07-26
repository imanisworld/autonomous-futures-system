from __future__ import annotations

from pathlib import Path

import pytest

from research.reconcile_12hr_miyagi import (
    REAL_DATA_SPOT_CHECKS,
    reconcile,
    run_real_data_spot_checks,
    run_synthetic_suite,
)


_CACHE_15M = Path(__file__).resolve().parents[1] / "data" / "replay_polygon"
_CACHE_5M = Path(__file__).resolve().parents[1] / "data" / "replay_polygon_5m"

_requires_historical_dataset = pytest.mark.skipif(
    not (_CACHE_15M / "MNQ" / "MNQ_2024-08-22.jsonl").exists(),
    reason=(
        "data/replay_polygon{,_5m}/ is local-only, gitignored, Polygon-sourced "
        "bar data -- not committed to git and not present in every checkout/CI "
        "(same situation as data/stocks_advisory_polygon_5m/, see "
        "tests/test_stocks_tqqq_sqqq_backtest_parity.py). The synthetic-fixture "
        "suite above (run_synthetic_suite) needs no real data and always runs; "
        "only the hardcoded real-date spot-checks require the local cache."
    ),
)


def test_synthetic_suite_covers_every_spec_branch_and_all_pass():
    results = run_synthetic_suite()
    branches = {r["branch"] for r in results}
    expected_branches = {
        "valid_short_2u",
        "valid_long_2d",
        "bar_a_missing_returns_none",
        "bar_b_missing_returns_none",
        "bar_c_missing_returns_none",
        "bar_d_missing_returns_none",
        "bar_z_missing_returns_none",
        "bar_c_not_inside_bar_b_returns_none",
        "bar_b_not_outside_bar_a_returns_none",
        "bar_a_not_inside_bar_z_returns_none",
        "candle3_becomes_outside_bar_invalidates",
        "price_exactly_equals_bar_c_high_at_930_is_ambiguous",
        "price_exactly_equals_bar_c_low_at_930_is_ambiguous",
        "price_between_bar_c_bounds_at_930_returns_none",
        "missing_930_bar_returns_none",
        "missing_60m_stop_reference_returns_none",
    }
    assert expected_branches <= branches
    assert all(r["passed"] for r in results), [r for r in results if not r["passed"]]


@_requires_historical_dataset
def test_real_data_spot_checks_hardcoded_dates_pass_against_live_caches():
    results = run_real_data_spot_checks("data/replay_polygon", "data/replay_polygon_5m")
    assert len(results) == len(REAL_DATA_SPOT_CHECKS) == 5
    failures = [r for r in results if not r["passed"]]
    assert not failures, failures


def test_real_data_spot_checks_include_a_no_signal_and_an_invalidation_case():
    outcomes = {
        (case["instrument"], case["eval_date"].isoformat()): case
        for case in REAL_DATA_SPOT_CHECKS
    }
    assert outcomes[("MNQ", "2024-07-10")]["expected_signal"] is None
    assert outcomes[("MNQ", "2025-02-12")]["expected_invalidation"] == "CANDLE3_BECAME_OUTSIDE_BAR"
    assert outcomes[("MNQ", "2024-08-22")]["expected_direction"] == "SHORT"
    assert outcomes[("MES", "2024-07-12")]["expected_direction"] == "SHORT"


@_requires_historical_dataset
def test_reconcile_report_passes_and_reports_both_halves():
    report = reconcile("data/replay_polygon", "data/replay_polygon_5m")
    assert report["passed"] is True
    assert report["synthetic_suite"]["all_passed"] is True
    assert report["real_data_spot_checks"]["all_passed"] is True
    assert report["synthetic_suite"]["total"] == 16
    assert report["real_data_spot_checks"]["total"] == 5
