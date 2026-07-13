"""
tests/test_stocks_tqqq_sqqq_backtest_v2_july13_fixture.py

Historical-fixture validation for stocks_advisory/tqqq_sqqq_backtest_v2.py,
using the REAL QQQ/TQQQ/SQQQ 2026-07-13 bars already used (and hashed) for
the paper-proof window's non-official validation record (see
data/stocks_advisory_paper_proof/VALIDATION_EVIDENCE_2026-07-13.md). The
same three CSVs are committed here under tests/fixtures/ so this test is
reproducible in any checkout/CI, not dependent on the local, gitignored
paper-proof data directory.

Proves three things the operator's validation requirement calls for:
1. Lane 1 (the real, unmodified paper-harness engine) reproduces the
   already-known result for this date exactly: NO_TRADE, "QQQ is inside
   the first-hour range".
2. Records whatever Lane 2 does (trigger or not) for this specific day,
   and the exact first eligible trigger timestamp if it does.
3. No-lookahead, directly: re-running evaluate_day_v2() against the day's
   bars truncated just past any candidate trigger index reproduces the
   identical decision the full-day run makes -- bars beyond the trigger
   point never change an earlier decision.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.tqqq_sqqq_backtest_v2 import V2Config, evaluate_day_v2, _evaluate_lane1

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "stocks_advisory_v2_july13"

# The frozen, actual paper-proof-window thresholds (see
# data/stocks_advisory_paper_proof/PROOF_MANIFEST.md) -- not a new choice
# made for this test.
_CONFIG = V2Config(
    allowed_max_gap_percent=2.0,
    allowed_min_first_hour_range=1.0,
    allowed_max_first_hour_range=10.0,
)


def _load_july13_day():
    qqq = load_bars_from_csv(str(_FIXTURES_DIR / "QQQ_2026-07-13.csv"))
    tqqq = load_bars_from_csv(str(_FIXTURES_DIR / "TQQQ_2026-07-13.csv"))
    sqqq = load_bars_from_csv(str(_FIXTURES_DIR / "SQQQ_2026-07-13.csv"))
    sessions, report = build_day_sessions(qqq, tqqq, sqqq)
    day = next((s for s in sessions if s.date == "2026-07-13"), None)
    assert day is not None, f"2026-07-13 did not build as a session: {report.excluded_dates}"
    return day


def test_lane1_reproduces_the_known_july13_result():
    day = _load_july13_day()
    lane1 = _evaluate_lane1(day, _CONFIG)
    assert lane1.skipped
    assert lane1.skipped_reason == "QQQ is inside the first-hour range"


def test_full_day_v2_result_and_lane2_trigger_timestamp():
    day = _load_july13_day()
    result = evaluate_day_v2(day, _CONFIG)
    # Lane 1 read NO_TRADE, so the day-level result is whatever Lane 2 did.
    assert result.lane == "lane2"
    if result.skipped:
        print(f"\nJuly 13 Lane 2: no trigger -- reason: {result.skipped_reason}")
    else:
        print(
            f"\nJuly 13 Lane 2: TRIGGERED -- direction={result.direction}, "
            f"entry_time={result.entry_time}, entry_price={result.entry_price}"
        )
    # Whichever it is, it must be deterministic and reproducible.
    result_again = evaluate_day_v2(day, _CONFIG)
    assert result.skipped == result_again.skipped
    assert result.direction == result_again.direction
    assert result.entry_time == result_again.entry_time


def test_no_lookahead_truncation_proof():
    """The literal 'verify the rule would trigger without using future
    candles' requirement: for every index that could plausibly be a
    Lane 2 candidate bar, truncating the day's bars to just past that
    index (enough for a fill bar, never more) must reproduce the exact
    same decision as the full day's bars would produce up to that same
    point -- proving no bar beyond the truncation point could have
    changed an earlier-available decision."""
    day = _load_july13_day()
    full_result = evaluate_day_v2(day, _CONFIG)

    # Walk every plausible truncation point in the 11:00-15:00 window and
    # confirm truncating there never *retroactively* changes what the
    # decision would have been through that point -- i.e. re-running on
    # the prefix either matches "no signal yet" or matches the full run's
    # eventual trigger, never something inconsistent with it.
    total_bars = len(day.qqq_bars)
    for cut in range(20, total_bars):
        truncated_day = dataclasses.replace(
            day,
            qqq_bars=day.qqq_bars[:cut],
            tqqq_bars=day.tqqq_bars[:cut],
            sqqq_bars=day.sqqq_bars[:cut],
        )
        truncated_result = evaluate_day_v2(truncated_day, _CONFIG)
        if not full_result.skipped and not truncated_result.skipped:
            # Once both see a trigger, it must be the identical trigger --
            # a later bar must never change which bar/direction/price an
            # earlier evaluation already committed to.
            assert truncated_result.direction == full_result.direction
            assert truncated_result.entry_time == full_result.entry_time
            assert truncated_result.entry_price == full_result.entry_price
        if not truncated_result.skipped:
            # A prefix that already found a trigger must have that same
            # trigger confirmed by the full day -- never invented from
            # data that a shorter run couldn't have seen.
            assert not full_result.skipped
