"""Offline reconciliation for the pure 4HR Re-Trigger detector.

This module is deliberately research-only.  It imports the pure detector and
standard-library modules; it has no strategy-engine, risk, broker, execution,
configuration, environment, or deployment dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from research.detector_4hr_retrigger import detect_4hr_retrigger


ET = ZoneInfo("America/New_York")


class ReconciliationInputError(ValueError):
    """An input cannot support an auditable reconciliation."""


@dataclass(frozen=True)
class ManualSample:
    eval_date: date
    instrument: str
    direction: str
    expected_entry_trigger: float | None = None
    expected_stop_reference: float | None = None
    expected_target: float | None = None
    source_row: int | None = None


def _optional_float(value: str | None, field: str, row_number: int) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ReconciliationInputError(
            f"row {row_number}: {field} must be numeric when provided"
        ) from exc
    if not math.isfinite(parsed):
        raise ReconciliationInputError(
            f"row {row_number}: {field} must be finite when provided"
        )
    return parsed


def load_manual_samples(path: str | Path, instrument: str) -> list[ManualSample]:
    """Load the external researcher's dated sample list.

    Required CSV columns are ``date``, ``instrument``, and ``direction``.
    Optional expected level columns make price-level reconciliation possible
    without requiring them for the first date-set comparison.
    """

    wanted_instrument = instrument.upper()
    samples: list[ManualSample] = []
    seen_dates: set[date] = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "instrument", "direction"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ReconciliationInputError(
                f"manual sample CSV is missing columns: {', '.join(sorted(missing))}"
            )
        for row_number, row in enumerate(reader, start=2):
            row_instrument = (row.get("instrument") or "").strip().upper()
            if row_instrument != wanted_instrument:
                continue
            try:
                eval_date = date.fromisoformat((row.get("date") or "").strip())
            except ValueError as exc:
                raise ReconciliationInputError(
                    f"row {row_number}: date must be YYYY-MM-DD"
                ) from exc
            direction = (row.get("direction") or "").strip().upper()
            if direction not in {"LONG", "SHORT"}:
                raise ReconciliationInputError(
                    f"row {row_number}: direction must be LONG or SHORT"
                )
            if eval_date in seen_dates:
                raise ReconciliationInputError(
                    f"row {row_number}: duplicate {wanted_instrument} date {eval_date}"
                )
            seen_dates.add(eval_date)
            samples.append(
                ManualSample(
                    eval_date=eval_date,
                    instrument=row_instrument,
                    direction=direction,
                    expected_entry_trigger=_optional_float(
                        row.get("expected_entry_trigger"),
                        "expected_entry_trigger",
                        row_number,
                    ),
                    expected_stop_reference=_optional_float(
                        row.get("expected_stop_reference"),
                        "expected_stop_reference",
                        row_number,
                    ),
                    expected_target=_optional_float(
                        row.get("expected_target"), "expected_target", row_number
                    ),
                    source_row=row_number,
                )
            )
    if not samples:
        raise ReconciliationInputError(
            f"manual sample CSV has no rows for instrument {wanted_instrument}"
        )
    return sorted(samples, key=lambda sample: sample.eval_date)


def _parse_timestamp(value: Any, source: Path, line_number: int) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationInputError(
            f"{source}:{line_number}: ts must be an ISO-8601 string or epoch"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReconciliationInputError(
            f"{source}:{line_number}: invalid ISO-8601 ts"
        ) from exc
    if parsed.tzinfo is None:
        raise ReconciliationInputError(
            f"{source}:{line_number}: ts must include a timezone"
        )
    return parsed


def load_bars_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load one timeframe of OHLCV bars from JSON Lines."""

    source = Path(path)
    bars: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReconciliationInputError(
                    f"{source}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise ReconciliationInputError(
                    f"{source}:{line_number}: bar must be a JSON object"
                )
            parsed: dict[str, Any] = {
                "ts": _parse_timestamp(row.get("ts"), source, line_number)
            }
            for field in ("open", "high", "low", "close"):
                value = row.get(field)
                if isinstance(value, bool):
                    raise ReconciliationInputError(
                        f"{source}:{line_number}: {field} must be numeric"
                    )
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ReconciliationInputError(
                        f"{source}:{line_number}: {field} must be numeric"
                    ) from exc
                if not math.isfinite(number):
                    raise ReconciliationInputError(
                        f"{source}:{line_number}: {field} must be finite"
                    )
                parsed[field] = number
            if "volume" in row:
                parsed["volume"] = row["volume"]
            bars.append(parsed)
    if not bars:
        raise ReconciliationInputError(f"{source}: no bars found")
    return sorted(bars, key=lambda bar: bar["ts"])


