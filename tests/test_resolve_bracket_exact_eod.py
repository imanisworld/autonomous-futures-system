"""Frozen exact-EOD contract for day-only lanes in
scripts/edge_decomposition_audit.resolve_bracket (ledger AFS-0025).

Contract (docs/strategy-rules/60M_322_FirstLive_Rules.md "Common Day-Only
Exit"; docs/prereg-322-trigger-timing-ab-2026-09-18.md L27, L62-63;
execution/day_only_exit.py):
  * resolve stop/target on bars up to and including the exact 15:55 ET 5m bar,
    stop first when one bar touches both, then DAY_ONLY_FLATTEN at its close;
  * exact bar absent -> UNRESOLVED / EOD_BAR_MISSING; no earlier-bar
    substitute; bars after 15:55 ET are never walked.

Synthetic bars only; no corpus.  Timestamps are stored in UTC exactly as the
corpus does and converted to America/New_York by the resolver.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import edge_decomposition_audit as audit

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DAY_ONLY_LANE = audit.LANES["322_mnq"]


def _bars_at(rows_et: list[tuple[datetime, float, float, float, float]]) -> audit.Bars:
    rows = []
    for dt_et, o, h, l, c in rows_et:
        dt = dt_et.astimezone(UTC)
        rows.append({"timestamp": dt.isoformat(), "_dt": dt, "open": o, "high": h, "low": l,
                     "close": c, "session": "new_york"})
    return audit.Bars(
        corpus_dir=Path("."), instrument="MNQ", rows=rows, files=[Path("synthetic")],
        by_ts={r["timestamp"]: i for i, r in enumerate(rows)},
        by_dt={r["_dt"]: i for i, r in enumerate(rows)},
        file_of_idx=[0] * len(rows), bars_per_day=len(rows),
    )


def _run(bars: audit.Bars, idx: int, direction: str, entry: float, stop: float, target: float,
         *, slippage_ticks: float = 1.0) -> dict:
    cand = audit._candidate(DAY_ONLY_LANE, bars, idx, direction, entry, stop, target)
    return audit.resolve_bracket(DAY_ONLY_LANE, bars, cand, fill_model="market",
                                 slippage_ticks=slippage_ticks, tolerance_ticks=32.0)


def _span(start: datetime, end: datetime, ohlc: tuple[float, float, float, float]):
    out, t = [], start
    while t <= end:
        out.append((t, *ohlc))
        t += timedelta(minutes=5)
    return out


# 2025-01-20 (MLK Day) pattern, using the frozen 34-candidate row's own plan:
# SHORT trigger 21658.5, stop 21780.0, target 21538.0, trigger bar 10:40 ET.
# CME equity RTH halted at 13:00 ET and Globex reopened 18:00 ET the SAME ET
# date, so there is no 15:55 bar.  The target is only touched on the 19:50 ET
# evening bar (2025-01-21T00:50Z), as in
# scripts/322_trigger_timing_ab_2026-09-18.json.
MLK = datetime(2025, 1, 20, tzinfo=ET)


def _mlk_bars() -> audit.Bars:
    rows = [(MLK.replace(hour=10, minute=40), 21687.0, 21697.0, 21641.0, 21657.5)]
    rows += _span(MLK.replace(hour=10, minute=45), MLK.replace(hour=12, minute=55),
                  (21660.0, 21700.0, 21600.0, 21650.0))
    rows += _span(MLK.replace(hour=18, minute=0), MLK.replace(hour=19, minute=45),
                  (21600.0, 21640.0, 21560.0, 21590.0))
    rows.append((MLK.replace(hour=19, minute=50), 21560.0, 21565.0, 21530.0, 21540.0))
    return _bars_at(rows)


def test_early_close_without_1555_bar_fails_closed_despite_same_date_evening_bars():
    bars = _mlk_bars()
    # Sanity: fixture has no 15:55 ET bar and has evening bars on the same ET date.
    assert not any(audit._is_eod_bar(r["timestamp"], 5) for r in bars.rows)
    assert bars.rows[-1]["timestamp"] == "2025-01-21T00:50:00+00:00"
    assert bars.et(len(bars.rows) - 1).date() == MLK.date()

    res = _run(bars, 0, "SHORT", 21658.5, 21780.0, 21538.0)

    assert res["status"] == "UNRESOLVED"
    assert res["reason"] == audit.EOD_BAR_MISSING
    # Walk stopped at the first bar after the EOD slot (18:00 ET reopen); no
    # bar after 15:55 ET was used to resolve the trade.
    stopped_at = bars.et(res["exit_idx"])
    assert (stopped_at.hour, stopped_at.minute) == (18, 0)
    assert "exit_bar_ts" not in res and "result" not in res


def test_data_gap_at_1555_is_not_filled_by_a_later_same_date_bar():
    day = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(day.replace(hour=15, minute=40), day.replace(hour=15, minute=50),
                 (100.0, 100.5, 99.5, 100.25))
    rows.append((day.replace(hour=16, minute=0), 100.0, 111.0, 99.5, 110.5))  # would hit target
    res = _run(_bars_at(rows), 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING


def test_no_earlier_bar_is_substituted_as_the_close():
    # Early close at 13:00 ET with nothing afterwards: the 12:55 bar is NOT a
    # flatten bar (no substitution rule is pre-registered). This case also
    # passes on the old date-only guard, because the walk simply runs out of
    # bars. The same-date bars after 15:55 in
    # tests/test_resolve_bracket_exact_eod_qa.py are the checks that fail on
    # that guard.
    day = datetime(2025, 11, 28, tzinfo=ET)
    rows = _span(day.replace(hour=10, minute=40), day.replace(hour=12, minute=55),
                 (100.0, 100.5, 99.5, 100.25))
    res = _run(_bars_at(rows), 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING


def test_exact_1555_bar_touching_both_stop_and_target_resolves_as_stop():
    day = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(day.replace(hour=15, minute=40), day.replace(hour=15, minute=50),
                 (100.0, 100.5, 99.5, 100.25))
    rows.append((day.replace(hour=15, minute=55), 100.0, 111.0, 94.0, 105.0))  # both touched
    bars = _bars_at(rows)
    res = _run(bars, 0, "LONG", 100.0, 95.0, 110.0, slippage_ticks=1.0)
    assert res["status"] == "RESOLVED"
    assert res["exit_reason"] == "STOP_HIT" and res["result"] == "LOSS"
    assert res["exit_bar_ts"] == bars.rows[-1]["timestamp"]
    assert res["fill_entry"] == pytest.approx(100.25)
    assert res["exit_price"] == pytest.approx(95.0 - 0.25)
    assert res["net"] == pytest.approx((94.75 - 100.25) / 0.25 * 0.5 - audit.COMMISSION_ROUND_TRIP)


def test_exact_1555_bar_flattens_at_its_close_and_later_bars_are_ignored():
    day = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(day.replace(hour=15, minute=40), day.replace(hour=15, minute=55),
                 (100.0, 100.5, 99.5, 100.25))
    rows.append((day.replace(hour=18, minute=0), 100.0, 111.0, 99.5, 110.5))  # would hit target
    bars = _bars_at(rows)
    res = _run(bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "RESOLVED"
    assert res["exit_reason"] == audit.DAY_ONLY_EXIT_REASON
    assert res["exit_price"] == pytest.approx(100.25)
    assert res["exit_bar_ts"] == bars.rows[3]["timestamp"]
