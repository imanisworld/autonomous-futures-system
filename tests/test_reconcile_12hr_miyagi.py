from datetime import date

import pytest

from research.reconcile_12hr_miyagi import (
    ManualSetup,
    ReconciliationInputError,
    reconcile,
)
from tests.test_detector_12hr_miyagi import EVAL, bundle


def sample(direction="SHORT"):
    return ManualSetup(EVAL, "MNQ", direction, 100, 90, 75)


def test_perfect_reconciliation_passes():
    bars_12h, bars_5m, bars_60m = bundle()
    report = reconcile(
        manual_setups=[sample()],
        bars_12h=bars_12h,
        bars_5m=bars_5m,
        bars_60m=bars_60m,
        start=EVAL,
        end=EVAL,
        instrument="MNQ",
        expected_manual_count=1,
    )
    assert report["summary"]["passed"] is True
    assert report["summary"]["true_positives"] == 1
    assert report["detector_only_dates"] == []


def test_direction_mismatch_fails():
    bars_12h, bars_5m, bars_60m = bundle()
    report = reconcile(
        manual_setups=[sample("LONG")],
        bars_12h=bars_12h,
        bars_5m=bars_5m,
        bars_60m=bars_60m,
        start=EVAL,
        end=EVAL,
        instrument="MNQ",
    )
    assert report["summary"]["passed"] is False
    assert report["direction_mismatches"]


def test_level_mismatch_fails():
    bars_12h, bars_5m, bars_60m = bundle()
    wrong = ManualSetup(EVAL, "MNQ", "SHORT", 101, 90, 75)
    report = reconcile(
        manual_setups=[wrong],
        bars_12h=bars_12h,
        bars_5m=bars_5m,
        bars_60m=bars_60m,
        start=EVAL,
        end=EVAL,
        instrument="MNQ",
    )
    assert report["summary"]["passed"] is False
    assert report["level_mismatches"][0]["field"] == "entry_trigger"


def test_expected_count_mismatch_fails_closed():
    bars_12h, bars_5m, bars_60m = bundle()
    with pytest.raises(ReconciliationInputError):
        reconcile(
            manual_setups=[sample()],
            bars_12h=bars_12h,
            bars_5m=bars_5m,
            bars_60m=bars_60m,
            start=date(2026, 1, 8),
            end=date(2026, 1, 8),
            instrument="MNQ",
            expected_manual_count=13,
        )
