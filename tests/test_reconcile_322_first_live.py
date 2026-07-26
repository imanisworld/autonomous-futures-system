from research.reconcile_322_first_live import ManualEntry, reconcile
from tests.test_detector_322_first_live import DAY, short_bundle


def entry(**changes):
    values = {
        "eval_date": DAY,
        "direction": "SHORT",
        "entry_trigger": 92,
        "entry_price": 92,
        "stop": 115,
        "target": 90,
        "gap_open": False,
    }
    values.update(changes)
    return ManualEntry(**values)


def test_perfect_reconciliation_passes():
    report = reconcile(
        manual_entries=[entry()],
        bars_60m=short_bundle(),
        start=DAY,
        end=DAY,
        expected_manual_count=1,
    )
    assert report["summary"]["passed"] is True
    assert report["summary"]["true_positives"] == 1


def test_detector_only_date_is_reported():
    report = reconcile(
        manual_entries=[],
        bars_60m=short_bundle(),
        start=DAY,
        end=DAY,
    )
    assert report["detector_only_dates"] == [DAY.isoformat()]
    assert report["summary"]["passed"] is False


def test_gap_open_entry_price_mismatch_fails():
    from tests.test_detector_322_first_live import bar

    bars = short_bundle(bar(10, 88, 95, 80, 90))
    report = reconcile(
        manual_entries=[entry(entry_price=92, gap_open=False)],
        bars_60m=bars,
        start=DAY,
        end=DAY,
    )
    assert report["summary"]["passed"] is False
    assert {row["field"] for row in report["level_mismatches"]} == {
        "entry_price",
        "gap_open",
    }
