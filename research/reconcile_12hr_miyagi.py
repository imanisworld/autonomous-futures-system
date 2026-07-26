"""Offline reconciliation gate for the pure 12HR Miyagi detector.

Miyagi has no dated manual-sample ground truth anywhere in this repository's
git history (same situation `research/reconcile_322_first_live.py` found for
the 3-2-2 lane -- searched all commits/branches; none found, and the "MNQ
+$102.35 / MES +$25.78 expectancy, n=13/20" figures in
`docs/strategy-rules/12HR_Miyagi_Rules.md` section 9 are external-study
provenance context only, never reproducible ground truth). Unlike 3-2-2,
Miyagi doesn't even have a documented n=32-signal cohort to spot-check
against, so this module leans harder on synthetic coverage:

  1. A synthetic fixture suite exercising every branch of
     `docs/strategy-rules/Detector_Specifications.md`'s Detector 2 spec (valid
     1-3-1 pattern both directions, an inside/outside/inside failure at each
     step, the Candle-3-becomes-outside-bar invalidation, the ambiguous
     exact-equal-at-9:30 edge case, a missing Bar Z, and missing bars
     generally) -- `run_synthetic_suite()` below, reported as a
     branch -> pass/fail coverage table.
  2. A small number (5) of hand-verified real historical MNQ/MES dates,
     manually cross-checked against the raw `data/replay_polygon*` JSONL rows
     (not just re-run through the detector) before being hardcoded here --
     `REAL_DATA_SPOT_CHECKS` below. See
     docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md,
     "Reconciliation -- real-data spot-check detail" for the by-hand
     arithmetic behind each one.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.bars_12hr_miyagi_loader import (
    load_12h_bars_for_date,
    load_5m_premarket_window,
    load_60m_bars_for_date,
)
from research.detector_12hr_miyagi import detect_12hr_miyagi


ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Synthetic fixture suite
# ---------------------------------------------------------------------------


def _bar12(ts, high, low):
    return {"ts": ts, "open": low, "high": high, "low": low, "close": high}


def _bar5(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def _skeleton(eval_date: date) -> dict:
    """Timestamps for the canonical 1-3-1 skeleton anchored on `eval_date`."""
    return {
        "z": datetime(eval_date.year, eval_date.month, eval_date.day, 4, tzinfo=ET) - timedelta(days=2),
        "a": datetime(eval_date.year, eval_date.month, eval_date.day, 16, tzinfo=ET) - timedelta(days=2),
        "b": datetime(eval_date.year, eval_date.month, eval_date.day, 4, tzinfo=ET) - timedelta(days=1),
        "c": datetime(eval_date.year, eval_date.month, eval_date.day, 16, tzinfo=ET) - timedelta(days=1),
        "d": datetime(eval_date.year, eval_date.month, eval_date.day, 4, tzinfo=ET),
    }


def _valid_12h_bars(eval_date: date) -> list:
    ts = _skeleton(eval_date)
    return [
        _bar12(ts["z"], 120, 80),
        _bar12(ts["a"], 110, 90),
        _bar12(ts["b"], 130, 70),
        _bar12(ts["c"], 105, 95),
        _bar12(ts["d"], 103, 97),
    ]


def _clean_premarket(eval_date: date, bar_c_high=105, bar_c_low=95) -> list:
    bars = []
    ts = datetime(eval_date.year, eval_date.month, eval_date.day, 4, 0, tzinfo=ET)
    end = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)
    while ts < end:
        bars.append(_bar5(ts, 100, bar_c_high - 1, bar_c_low + 1, 100))
        ts += timedelta(minutes=5)
    return bars


def _with_930(eval_date: date, open_price: float, premarket=None) -> list:
    bars = list(premarket) if premarket is not None else _clean_premarket(eval_date)
    ts = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)
    bars.append(_bar5(ts, open_price, open_price + 2, open_price - 2, open_price))
    return bars


def _valid_60m_bars(eval_date: date, stop_high=112, stop_low=88) -> list:
    return [
        _bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 6, tzinfo=ET), 150, 50),
        _bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 7, tzinfo=ET), 140, 60),
        _bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 8, tzinfo=ET), stop_high, stop_low),
    ]


def _scenario(name: str, eval_date: date, bars12, bars5, bars60, expect) -> dict:
    actual = detect_12hr_miyagi(bars12, bars5, bars60, eval_date, "MNQ")
    passed = expect(actual)
    return {"branch": name, "passed": bool(passed), "detector_output": actual}


def run_synthetic_suite() -> list:
    eval_date = date(2026, 3, 5)  # arbitrary Thursday, no real-data collision
    results = []

    # 1. Valid SHORT (Bar D 2U)
    results.append(
        _scenario(
            "valid_short_2u",
            eval_date,
            _valid_12h_bars(eval_date),
            _with_930(eval_date, 110),
            _valid_60m_bars(eval_date),
            lambda r: r is not None and r["signal"] is True and r["direction"] == "SHORT",
        )
    )

    # 2. Valid LONG (Bar D 2D)
    results.append(
        _scenario(
            "valid_long_2d",
            eval_date,
            _valid_12h_bars(eval_date),
            _with_930(eval_date, 90),
            _valid_60m_bars(eval_date),
            lambda r: r is not None and r["signal"] is True and r["direction"] == "LONG",
        )
    )

    # 3-6. Each of Bar A/B/C/D missing -> None
    ts = _skeleton(eval_date)
    for key in ("a", "b", "c", "d"):
        bars12 = [b for b in _valid_12h_bars(eval_date) if b["ts"] != ts[key]]
        results.append(
            _scenario(
                f"bar_{key}_missing_returns_none",
                eval_date,
                bars12,
                _with_930(eval_date, 110),
                _valid_60m_bars(eval_date),
                lambda r: r is None,
            )
        )

    # 7. Bar Z missing -> None
    bars12 = [b for b in _valid_12h_bars(eval_date) if b["ts"] != ts["z"]]
    results.append(
        _scenario(
            "bar_z_missing_returns_none",
            eval_date,
            bars12,
            _with_930(eval_date, 110),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 8. Bar C not inside Bar B (step 1 fails)
    bars12 = _valid_12h_bars(eval_date)
    for b in bars12:
        if b["ts"] == ts["c"]:
            b["high"] = 999
    results.append(
        _scenario(
            "bar_c_not_inside_bar_b_returns_none",
            eval_date,
            bars12,
            _with_930(eval_date, 110),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 9. Bar B not outside Bar A (step 2 fails)
    bars12 = _valid_12h_bars(eval_date)
    for b in bars12:
        if b["ts"] == ts["b"]:
            b["low"] = 95  # no longer < Bar A low (90)
    results.append(
        _scenario(
            "bar_b_not_outside_bar_a_returns_none",
            eval_date,
            bars12,
            _with_930(eval_date, 110),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 10. Bar A not inside Bar Z (step 3 fails)
    bars12 = _valid_12h_bars(eval_date)
    for b in bars12:
        if b["ts"] == ts["a"]:
            b["high"] = 999
    results.append(
        _scenario(
            "bar_a_not_inside_bar_z_returns_none",
            eval_date,
            bars12,
            _with_930(eval_date, 110),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 11. Candle 3 becomes outside bar before 9:30 -> explicit invalidation
    premarket = _clean_premarket(eval_date)
    breach_ts = datetime(eval_date.year, eval_date.month, eval_date.day, 6, 0, tzinfo=ET)
    for b in premarket:
        if b["ts"] == breach_ts:
            b["high"] = 106
            b["low"] = 94
    results.append(
        _scenario(
            "candle3_becomes_outside_bar_invalidates",
            eval_date,
            _valid_12h_bars(eval_date),
            _with_930(eval_date, 110, premarket=premarket),
            _valid_60m_bars(eval_date),
            lambda r: r == {"signal": False, "invalidation": "CANDLE3_BECAME_OUTSIDE_BAR"},
        )
    )

    # 12/13. Price exactly equal to Bar C high / low at 9:30 -> ambiguous, None
    for boundary_price, label in ((105, "high"), (95, "low")):
        results.append(
            _scenario(
                f"price_exactly_equals_bar_c_{label}_at_930_is_ambiguous",
                eval_date,
                _valid_12h_bars(eval_date),
                _with_930(eval_date, boundary_price),
                _valid_60m_bars(eval_date),
                lambda r: r is None,
            )
        )

    # 14. Price between Bar C high/low at 9:30 -> None
    results.append(
        _scenario(
            "price_between_bar_c_bounds_at_930_returns_none",
            eval_date,
            _valid_12h_bars(eval_date),
            _with_930(eval_date, 100),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 15. Missing 9:30 bar -> None
    results.append(
        _scenario(
            "missing_930_bar_returns_none",
            eval_date,
            _valid_12h_bars(eval_date),
            _clean_premarket(eval_date),
            _valid_60m_bars(eval_date),
            lambda r: r is None,
        )
    )

    # 16. Missing 60-minute bars before 9:30 -> None
    results.append(
        _scenario(
            "missing_60m_stop_reference_returns_none",
            eval_date,
            _valid_12h_bars(eval_date),
            _with_930(eval_date, 110),
            [],
            lambda r: r is None,
        )
    )

    return results


# ---------------------------------------------------------------------------
# Real-data spot checks
# ---------------------------------------------------------------------------

# Hand-verified against raw `data/replay_polygon{,_5m}` JSONL rows -- see
# docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md for the
# by-hand arithmetic behind each entry. `expected` describes only the
# minimal shape independently confirmed by hand, not the full detector
# output (which is separately asserted to match exactly).
REAL_DATA_SPOT_CHECKS = [
    {
        "instrument": "MNQ",
        "eval_date": date(2024, 8, 22),
        "expected_signal": True,
        "expected_direction": "SHORT",
        "expected_trigger": 19905.875,
        "note": "Hand-confirmed 1-3-1: Z[19743.00,19924.75] A[19785.75,19855.75] "
        "(inside Z) B[19771.25,19982.00] (outside A) C[19865.75,19946.00] "
        "(inside B); trigger=(19946.00+19865.75)/2=19905.875.",
    },
    {
        "instrument": "MNQ",
        "eval_date": date(2024, 10, 11),
        "expected_signal": True,
        "expected_direction": "LONG",
        "expected_trigger": 20420.5,
        "note": "Hand-confirmed 1-3-1: Z[20208.75,20482.25] A[20413.00,20474.50] "
        "(inside Z) B[20301.25,20508.00] (outside A) C[20372.50,20468.50] "
        "(inside B); trigger=(20468.50+20372.50)/2=20420.5.",
    },
    {
        "instrument": "MNQ",
        "eval_date": date(2025, 2, 12),
        "expected_signal": False,
        "expected_invalidation": "CANDLE3_BECAME_OUTSIDE_BAR",
        "note": "Structurally valid 1-3-1 (C=[21766.00,21831.25] inside B) but "
        "the true (non-proxy) 08:30 ET 5-minute bar has high=21848.00 > "
        "C.high and low=21542.75 < C.low -- a genuine single-bar engulf "
        "confirmed directly against the raw JSONL row.",
    },
    {
        "instrument": "MES",
        "eval_date": date(2024, 7, 12),
        "expected_signal": True,
        "expected_direction": "SHORT",
        "expected_trigger": 5638.25,
        "note": "Hand-confirmed 1-3-1: Z[5634.75,5690.50] A[5679.00,5687.25] "
        "(inside Z) B[5629.75,5707.75] (outside A) C[5632.50,5644.00] "
        "(inside B); trigger=(5644.00+5632.50)/2=5638.25. Second calendar day "
        "of the entire 5-minute cache's coverage -- also confirms the "
        "loader's earliest-usable-date behavior.",
    },
    {
        "instrument": "MNQ",
        "eval_date": date(2024, 7, 10),
        "expected_signal": None,  # detector returns None (no pattern)
        "note": "Hand-confirmed NO pattern: Bar A=[20655.50,20748.75] is NOT "
        "inside Bar Z=[20588.00,20688.25] (A.high 20748.75 > Z.high 20688.25) "
        "-- fails at Step 3.",
    },
]


def run_real_data_spot_checks(cache_15m: str | Path, cache_5m: str | Path) -> list:
    out = []
    for case in REAL_DATA_SPOT_CHECKS:
        instrument = case["instrument"]
        eval_date = case["eval_date"]
        bars_12h = load_12h_bars_for_date(cache_15m, instrument, eval_date)
        bars_60m = load_60m_bars_for_date(cache_15m, instrument, eval_date)
        premarket = load_5m_premarket_window(cache_5m, cache_15m, instrument, eval_date)
        result = detect_12hr_miyagi(bars_12h, premarket["bars"], bars_60m, eval_date, instrument)

        if case.get("expected_signal") is None:
            passed = result is None
        elif case["expected_signal"] is False:
            passed = (
                result is not None
                and result.get("signal") is False
                and result.get("invalidation") == case["expected_invalidation"]
            )
        else:
            passed = (
                result is not None
                and result.get("signal") is True
                and result["direction"] == case["expected_direction"]
                and result["entry_trigger"] == case["expected_trigger"]
            )
        out.append(
            {
                "instrument": instrument,
                "eval_date": eval_date.isoformat(),
                "premarket_provenance": premarket["provenance"],
                "passed": passed,
                "detector_output": result,
                "note": case["note"],
            }
        )
    return out


def reconcile(cache_15m: str | Path, cache_5m: str | Path) -> dict:
    synthetic = run_synthetic_suite()
    real_data = run_real_data_spot_checks(cache_15m, cache_5m)
    synthetic_passed = sum(1 for r in synthetic if r["passed"])
    real_passed = sum(1 for r in real_data if r["passed"])
    return {
        "schema_version": 1,
        "strategy": "12HR_MIYAGI",
        "note": (
            "No dated manual-sample ground truth exists for Miyagi anywhere in "
            "this repository's git history. This reconciliation therefore rests "
            "on (a) a synthetic fixture suite covering every branch of the "
            "canonical spec and (b) a small number of hand-verified real "
            "historical dates, not a manual-study CSV comparison."
        ),
        "synthetic_suite": {
            "total": len(synthetic),
            "passed": synthetic_passed,
            "all_passed": synthetic_passed == len(synthetic),
            "coverage_table": [
                {"branch": r["branch"], "passed": r["passed"]} for r in synthetic
            ],
        },
        "real_data_spot_checks": {
            "total": len(real_data),
            "passed": real_passed,
            "all_passed": real_passed == len(real_data),
            "results": real_data,
        },
        "passed": synthetic_passed == len(synthetic) and real_passed == len(real_data),
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-15m", default="data/replay_polygon")
    parser.add_argument("--cache-5m", default="data/replay_polygon_5m")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = reconcile(args.cache_15m, args.cache_5m)
    Path(args.output).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "synthetic": report["synthetic_suite"]["passed"],
                "synthetic_total": report["synthetic_suite"]["total"],
                "real_data": report["real_data_spot_checks"]["passed"],
                "real_data_total": report["real_data_spot_checks"]["total"],
                "passed": report["passed"],
            },
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
