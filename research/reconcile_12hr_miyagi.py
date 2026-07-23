"""Offline reconciliation gate for the pure 12HR Miyagi detector."""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from research.detector_12hr_miyagi import detect_12hr_miyagi


@dataclass(frozen=True)
class ManualSetup:
    eval_date: date
    instrument: str
    direction: str
    expected_entry_trigger: float | None
    expected_target: float | None
    expected_target_2: float | None


class ReconciliationInputError(ValueError):
    pass


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ReconciliationInputError("expected levels must be finite")
    return parsed


def load_manual(path: str | Path) -> list[ManualSetup]:
    rows = []
    with Path(path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                ManualSetup(
                    eval_date=date.fromisoformat(row["date"]),
                    instrument=row["instrument"].upper(),
                    direction=row["direction"].upper(),
                    expected_entry_trigger=_optional_float(row.get("expected_entry_trigger")),
                    expected_target=_optional_float(row.get("expected_target")),
                    expected_target_2=_optional_float(row.get("expected_target_2")),
                )
            )
    if len({row.eval_date for row in rows}) != len(rows):
        raise ReconciliationInputError("manual setup dates must be unique")
    if any(row.direction not in {"LONG", "SHORT"} for row in rows):
        raise ReconciliationInputError("direction must be LONG or SHORT")
    return rows


def load_bars(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                row["ts"] = datetime.fromisoformat(row["ts"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ReconciliationInputError(
                    f"{path}:{line_number} has invalid ts"
                ) from exc
            if row["ts"].tzinfo is None:
                raise ReconciliationInputError(
                    f"{path}:{line_number} ts must be timezone-aware"
                )
            rows.append(row)
    return rows


def reconcile(
    *,
    manual_setups: list[ManualSetup],
    bars_12h: list[dict],
    bars_5m: list[dict],
    bars_60m: list[dict],
    start: date,
    end: date,
    instrument: str,
    expected_manual_count: int | None = None,
    price_tolerance: float = 1e-9,
    excluded_dates: set[date] | None = None,
) -> dict[str, Any]:
    instrument = instrument.upper()
    manual = {row.eval_date: row for row in manual_setups}
    excluded_dates = set(excluded_dates or ())
    if expected_manual_count is not None and len(manual) != expected_manual_count:
        raise ReconciliationInputError(
            f"manual count {len(manual)} != expected {expected_manual_count}"
        )
    if any(row.instrument != instrument for row in manual.values()):
        raise ReconciliationInputError("manual setups contain an unexpected instrument")
    if excluded_dates & manual.keys():
        raise ReconciliationInputError("manual setup dates cannot be excluded")

    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in excluded_dates:
            dates.append(current)
        current += timedelta(days=1)

    detected: dict[date, dict] = {}
    invalidated: dict[date, dict] = {}
    for eval_date in dates:
        result = detect_12hr_miyagi(
            bars_12h, bars_5m, bars_60m, eval_date, instrument
        )
        if result and result.get("signal") is True:
            detected[eval_date] = result
        elif result and result.get("signal") is False:
            invalidated[eval_date] = result

    direction_mismatches = []
    level_mismatches = []
    true_positive_dates = []
    for eval_date in sorted(manual.keys() & detected.keys()):
        sample = manual[eval_date]
        signal = detected[eval_date]
        if signal["direction"] != sample.direction:
            direction_mismatches.append(
                {
                    "date": eval_date.isoformat(),
                    "manual_direction": sample.direction,
                    "detector_direction": signal["direction"],
                }
            )
            continue
        true_positive_dates.append(eval_date.isoformat())
        for expected_name, actual_name in (
            ("expected_entry_trigger", "entry_trigger"),
            ("expected_target", "target"),
            ("expected_target_2", "target_2"),
        ):
            expected = getattr(sample, expected_name)
            if expected is not None and abs(expected - signal[actual_name]) > price_tolerance:
                level_mismatches.append(
                    {
                        "date": eval_date.isoformat(),
                        "field": actual_name,
                        "expected": expected,
                        "actual": signal[actual_name],
                    }
                )

    detector_only = sorted(detected.keys() - manual.keys())
    manual_only = sorted(manual.keys() - detected.keys())
    true_positives = len(true_positive_dates)
    false_positives = len(detector_only) + len(direction_mismatches)
    false_negatives = len(manual_only) + len(direction_mismatches)
    tpr = true_positives / len(manual) if manual else 0.0
    fpr = false_positives / len(detected) if detected else 0.0
    passed = (
        bool(manual)
        and tpr >= 0.95
        and fpr <= 0.10
        and not direction_mismatches
        and not level_mismatches
    )
    return {
        "schema_version": 1,
        "instrument": instrument,
        "study_range": {"start": start.isoformat(), "end": end.isoformat()},
        "thresholds": {
            "minimum_true_positive_rate": 0.95,
            "maximum_false_positive_rate": 0.10,
            "expected_manual_count": expected_manual_count,
            "price_tolerance": price_tolerance,
        },
        "summary": {
            "manual_count": len(manual),
            "detector_positive_count": len(detected),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "true_positive_rate": tpr,
            "false_positive_rate": fpr,
            "passed": passed,
        },
        "true_positive_dates": true_positive_dates,
        "detector_only_dates": [value.isoformat() for value in detector_only],
        "manual_only_dates": [value.isoformat() for value in manual_only],
        "direction_mismatches": direction_mismatches,
        "level_mismatches": level_mismatches,
        "invalidated_dates": {
            value.isoformat(): result for value, result in sorted(invalidated.items())
        },
        "detector_signals": {
            value.isoformat(): result for value, result in sorted(detected.items())
        },
        "excluded_dates": sorted(value.isoformat() for value in excluded_dates),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-setups", required=True)
    parser.add_argument("--bars-12h", required=True)
    parser.add_argument("--bars-5m", required=True)
    parser.add_argument("--bars-60m", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--expected-manual-count", type=int)
    parser.add_argument("--exclude-date", action="append", type=date.fromisoformat, default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = reconcile(
        manual_setups=load_manual(args.manual_setups),
        bars_12h=load_bars(args.bars_12h),
        bars_5m=load_bars(args.bars_5m),
        bars_60m=load_bars(args.bars_60m),
        start=args.start,
        end=args.end,
        instrument=args.instrument,
        expected_manual_count=args.expected_manual_count,
        excluded_dates=set(args.exclude_date),
    )
    Path(args.output).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
