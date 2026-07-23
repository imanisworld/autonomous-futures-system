"""Offline reconciliation gate for the pure MNQ 3-2-2 First Live detector."""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from research.detector_322_first_live import detect_322_first_live


@dataclass(frozen=True)
class ManualEntry:
    eval_date: date
    direction: str
    entry_trigger: float | None
    entry_price: float | None
    stop: float | None
    target: float | None
    gap_open: bool | None


class ReconciliationInputError(ValueError):
    pass


def _number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ReconciliationInputError("manual levels must be finite")
    return parsed


def _boolean(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise ReconciliationInputError("gap_open must be true or false")


def load_manual(path: str | Path) -> list[ManualEntry]:
    rows = []
    with Path(path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                ManualEntry(
                    eval_date=date.fromisoformat(row["date"]),
                    direction=row["direction"].upper(),
                    entry_trigger=_number(row.get("expected_entry_trigger")),
                    entry_price=_number(row.get("expected_entry_price")),
                    stop=_number(row.get("expected_stop")),
                    target=_number(row.get("expected_target")),
                    gap_open=_boolean(row.get("expected_gap_open")),
                )
            )
    if len({row.eval_date for row in rows}) != len(rows):
        raise ReconciliationInputError("manual entry dates must be unique")
    return rows


def load_bars(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row["ts"] = datetime.fromisoformat(row["ts"])
            if row["ts"].tzinfo is None:
                raise ReconciliationInputError("bar timestamps must be timezone-aware")
            rows.append(row)
    return rows


def reconcile(
    *,
    manual_entries: list[ManualEntry],
    bars_60m: list[dict],
    start: date,
    end: date,
    expected_manual_count: int | None = None,
    excluded_dates: set[date] | None = None,
    tolerance: float = 1e-9,
) -> dict:
    manual = {row.eval_date: row for row in manual_entries}
    if expected_manual_count is not None and len(manual) != expected_manual_count:
        raise ReconciliationInputError(
            f"manual count {len(manual)} != expected {expected_manual_count}"
        )
    excluded_dates = set(excluded_dates or ())
    if excluded_dates & manual.keys():
        raise ReconciliationInputError("manual entry dates cannot be excluded")

    detected = {}
    invalidated = {}
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in excluded_dates:
            result = detect_322_first_live(bars_60m, current, "MNQ")
            if result and result.get("signal") is True:
                detected[current] = result
            elif result:
                invalidated[current] = result
        current += timedelta(days=1)

    true_positive_dates = []
    direction_mismatches = []
    level_mismatches = []
    for eval_date in sorted(manual.keys() & detected.keys()):
        expected = manual[eval_date]
        actual = detected[eval_date]
        if expected.direction != actual["direction"]:
            direction_mismatches.append(
                {
                    "date": eval_date.isoformat(),
                    "expected": expected.direction,
                    "actual": actual["direction"],
                }
            )
            continue
        true_positive_dates.append(eval_date.isoformat())
        comparisons = (
            ("entry_trigger", expected.entry_trigger, actual["entry_trigger"]),
            ("entry_price", expected.entry_price, actual["entry_price"]),
            ("stop_reference", expected.stop, actual["stop_reference"]),
            ("target", expected.target, actual["target"]),
        )
        for field, expected_value, actual_value in comparisons:
            if expected_value is not None and abs(expected_value - actual_value) > tolerance:
                level_mismatches.append(
                    {
                        "date": eval_date.isoformat(),
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
        if expected.gap_open is not None and expected.gap_open != actual["gap_open"]:
            level_mismatches.append(
                {
                    "date": eval_date.isoformat(),
                    "field": "gap_open",
                    "expected": expected.gap_open,
                    "actual": actual["gap_open"],
                }
            )

    detector_only = sorted(detected.keys() - manual.keys())
    manual_only = sorted(manual.keys() - detected.keys())
    tp = len(true_positive_dates)
    fp = len(detector_only) + len(direction_mismatches)
    fn = len(manual_only) + len(direction_mismatches)
    tpr = tp / len(manual) if manual else 0.0
    fpr = fp / len(detected) if detected else 0.0
    passed = (
        bool(manual)
        and tpr >= 0.95
        and fpr <= 0.10
        and not direction_mismatches
        and not level_mismatches
    )
    return {
        "schema_version": 1,
        "instrument": "MNQ",
        "study_range": {"start": start.isoformat(), "end": end.isoformat()},
        "summary": {
            "manual_count": len(manual),
            "detector_positive_count": len(detected),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
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
    parser.add_argument("--manual-entries", required=True)
    parser.add_argument("--bars-60m", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--expected-manual-count", type=int)
    parser.add_argument("--exclude-date", action="append", type=date.fromisoformat, default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = reconcile(
        manual_entries=load_manual(args.manual_entries),
        bars_60m=load_bars(args.bars_60m),
        start=args.start,
        end=args.end,
        expected_manual_count=args.expected_manual_count,
        excluded_dates=set(args.exclude_date),
    )
    Path(args.output).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
