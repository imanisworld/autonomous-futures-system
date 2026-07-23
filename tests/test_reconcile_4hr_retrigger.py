import csv
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.reconcile_4hr_retrigger import (
    ManualSample,
    ReconciliationInputError,
    load_bars_jsonl,
    load_manual_samples,
    reconcile,
)


ET = ZoneInfo("America/New_York")
MONDAY = date(2026, 1, 5)
TUESDAY = date(2026, 1, 6)


def _bar(day, hour, minute, open_, high, low, close):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _calls_bundle():
    bars_4h = [
        _bar(MONDAY, 16, 0, 95, 100, 90, 95),
        _bar(TUESDAY, 4, 0, 90, 95, 85, 88),
    ]
    bars_5m = [
        _bar(TUESDAY, 8, minute, 90, 94, 89, 91)
        for minute in range(0, 60, 5)
    ]
    bars_5m += [
        _bar(TUESDAY, 9, minute, 90, 94, 89, 91)
        for minute in range(0, 30, 5)
    ]
    bars_5m[1] = _bar(TUESDAY, 8, 5, 93, 96, 92, 96)
    bars_5m[2] = _bar(TUESDAY, 8, 10, 96, 97, 93, 93)
    bars_5m.append(_bar(TUESDAY, 9, 30, 90, 91, 88, 90))
    bars_1h = [_bar(TUESDAY, 8, 0, 90, 94, 88, 91)]
    return bars_4h, bars_5m, bars_1h


def _sample(**updates):
    values = {
        "eval_date": TUESDAY,
        "instrument": "MNQ",
        "direction": "LONG",
        "expected_entry_trigger": 95.0,
        "expected_stop_reference": 88.0,
        "expected_target": 100.0,
        "source_row": 2,
    }
    values.update(updates)
    return ManualSample(**values)


def _run(samples=None, expected_count=1):
    samples = samples or [_sample()]
    bars_4h, bars_5m, bars_1h = _calls_bundle()
    return reconcile(
        manual_samples=samples,
        bars_4h=bars_4h,
        bars_5m=bars_5m,
        bars_1h=bars_1h,
        start=TUESDAY,
        end=max(sample.eval_date for sample in samples),
        instrument="MNQ",
        expected_manual_count=expected_count,
    )


def test_exact_date_direction_and_levels_pass():
    report = _run()
    assert report["research_only"] is True
    assert report["summary"] == {
        "manual_count": 1,
        "detector_positive_count": 1,
        "true_positives": 1,
        "false_positives": 0,
        "false_negatives": 0,
        "true_positive_rate": 1.0,
        "false_positive_rate": 0.0,
        "manual_count_matches": True,
        "coverage_complete": True,
        "passed": True,
    }
    assert report["true_positive_dates"] == ["2026-01-06"]


def test_direction_mismatch_counts_as_false_positive_and_false_negative():
    report = _run([_sample(direction="SHORT")])
    assert report["summary"]["true_positives"] == 0
    assert report["summary"]["false_positives"] == 1
    assert report["summary"]["false_negatives"] == 1
    assert report["summary"]["passed"] is False
    assert report["direction_mismatches"][0]["date"] == "2026-01-06"


def test_expected_level_mismatch_fails_gate():
    report = _run([_sample(expected_stop_reference=87.75)])
    assert report["summary"]["true_positive_rate"] == 1.0
    assert report["summary"]["passed"] is False
    assert report["level_mismatches"] == [
        {
            "date": "2026-01-06",
            "field": "stop_reference",
            "expected": 87.75,
            "actual": 88.0,
            "source_row": 2,
        }
    ]


def test_expected_manual_count_mismatch_fails_closed():
    report = _run(expected_count=32)
    assert report["summary"]["manual_count_matches"] is False
    assert report["summary"]["passed"] is False


def test_manual_date_without_signal_is_false_negative():
    wednesday = date(2026, 1, 7)
    report = _run(
        [
            _sample(),
            _sample(
                eval_date=wednesday,
                expected_entry_trigger=None,
                expected_stop_reference=None,
                expected_target=None,
                source_row=3,
            ),
        ],
        expected_count=2,
    )
    assert report["manual_only_dates"] == ["2026-01-07"]
    assert report["summary"]["false_negatives"] == 1
    assert report["summary"]["true_positive_rate"] == 0.5
    assert report["summary"]["coverage_complete"] is False


def test_incomplete_bar_coverage_fails_even_when_manual_date_matches():
    bars_4h, bars_5m, bars_1h = _calls_bundle()
    report = reconcile(
        manual_samples=[_sample()],
        bars_4h=bars_4h,
        bars_5m=bars_5m[:3] + bars_5m[-1:],
        bars_1h=bars_1h,
        start=TUESDAY,
        end=TUESDAY,
        instrument="MNQ",
        expected_manual_count=1,
    )
    assert report["summary"]["true_positive_rate"] == 1.0
    assert report["summary"]["coverage_complete"] is False
    assert report["summary"]["passed"] is False
    assert report["coverage_issues"] == [
        {"date": "2026-01-06", "missing": ["5m_08:00-09:25_complete"]}
    ]


def test_load_manual_samples_rejects_duplicate_instrument_date(tmp_path):
    path = tmp_path / "manual.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "instrument", "direction"])
        writer.writerow(["2026-01-06", "MNQ", "LONG"])
        writer.writerow(["2026-01-06", "MNQ", "LONG"])
    with pytest.raises(ReconciliationInputError, match="duplicate MNQ date"):
        load_manual_samples(path, "MNQ")


def test_load_manual_samples_filters_other_instruments(tmp_path):
    path = tmp_path / "manual.csv"
    path.write_text(
        "date,instrument,direction\n"
        "2026-01-06,MES,SHORT\n"
        "2026-01-07,MNQ,LONG\n"
    )
    samples = load_manual_samples(path, "mnq")
    assert len(samples) == 1
    assert samples[0].eval_date == date(2026, 1, 7)


def test_load_bars_requires_timezone_aware_timestamp(tmp_path):
    path = tmp_path / "bars.jsonl"
    path.write_text(
        json.dumps(
            {
                "ts": "2026-01-06T09:30:00",
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1,
            }
        )
        + "\n"
    )
    with pytest.raises(ReconciliationInputError, match="include a timezone"):
        load_bars_jsonl(path)


def test_load_bars_accepts_epoch_milliseconds(tmp_path):
    path = tmp_path / "bars.jsonl"
    path.write_text(
        json.dumps(
            {
                "ts": 1767711600000,
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1,
            }
        )
        + "\n"
    )
    bars = load_bars_jsonl(path)
    assert bars[0]["ts"].tzinfo is not None
