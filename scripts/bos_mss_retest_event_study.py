#!/usr/bin/env python3
"""Run the research-only BOS/MSS -> first-retest event study.

Phase 1 is MNQ. The same code accepts MES for a later portability pass only;
do not combine instruments in one run.

This script does not define trades or use PaperBroker/RiskEngine/DecisionEngine.
It measures causal structure events and subsequent price behavior only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.bos_mss_retest_event_study import (  # noqa: E402
    DEFAULT_HORIZONS_MINUTES,
    DEFAULT_SWING,
    build_event_records,
    row_ts,
    summarize_records,
)

ALLOWED_INSTRUMENTS = ("MNQ", "MES")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rows(
    data_dir: Path,
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[list[dict], list[dict]]:
    files = sorted(data_dir.glob("*.jsonl"))
    if not files:
        raise ValueError(f"no JSONL files found in {data_dir}")

    rows: list[dict] = []
    manifest_files: list[dict] = []
    for path in files:
        used = 0
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{lineno}: row must be a JSON object")
            ts = row_ts(row)
            day = ts.date()
            if start_date is not None and day < start_date:
                continue
            if end_date is not None and day > end_date:
                continue
            rows.append(row)
            used += 1
        if used:
            manifest_files.append(
                {
                    "path": str(path.resolve()),
                    "sha256": _sha256(path),
                    "rows_used": used,
                }
            )
    if not rows:
        raise ValueError("date filter produced zero 5m rows")
    return rows, manifest_files


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", choices=ALLOWED_INSTRUMENTS, default="MNQ")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args(argv)

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)
    if start_date is not None and end_date is not None and end_date < start_date:
        parser.error("--end-date cannot be before --start-date")

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    try:
        rows, input_files = _load_rows(
            data_dir,
            start_date=start_date,
            end_date=end_date,
        )
        records, build_meta = build_event_records(
            rows,
            swing=DEFAULT_SWING,
            horizons_minutes=DEFAULT_HORIZONS_MINUTES,
            retest_max_minutes=120,
        )
        for record in records:
            record["instrument"] = args.instrument
        summary = summarize_records(records, horizons_minutes=DEFAULT_HORIZONS_MINUTES)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = out_dir / f"{args.instrument.lower()}_bos_mss_retest_events.jsonl"
    summary_path = out_dir / f"{args.instrument.lower()}_bos_mss_retest_summary.json"
    manifest_path = out_dir / f"{args.instrument.lower()}_bos_mss_retest_manifest.json"

    records_text = "".join(
        json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in records
    )
    records_path.write_text(records_text, encoding="utf-8")

    summary_doc = {
        "instrument": args.instrument,
        "study_phase": "MNQ_PHASE_1" if args.instrument == "MNQ" else "MES_PORTABILITY_PHASE_2",
        "swing_length": DEFAULT_SWING,
        "horizons_minutes": list(DEFAULT_HORIZONS_MINUTES),
        "retest_max_minutes": 120,
        "build": build_meta,
        "summary": summary,
    }
    summary_path.write_text(
        json.dumps(summary_doc, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "instrument": args.instrument,
        "research_only": True,
        "trade_strategy_defined": False,
        "choch_claimed": False,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "input_files": input_files,
        "input_rows": len(rows),
        "records_file": str(records_path.resolve()),
        "records_sha256": hashlib.sha256(records_text.encode("utf-8")).hexdigest(),
        "records": len(records),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary_doc, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
