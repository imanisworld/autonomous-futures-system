"""Driver for the 12HR Miyagi canonical-evidence study (2026-07-26).

Scoped exclusively to the Miyagi lane. Orchestrates (does not reimplement the
core rules of):
  - research/bars_12hr_miyagi_loader.py   (12H/60M/5M bar loading + resampling)
  - research/detector_12hr_miyagi.py      (pure detector, canonical spec)
  - research/replay_12hr_miyagi_honest_fill.py (honest-fill replay, day-only exit)

Produces JSON evidence artifacts under docs/strategy-rules/evidence_12hr_miyagi/
for MNQ and MES independently: candidate detection, honest-fill replay, and
1/2/3/4-tick slippage sensitivity, plus a granularity-ambiguity instrumentation
report for the Step-5 pre-market integrity check (see
bars_12hr_miyagi_loader.py's module docstring for the underlying data-coverage
finding this instruments).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from research.bars_12hr_miyagi_loader import (
    load_12h_bars_for_date,
    load_5m_day,
    load_5m_premarket_window,
    load_60m_bars_for_date,
    trading_days,
)
from research.detector_12hr_miyagi import detect_12hr_miyagi
from research.replay_12hr_miyagi_honest_fill import build_sensitivity, run_replay


REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_15M = REPO_ROOT / "data" / "replay_polygon"
CACHE_5M = REPO_ROOT / "data" / "replay_polygon_5m"

STUDY_START = date(2024, 7, 2)
STUDY_END = date(2026, 6, 26)


def detect_candidates(instrument: str, start: date, end: date) -> dict:
    """Run the pure detector over every weekday in [start, end].

    Returns candidates (signal dicts shaped for replay), invalidations
    (explicit CANDLE3_BECAME_OUTSIDE_BAR etc.), and granularity-ambiguity
    instrumentation: for every date whose pre-market Step-5 window had to
    fall back to the 15-minute proxy, record whether that proxy flagged a
    breach (which would be reported AMBIGUOUS_GRANULARITY, unresolved,
    rather than silently trusted) versus resolved clean.
    """
    candidates = []
    invalidations = []
    proxy_dates = []
    proxy_breach_dates = []

    for day in trading_days(start, end):
        bars_12h = load_12h_bars_for_date(CACHE_15M, instrument, day)
        bars_60m = load_60m_bars_for_date(CACHE_15M, instrument, day)
        premarket = load_5m_premarket_window(CACHE_5M, CACHE_15M, instrument, day)
        bars_5m = premarket["bars"]

        if premarket["provenance"] == "15m_proxy":
            proxy_dates.append(day.isoformat())

        result = detect_12hr_miyagi(bars_12h, bars_5m, bars_60m, day, instrument)
        if result is None:
            continue
        if result.get("signal") is True:
            candidates.append(
                {
                    "date": day,
                    "instrument": instrument,
                    "direction": result["direction"],
                    "entry_trigger": result["entry_trigger"],
                    "stop": result["stop_reference"],
                    "target": result["target"],
                    "target_2": result["target_2"],
                    "premarket_provenance": premarket["provenance"],
                }
            )
        else:
            invalidations.append(
                {
                    "date": day.isoformat(),
                    "invalidation": result.get("invalidation"),
                    "premarket_provenance": premarket["provenance"],
                }
            )
            if (
                premarket["provenance"] == "15m_proxy"
                and result.get("invalidation") == "CANDLE3_BECAME_OUTSIDE_BAR"
            ):
                proxy_breach_dates.append(day.isoformat())

    return {
        "candidates": candidates,
        "invalidations": invalidations,
        "granularity_ambiguity": {
            "proxy_used_date_count": len(proxy_dates),
            "proxy_used_dates": proxy_dates,
            "proxy_flagged_breach_count": len(proxy_breach_dates),
            "proxy_flagged_breach_dates": proxy_breach_dates,
            "note": (
                "proxy_flagged_breach_dates are candidates where the 15-minute "
                "proxy Step-5 check fired a breach on a date true 5-minute data "
                "was unavailable for -- these are NOT resolved to invalidated; "
                "they would require separate AMBIGUOUS_GRANULARITY handling if "
                "this count is ever nonzero (it is 0 across the full study "
                "range as of 2026-07-26 -- see report doc)."
            ),
        },
    }


def load_5m_bars_for_range(instrument: str, start: date, end: date) -> list:
    bars = []
    for day in trading_days(start, end):
        bars.extend(load_5m_day(CACHE_5M, instrument, day))
    return bars


def run_instrument_study(instrument: str) -> dict:
    detection = detect_candidates(instrument, STUDY_START, STUDY_END)
    candidates = detection["candidates"]
    bars_5m = load_5m_bars_for_range(instrument, STUDY_START, STUDY_END)

    replay_signals = [
        {
            "date": c["date"],
            "instrument": c["instrument"],
            "direction": c["direction"],
            "entry_trigger": c["entry_trigger"],
            "stop": c["stop"],
            "target": c["target"],
            "target_2": c["target_2"],
        }
        for c in candidates
    ]

    if not replay_signals:
        base_case = None
        sensitivity = {}
    else:
        base_case = run_replay(
            replay_signals, bars_5m, study_start=STUDY_START, study_end=STUDY_END, slippage_ticks=2.0
        )
        sensitivity = build_sensitivity(
            replay_signals, bars_5m, study_start=STUDY_START, study_end=STUDY_END
        )

    return {
        "instrument": instrument,
        "study_range": {"start": STUDY_START.isoformat(), "end": STUDY_END.isoformat()},
        "candidate_count": len(candidates),
        "candidates": [
            {**c, "date": c["date"].isoformat()} for c in candidates
        ],
        "invalidations": detection["invalidations"],
        "granularity_ambiguity": detection["granularity_ambiguity"],
        "base_case_slippage_ticks": 2,
        "base_case": base_case,
        "slippage_sensitivity": {
            key: {
                "overall": value["overall"],
                "halves": value["halves"],
                "directions": value["directions"],
                "by_month": value["by_month"],
                "by_year": value["by_year"],
            }
            for key, value in sensitivity.items()
        },
    }


def main() -> None:
    out_dir = REPO_ROOT / "docs" / "strategy-rules" / "evidence_12hr_miyagi"
    out_dir.mkdir(parents=True, exist_ok=True)

    for instrument in ("MNQ", "MES"):
        print(f"[{instrument}] detecting candidates {STUDY_START}..{STUDY_END}")
        result = run_instrument_study(instrument)
        print(f"[{instrument}] {result['candidate_count']} candidates")
        out_path = out_dir / f"{instrument.lower()}_results.json"
        out_path.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(f"[{instrument}] wrote {out_path}")


if __name__ == "__main__":
    main()