def _study_dates(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise ReconciliationInputError("end date must not precede start date")
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _signal_for_json(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in signal.items()
    }


def _coverage_issues(
    *,
    study_dates: list[date],
    bars_4h: list[dict[str, Any]],
    bars_5m: list[dict[str, Any]],
    bars_1h: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return missing-data defects that could suppress detector positives.

    The reconciliation cannot infer exchange holidays safely. Callers must
    explicitly exclude known closed sessions; every remaining study date must
    have the minimum bars needed to evaluate a setup without silently treating
    absent data as "no signal."
    """

    four_am_dates = {
        local.date()
        for bar in bars_4h
        if (local := bar["ts"].astimezone(ET)).hour == 4 and local.minute == 0
    }
    preopen_slots: dict[date, set[tuple[int, int]]] = {}
    nine_thirty_dates: set[date] = set()
    for bar in bars_5m:
        local = bar["ts"].astimezone(ET)
        if (local.hour, local.minute) == (9, 30):
            nine_thirty_dates.add(local.date())
        if (
            local.minute % 5 == 0
            and ((local.hour == 8) or (local.hour == 9 and local.minute < 30))
        ):
            preopen_slots.setdefault(local.date(), set()).add(
                (local.hour, local.minute)
            )
    eight_am_1h_dates = {
        local.date()
        for bar in bars_1h
        if (local := bar["ts"].astimezone(ET)).hour == 8 and local.minute == 0
    }

    issues: list[dict[str, Any]] = []
    for eval_date in study_dates:
        missing: list[str] = []
        if eval_date not in four_am_dates:
            missing.append("4h_04:00")
        if len(preopen_slots.get(eval_date, set())) < 18:
            missing.append("5m_08:00-09:25_complete")
        if eval_date not in nine_thirty_dates:
            missing.append("5m_09:30")
        if eval_date not in eight_am_1h_dates:
            missing.append("1h_08:00")
        if missing:
            issues.append({"date": eval_date.isoformat(), "missing": missing})
    return issues


def _level_mismatches(
    sample: ManualSample, signal: dict[str, Any], tolerance: float
) -> list[dict[str, Any]]:
    comparisons = (
        ("entry_trigger", sample.expected_entry_trigger),
        ("stop_reference", sample.expected_stop_reference),
        ("target", sample.expected_target),
    )
    mismatches: list[dict[str, Any]] = []
    for field, expected in comparisons:
        if expected is None:
            continue
        actual = signal.get(field)
        if not isinstance(actual, (int, float)) or abs(float(actual) - expected) > tolerance:
            mismatches.append(
                {
                    "date": sample.eval_date.isoformat(),
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                    "source_row": sample.source_row,
                }
            )
    return mismatches


def reconcile(
    *,
    manual_samples: list[ManualSample],
    bars_4h: list[dict[str, Any]],
    bars_5m: list[dict[str, Any]],
    bars_1h: list[dict[str, Any]],
    start: date,
    end: date,
    instrument: str,
    expected_manual_count: int | None = None,
    price_tolerance: float = 1e-9,
    excluded_dates: set[date] | None = None,
) -> dict[str, Any]:
    """Run the detector over the full study range and compare dated signals."""

    normalized_instrument = instrument.upper()
    if expected_manual_count is not None and expected_manual_count < 1:
        raise ReconciliationInputError("expected manual count must be positive")
    if not math.isfinite(price_tolerance) or price_tolerance < 0:
        raise ReconciliationInputError(
            "price tolerance must be finite and non-negative"
        )
    manual_by_date = {sample.eval_date: sample for sample in manual_samples}
    if any(sample.instrument != normalized_instrument for sample in manual_samples):
        raise ReconciliationInputError("manual samples contain an unexpected instrument")
    outside = [
        sample.eval_date.isoformat()
        for sample in manual_samples
        if not start <= sample.eval_date <= end
    ]
    if outside:
        raise ReconciliationInputError(
            "manual sample dates outside study range: " + ", ".join(outside)
        )
    excluded_dates = set(excluded_dates or ())
    excluded_manual_dates = sorted(excluded_dates & manual_by_date.keys())
    if excluded_manual_dates:
        raise ReconciliationInputError(
            "manual sample dates cannot be excluded: "
            + ", ".join(value.isoformat() for value in excluded_manual_dates)
        )
    all_study_dates = list(_study_dates(start, end))
    unknown_exclusions = sorted(excluded_dates - set(all_study_dates))
    if unknown_exclusions:
        raise ReconciliationInputError(
            "excluded dates outside weekday study range: "
            + ", ".join(value.isoformat() for value in unknown_exclusions)
        )
    evaluation_dates = [
        eval_date for eval_date in all_study_dates if eval_date not in excluded_dates
    ]
    coverage_issues = _coverage_issues(
        study_dates=evaluation_dates,
        bars_4h=bars_4h,
        bars_5m=bars_5m,
        bars_1h=bars_1h,
    )

    detected: dict[date, dict[str, Any]] = {}
    invalidated: dict[date, dict[str, Any]] = {}
    for eval_date in evaluation_dates:
        result = detect_4hr_retrigger(
            bars_4h, bars_5m, bars_1h, eval_date, normalized_instrument
        )
        if result and result.get("signal") is True:
            detected[eval_date] = result
        elif result and result.get("signal") is False:
            invalidated[eval_date] = result

    true_positive_dates: list[str] = []
    direction_mismatches: list[dict[str, Any]] = []
    level_mismatches: list[dict[str, Any]] = []
    for eval_date in sorted(manual_by_date.keys() & detected.keys()):
        sample = manual_by_date[eval_date]
        signal = detected[eval_date]
        if signal.get("direction") == sample.direction:
            true_positive_dates.append(eval_date.isoformat())
            level_mismatches.extend(
                _level_mismatches(sample, signal, price_tolerance)
            )
        else:
            direction_mismatches.append(
                {
                    "date": eval_date.isoformat(),
                    "manual_direction": sample.direction,
                    "detector_direction": signal.get("direction"),
                    "source_row": sample.source_row,
                }
            )

    detector_only_dates = sorted(detected.keys() - manual_by_date.keys())
    manual_only_dates = sorted(manual_by_date.keys() - detected.keys())
    true_positives = len(true_positive_dates)
    false_positives = len(detector_only_dates) + len(direction_mismatches)
    false_negatives = len(manual_only_dates) + len(direction_mismatches)
    detector_positive_count = len(detected)
    manual_count = len(manual_by_date)
    true_positive_rate = true_positives / manual_count
    false_positive_rate = (
        false_positives / detector_positive_count if detector_positive_count else 0.0
    )
    count_matches = (
        expected_manual_count is None or manual_count == expected_manual_count
    )
    passed = (
        count_matches
        and true_positive_rate >= 0.95
        and false_positive_rate <= 0.10
        and not direction_mismatches
        and not level_mismatches
        and not coverage_issues
    )

    return {
        "schema_version": "4hr_retrigger_reconciliation.v1",
        "research_only": True,
        "instrument": normalized_instrument,
        "study_range": {"start": start.isoformat(), "end": end.isoformat()},
        "excluded_dates": sorted(value.isoformat() for value in excluded_dates),
        "thresholds": {
            "minimum_true_positive_rate": 0.95,
            "maximum_false_positive_rate": 0.10,
            "price_tolerance": price_tolerance,
            "expected_manual_count": expected_manual_count,
        },
        "summary": {
            "manual_count": manual_count,
            "detector_positive_count": detector_positive_count,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "true_positive_rate": true_positive_rate,
            "false_positive_rate": false_positive_rate,
            "manual_count_matches": count_matches,
            "coverage_complete": not coverage_issues,
            "passed": passed,
        },
        "true_positive_dates": true_positive_dates,
        "detector_only_dates": [value.isoformat() for value in detector_only_dates],
        "manual_only_dates": [value.isoformat() for value in manual_only_dates],
        "direction_mismatches": direction_mismatches,
        "level_mismatches": level_mismatches,
        "coverage_issues": coverage_issues,
        "invalidated_dates": {
            key.isoformat(): _signal_for_json(value)
            for key, value in sorted(invalidated.items())
        },
        "detector_signals": {
            key.isoformat(): _signal_for_json(value)
            for key, value in sorted(detected.items())
        },
        "manual_samples": [
            {
                **asdict(sample),
                "eval_date": sample.eval_date.isoformat(),
            }
            for sample in manual_samples
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline, fail-closed 4HR Re-Trigger detector reconciliation"
    )
    parser.add_argument("--manual-samples", required=True, type=Path)
    parser.add_argument("--bars-4h", required=True, type=Path)
    parser.add_argument("--bars-5m", required=True, type=Path)
    parser.add_argument("--bars-1h", required=True, type=Path)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--expected-manual-count", type=int)
    parser.add_argument("--price-tolerance", type=float, default=1e-9)
    parser.add_argument(
        "--exclude-date",
        action="append",
        default=[],
        type=date.fromisoformat,
        help="known closed weekday session; repeat for multiple dates",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        samples = load_manual_samples(args.manual_samples, args.instrument)
        report = reconcile(
            manual_samples=samples,
            bars_4h=load_bars_jsonl(args.bars_4h),
            bars_5m=load_bars_jsonl(args.bars_5m),
            bars_1h=load_bars_jsonl(args.bars_1h),
            start=args.start,
            end=args.end,
            instrument=args.instrument,
            expected_manual_count=args.expected_manual_count,
            price_tolerance=args.price_tolerance,
            excluded_dates=set(args.exclude_date),
        )
    except (OSError, ReconciliationInputError) as exc:
        print(f"input error: {exc}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0 if report["summary"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
