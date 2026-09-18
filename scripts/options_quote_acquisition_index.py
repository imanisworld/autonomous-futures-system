#!/usr/bin/env python3
"""Build an outcome-independent decision index for historical option-quote acquisition.

The index selects only by structural family and universe membership. It never
reads outcome/MFE/P&L fields when deciding membership, so a later quote pull can
be defined before option outcomes are known.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

_REQUIRED = (
    "symbol",
    "session_date",
    "bar_time",
    "direction",
    "family",
    "episode_id",
    "trigger_level",
    "invalidation_level",
    "trigger_time",
    "first_seen_time",
    "in_20_universe",
    "quality_flags",
    "source_file",
)


def _true(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _number_text(value: str, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite")
    return result


def build_acquisition_rows(
    rows: Iterable[Mapping[str, str]],
    *,
    family: str,
    require_primary20: bool = True,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        missing = [field for field in _REQUIRED if field not in row]
        if missing:
            raise ValueError(f"population row missing required fields: {','.join(missing)}")
        if row["family"] != family:
            continue
        in_primary20 = _true(row["in_20_universe"])
        if require_primary20 and not in_primary20:
            continue
        decision_ts = str(row["first_seen_time"]).strip()
        if not decision_ts:
            raise ValueError("first_seen_time is required for selected rows")
        episode_id = str(row["episode_id"]).strip()
        symbol = str(row["symbol"]).strip().upper()
        direction = str(row["direction"]).strip().upper()
        if not episode_id or not symbol or direction not in {"LONG", "SHORT"}:
            raise ValueError("selected row has invalid identity/direction")
        decision_id = f"{family}|{episode_id}|{decision_ts}"
        if decision_id in seen:
            raise ValueError(f"duplicate quote acquisition decision_id: {decision_id}")
        seen.add(decision_id)
        selected.append(
            {
                "decision_id": decision_id,
                "family": family,
                "symbol": symbol,
                "direction": direction,
                "session_date": str(row["session_date"]).strip(),
                "decision_ts": decision_ts,
                "decision_basis": "first_sight",
                "bar_time": str(row["bar_time"]).strip(),
                "trigger_time": str(row["trigger_time"]).strip(),
                "trigger_level": _number_text(row["trigger_level"], "trigger_level"),
                "invalidation_level": _number_text(row["invalidation_level"], "invalidation_level"),
                "episode_id": episode_id,
                "in_primary20": in_primary20,
                "quality_flags": str(row["quality_flags"]).strip(),
                "underlying_source_file": str(row["source_file"]).strip(),
            }
        )
    return sorted(selected, key=lambda item: (str(item["decision_ts"]), str(item["symbol"]), str(item["decision_id"])))


def acquisition_index_bytes(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def build_from_csv(path: Path, *, family: str, require_primary20: bool = True) -> tuple[list[dict[str, object]], bytes]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("population CSV has no header")
        absent = [field for field in _REQUIRED if field not in reader.fieldnames]
        if absent:
            raise ValueError(f"population CSV missing required columns: {','.join(absent)}")
        rows = build_acquisition_rows(reader, family=family, require_primary20=require_primary20)
    if not rows:
        raise ValueError("quote acquisition population is empty")
    return rows, acquisition_index_bytes(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population-csv", type=Path, required=True)
    parser.add_argument("--family", default="STRAT_212_REVERSAL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    rows, payload = build_from_csv(args.population_csv, family=args.family)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"rows={len(rows)}")
    print(f"sha256={hashlib.sha256(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
