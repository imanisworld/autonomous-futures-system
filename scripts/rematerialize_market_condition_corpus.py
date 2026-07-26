"""Rematerialize replay JSONL with canonical engine-facing market condition.

This is a provenance-preserving migration for corpora produced after the
``reconstructed_market_condition`` fields were introduced but before those
fields controlled ReplayEngine.  It never mutates the source corpus.

Usage:
    python3 scripts/rematerialize_market_condition_corpus.py \
        --input data/replay_corpus_v1 \
        --output /tmp/replay_corpus_v1_market_condition_parity \
        --report /tmp/replay_corpus_v1_market_condition_parity_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pine_market_condition import (
    RECONSTRUCTED,
    RECONSTRUCTED_UNVALIDATED_INIT,
    UNAVAILABLE_SYNTHETIC_VOLUME,
    UNAVAILABLE_WARMUP,
)

_AVAILABLE_STATUSES = {RECONSTRUCTED, RECONSTRUCTED_UNVALIDATED_INIT}
_UNAVAILABLE_STATUSES = {UNAVAILABLE_WARMUP, UNAVAILABLE_SYNTHETIC_VOLUME}


def rematerialize(input_dir: Path, output_dir: Path) -> dict:
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("input and output directories must differ")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")

    source_files = sorted(input_dir.rglob("*.jsonl"))
    if not source_files:
        raise ValueError(f"no JSONL files found under {input_dir}")

    before = Counter()
    after = Counter()
    statuses = Counter()
    bars = 0
    comparable = 0
    before_mismatches = 0
    after_mismatches = 0
    trending_removed = 0
    trending_added = 0
    unavailable = 0

    for source_path in source_files:
        relative = source_path.relative_to(input_dir)
        output_path = output_dir / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with source_path.open(encoding="utf-8") as source, output_path.open(
            "w", encoding="utf-8"
        ) as destination:
            for line_no, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if "reconstructed_market_condition" not in row:
                    raise ValueError(
                        f"{source_path}:{line_no} lacks reconstructed_market_condition"
                    )

                legacy = row.get("legacy_market_condition", row.get("market_condition"))
                canonical = row.get("reconstructed_market_condition")
                status = row.get("reconstructed_market_condition_status", "UNKNOWN")
                if canonical is None and status not in _UNAVAILABLE_STATUSES:
                    raise ValueError(
                        f"{source_path}:{line_no} unavailable condition has invalid "
                        f"status {status!r}"
                    )
                if canonical is not None and status not in _AVAILABLE_STATUSES:
                    raise ValueError(
                        f"{source_path}:{line_no} canonical condition has invalid "
                        f"status {status!r}"
                    )

                bars += 1
                before[str(legacy) if legacy is not None else "null"] += 1
                after[str(canonical) if canonical is not None else "null"] += 1
                statuses[str(status)] += 1

                if canonical is None:
                    unavailable += 1
                else:
                    comparable += 1
                    if legacy != canonical:
                        before_mismatches += 1
                    if legacy == "TRENDING" and canonical != "TRENDING":
                        trending_removed += 1
                    if legacy != "TRENDING" and canonical == "TRENDING":
                        trending_added += 1

                row["legacy_market_condition"] = legacy
                row["market_condition"] = canonical
                row["market_condition_status"] = status
                if row["market_condition"] != row["reconstructed_market_condition"]:
                    after_mismatches += 1
                destination.write(json.dumps(row, separators=(",", ":")) + "\n")

    return {
        "source": str(input_dir),
        "output": str(output_dir),
        "files": len(source_files),
        "bars_compared": bars,
        "comparable_bars": comparable,
        "initialization_or_missing_data_exclusions": unavailable,
        "mismatch_count_before": before_mismatches,
        "mismatch_count_after": after_mismatches,
        "trending_removed": trending_removed,
        "trending_added": trending_added,
        "label_distribution_before": dict(sorted(before.items())),
        "label_distribution_after": dict(sorted(after.items())),
        "condition_status_distribution": dict(sorted(statuses.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    report = rematerialize(args.input, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
