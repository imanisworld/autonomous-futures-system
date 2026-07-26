"""Driver for the MNQ 60M 3-2-2 expanded-evidence study (2026-07-26).

Scoped exclusively to the 60M 3-2-2 lane. Orchestrates (does not reimplement
the core rules of):
  - research/bars_322_polygon_loader.py  (15m->60m resample, 5m loader)
  - research/detector_322_first_live.py  (pure detector, ported verbatim)
  - research/replay_322_honest_fill.py   (corrected honest-fill replay)

Produces JSON evidence artifacts under docs/strategy-rules/evidence_322/ for:
  - the legacy-semantics reproduction (provenance-only, uses the OLD
    eligible[-1]-close EOD behavior, run out of a throwaway copy of the
    original codex file -- NOT part of this repo's canonical code)
  - the corrected canonical baseline (2024-07-02..2026-06-26)
  - the out-of-sample expansion window, if any exists
  - the combined evidence set
  - 1/2/3/4-tick slippage sensitivity for each group

This module does not read `market_condition`, `trend_direction`,
`trend_strength`, or any TRENDING gate for candidate generation, filtering, or
scoring -- those fields are only read (read-only) for the Robustness Question
#10 regime-dependency breakdown, entirely after trade outcomes are already
final.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from research.bars_322_polygon_loader import (
    load_15m_day,
    load_5m_day,
    load_60m_day_et,
    resample_60m_et,
    trading_days,
)
from research.detector_322_first_live import detect_322_first_live
from research.replay_322_honest_fill import build_sensitivity, run_replay


REPO_ROOT = Path(__file__).resolve().parents[1]
POLYGON_15M = REPO_ROOT / "data" / "replay_polygon"
POLYGON_5M = REPO_ROOT / "data" / "replay_polygon_5m"
CORPUS_V1_15M = REPO_ROOT / "data" / "replay_corpus_v1"


def detect_candidates(
    cache_15m: Path, instrument: str, start: date, end: date
) -> list[dict]:
    """Run the pure detector over every weekday in [start, end], returning
    dicts already shaped for replay_322_honest_fill's in-memory functions
    (skips the CSV round-trip entirely -- signals are built directly from
    detector output, not re-parsed from a file)."""
    candidates = []
    for day in trading_days(start, end):
        bars_60m = load_60m_day_et(cache_15m, instrument, day)
        if not bars_60m:
            continue
        result = detect_322_first_live(bars_60m, day, instrument)
        if result and result.get("signal") is True:
            candidates.append(
                {
                    "date": day,
                    "direction": result["direction"],
                    "entry_trigger": result["entry_trigger"],
                    "entry_price": result["entry_price"],
                    "stop": result["stop_reference"],
                    "target": result["target"],
                    "gap_open": result["gap_open"],
                }
            )
    return candidates


def load_5m_bars_for_range(
    cache_5m: Path, instrument: str, start: date, end: date
) -> list[dict]:
    bars: list[dict] = []
    for day in trading_days(start, end):
        bars.extend(load_5m_day(cache_5m, instrument, day))
    return bars


def run_group(
    label: str,
    candidates: list[dict],
    bars_5m: list[dict],
    *,
    study_start: date,
    study_end: date,
) -> dict:
    if not candidates:
        return {
            "label": label,
            "study_range": {"start": study_start.isoformat(), "end": study_end.isoformat()},
            "candidate_count": 0,
            "note": "0 candidates in this group",
        }
    replay_signals = [
        {
            "date": c["date"],
            "direction": c["direction"],
            "entry_trigger": c["entry_trigger"],
            "entry_price": c["entry_price"],
            "stop": c["stop"],
            "target": c["target"],
            "gap_open": c["gap_open"],
        }
        for c in candidates
    ]
    base = run_replay(
        replay_signals, bars_5m, study_start=study_start, study_end=study_end, slippage_ticks=2.0
    )
    sensitivity = build_sensitivity(
        replay_signals, bars_5m, study_start=study_start, study_end=study_end
    )
    return {
        "label": label,
        "study_range": {"start": study_start.isoformat(), "end": study_end.isoformat()},
        "candidate_count": len(candidates),
        "base_case_slippage_ticks": 2,
        "base_case": base,
        "slippage_sensitivity": {
            key: {
                "overall": value["overall"],
                "halves": value["halves"],
                "directions": value["directions"],
            }
            for key, value in sensitivity.items()
        },
    }


def main() -> None:
    out_dir = REPO_ROOT / "docs" / "strategy-rules" / "evidence_322"
    out_dir.mkdir(parents=True, exist_ok=True)

    baseline_start = date(2024, 7, 2)
    baseline_end = date(2026, 6, 26)

    print(f"[group1] detecting candidates {baseline_start}..{baseline_end} (corrected EOD contract)")
    group1_candidates = detect_candidates(POLYGON_15M, "MNQ", baseline_start, baseline_end)
    print(f"[group1] {len(group1_candidates)} candidates")
    bars_5m_group1 = load_5m_bars_for_range(POLYGON_5M, "MNQ", baseline_start, baseline_end)
    print(f"[group1] {len(bars_5m_group1)} 5m bars loaded")
    group1 = run_group(
        "Group 1: corrected canonical baseline (2024-07-02..2026-06-26)",
        group1_candidates,
        bars_5m_group1,
        study_start=baseline_start,
        study_end=baseline_end,
    )
    (out_dir / "group1_corrected_baseline.json").write_text(
        json.dumps(group1, indent=2, default=str) + "\n"
    )
    print(f"[group1] wrote {out_dir / 'group1_corrected_baseline.json'}")

    # Group 2: out-of-sample expansion window. Per Step 2 finding, there is no
    # 5-minute cache covering 2026-06-27..2026-07-23 (the only window
    # replay_corpus_v1 extends past the baseline's 5m-cache cutoff), so honest
    # fill replay cannot be performed there. Group 2 is reported empty with an
    # explicit reason -- see OOS_EXPANSION_BLOCKED_BY_DATA_COVERAGE.md.
    group2 = {
        "label": "Group 2: newly added out-of-sample historical evidence",
        "candidate_count": 0,
        "note": "OOS EXPANSION BLOCKED BY DATA COVERAGE",
        "reason": (
            "replay_corpus_v1/MNQ extends 2025-07-24..2026-07-23 (15-minute, "
            "full-day bars only). replay_polygon_5m/MNQ (the only 5-minute "
            "MNQ cache on this machine) stops at 2026-06-26, identical to "
            "replay_polygon/MNQ's 15-minute cache. No 5-minute-granularity "
            "MNQ cache exists anywhere in this environment for "
            "2026-06-27..2026-07-23. The 60M 3-2-2 honest-fill replay "
            "requires 5-minute bars to recover the causal entry crossing "
            "in the 10:00-11:00 ET window and to resolve exits including "
            "the exact 15:55-16:00 ET day-only-flatten bar; substituting "
            "15-minute bars would require guessing intra-15-minute path and "
            "was explicitly ruled out as inventing optimistic fills / "
            "lookahead. No pre-2024-07-01 5-minute MNQ cache exists either. "
            "Group 2 is therefore empty by data availability, not by any "
            "property of the strategy."
        ),
    }
    (out_dir / "group2_oos_expansion.json").write_text(
        json.dumps(group2, indent=2, default=str) + "\n"
    )
    print("[group2] OOS EXPANSION BLOCKED BY DATA COVERAGE -- 0 candidates")

    # Group 3: combined. Identical to Group 1 since Group 2 is empty.
    group3 = dict(group1)
    group3["label"] = "Group 3: combined evidence (identical to Group 1 -- Group 2 is empty)"
    (out_dir / "group3_combined.json").write_text(
        json.dumps(group3, indent=2, default=str) + "\n"
    )
    print("[group3] combined == group1 (group2 empty)")


if __name__ == "__main__":
    main()
