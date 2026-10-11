#!/usr/bin/env python3
"""Recompute the published table from episode rows.

This does not refetch bars and does not import the scorer. It checks that
results.json matches episodes.jsonl, and that the two entries do not share
an absolute stop distance.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLISHED = {
    "htf_pullback": {
        "eligible_episodes": 7237,
        "actionable_trigger_pct": 14.4,
        "r1_before_stop_pct": 46.4,
        "r2_before_stop_pct": 0.9,
        "baseline_r1_before_stop_pct": 29.5,
        "verdict": "PROMISING BUT UNPROVEN",
    },
    "failed_break": {
        "eligible_episodes": 11944,
        "actionable_trigger_pct": 58.6,
        "r1_before_stop_pct": 44.3,
        "r2_before_stop_pct": 2.6,
        "baseline_r1_before_stop_pct": 33.8,
        "verdict": "PROMISING BUT UNPROVEN",
    },
    "compression_expansion": {
        "eligible_episodes": 7260,
        "actionable_trigger_pct": 36.7,
        "r1_before_stop_pct": 41.3,
        "r2_before_stop_pct": 0.5,
        "baseline_r1_before_stop_pct": 42.7,
        "verdict": "REJECT",
    },
}


def rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


def recompute(rows: list[dict]) -> dict:
    eligible = len(rows)
    executed = [row for row in rows if row["strategy"]["class"] == "EXECUTED"]
    base = [row for row in rows if row["baseline"]["class"] == "EXECUTED"]
    r1 = sum(1 for row in executed if row["strategy"]["result"] in {"R1", "R2"})
    r2 = sum(1 for row in executed if row["strategy"]["result"] == "R2")
    b1 = sum(1 for row in base if row["baseline"]["result"] in {"R1", "R2"})
    paired = [
        row["strategy"]["risk_price"] / row["baseline"]["risk_price"]
        for row in rows
        if row["strategy"].get("risk_price") and row["baseline"].get("risk_price")
    ]
    return {
        "eligible_episodes": eligible,
        "actionable_trigger_pct": rate(len(executed), eligible),
        "r1_before_stop_pct": rate(r1, len(executed)),
        "r2_before_stop_pct": rate(r2, len(executed)),
        "baseline_r1_before_stop_pct": rate(b1, len(base)),
        "paired_executed": len(paired),
        "median_paired_strategy_over_baseline_risk": round(statistics.median(paired), 3) if paired else None,
    }


def main() -> int:
    results = json.loads((ROOT / "results.json").read_text())
    grouped: dict[str, list[dict]] = {name: [] for name in PUBLISHED}
    for line in (ROOT / "episodes.jsonl").open():
        row = json.loads(line)
        grouped[row["family"]].append(row)
    failures = []
    for name, expected in PUBLISHED.items():
        fresh = recompute(grouped[name])
        stored = results["summaries"][name]
        for key, value in expected.items():
            if key == "verdict":
                actual = stored["verdict"]
            else:
                actual = fresh[key]
                if stored[key] != actual:
                    failures.append(f"{name} results.json {key}={stored[key]} episodes={actual}")
            if actual != value:
                failures.append(f"{name} {key} reproduced {actual} published {value}")
        comparison = stored["baseline_comparability"]
        if comparison["same_absolute_stop_distance"] is not False:
            failures.append(f"{name} claimed identical stop distance")
        if comparison["median_paired_strategy_over_baseline_risk"] != fresh["median_paired_strategy_over_baseline_risk"]:
            failures.append(f"{name} risk ratio mismatch")
        print(name, fresh["median_paired_strategy_over_baseline_risk"], "paired", fresh["paired_executed"])
    if failures:
        print("\n".join(failures))
        return 1
    print("reproduced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
