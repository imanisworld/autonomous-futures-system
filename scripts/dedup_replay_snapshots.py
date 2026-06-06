#!/usr/bin/env python3
"""Deduplicate cumulative replay snapshots into one JSONL file per date."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-glob", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    import glob

    files = sorted(Path(path) for path in glob.glob(args.input_glob))
    by_key: dict[tuple[str, str], dict] = {}
    duplicate_rows = 0
    conflicting_ohlc = 0

    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row.get("instrument") or ""), str(row.get("timestamp") or ""))
            previous = by_key.get(key)
            if previous is not None:
                duplicate_rows += 1
                if any(previous.get(field) != row.get(field) for field in ("open", "high", "low", "close")):
                    conflicting_ohlc += 1
            by_key[key] = row

    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in by_key.values():
        by_date[str(row["timestamp"])[:10]].append(row)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for day, rows in by_date.items():
        rows.sort(key=lambda row: (str(row["timestamp"]), str(row.get("instrument") or "")))
        path = output_dir / f"day_{day}.jsonl"
        path.write_text(
            "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({
        "source_files": len(files),
        "source_rows": len(by_key) + duplicate_rows,
        "unique_rows": len(by_key),
        "duplicate_rows": duplicate_rows,
        "conflicting_ohlc_duplicates": conflicting_ohlc,
        "dates": len(by_date),
        "output_dir": str(output_dir),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
