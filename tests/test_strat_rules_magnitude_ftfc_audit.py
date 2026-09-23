from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scripts.strat_rules_magnitude_ftfc_audit import (
    Bar,
    Opens,
    Seam,
    _geometry,
    detect,
    is_gap,
    is_hammer,
    is_shooter,
    trading_date,
)


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(ts=ts, open=o, high=h, low=l, close=c, market_condition=None, trading_date=trading_date(ts))


def _series(start: datetime, n: int, base: float = 100.0) -> list[Bar]:
    return [_bar(start + timedelta(minutes=15 * k), base + k, base + k + 1, base + k - 1, base + k + 0.5) for k in range(n)]


def test_trading_date_rolls_at_1800_et_and_skips_weekend():
    # 2026-09-18 (Fri) 22:00Z = 18:00 EDT -> next weekday is Mon 09-21
    assert trading_date(datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)) == "2026-09-21"
    assert trading_date(datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)) == "2026-09-21"  # Sun 18:00 ET
    assert trading_date(datetime(2026, 9, 21, 20, 45, tzinfo=timezone.utc)) == "2026-09-21"
    assert trading_date(datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc)) == "2026-09-22"


def test_hammer_and_shooter_definitions():
    t = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    assert is_hammer(_bar(t, 9.8, 10.0, 7.0, 9.9))           # both in top third
    assert not is_hammer(_bar(t, 8.0, 10.0, 7.0, 9.9))       # open below top third
    assert is_shooter(_bar(t, 7.2, 10.0, 7.0, 7.1))
    assert not is_shooter(_bar(t, 7.2, 10.0, 7.0, 8.5))
    assert not is_hammer(_bar(t, 5.0, 5.0, 5.0, 5.0))        # zero range


def test_gap_detection_allows_session_reopen_only():
    a = _bar(datetime(2026, 9, 21, 20, 45, tzinfo=timezone.utc), 1, 1, 1, 1)  # 16:45 ET
    reopen = _bar(datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc), 1, 1, 1, 1)  # 18:00 ET
    hole = _bar(datetime(2026, 9, 21, 14, 30, tzinfo=timezone.utc), 1, 1, 1, 1)
    prior = _bar(datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc), 1, 1, 1, 1)
    assert not is_gap(a, reopen)
    assert is_gap(prior, hole)


def test_ftfc_is_causal_future_bars_do_not_change_state():
    start = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)  # Sun 18:00 ET, trading date 08-31
    bars = _series(start, 200)
    opens = Opens(bars, [])
    before = [opens.state(i) for i in range(len(bars))]
    for cut in (50, 120):
        mutated = bars[:cut] + [replace(b, open=b.open * 3, high=b.high * 3, low=b.low / 3, close=b.close * 3) for b in bars[cut:]]
        after = Opens(mutated, [])
        assert [after.state(i) for i in range(cut)] == before[:cut]


def test_ftfc_opens_never_after_decision_bar():
    start = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    bars = _series(start, 300)
    opens = Opens(bars, [])
    for i in range(len(bars)):
        for j in opens.opens_at(i).values():
            assert j is None or bars[j].ts <= bars[i].ts


def test_ftfc_back_adjusts_opens_across_seam():
    start = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    bars = _series(start, 20)
    seam_ts = bars[10].ts
    # after the seam, prices jump +50: without adjustment everything would read UP
    bars = bars[:10] + [replace(b, open=b.open + 50, high=b.high + 50, low=b.low + 50, close=b.close + 50) for b in bars[10:]]
    adjusted = Opens(bars, [Seam(ts=seam_ts, gap=50.0)])
    unadjusted = Opens(bars, [])
    j = adjusted.opens_at(15)["day"]
    assert j == 0
    assert adjusted._offset(bars[15].ts) - adjusted._offset(bars[j].ts) == 50.0
    assert unadjusted._offset(bars[15].ts) == 0.0


def test_geometry_strat_vs_observer_for_2_2_reversal():
    t0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=15), 100, 110, 100, 108),     # t-2 (magnitude high 110)
        _bar(t0 + timedelta(minutes=30), 104, 106, 98, 99),       # t-1 = 2d
        _bar(t0 + timedelta(minutes=45), 100, 107, 99, 106.5),    # t   = 2u
    ]
    entry, stop, target = _geometry("rev_2_2", "A", "LONG", bars, 3, 0.25)
    assert (entry, stop, target) == (106.25, 97.75, 106.25 + 2 * 8.5)
    entry, stop, target = _geometry("rev_2_2", "S", "LONG", bars, 3, 0.25)
    assert (entry, stop, target) == (106.25, 98.75, 110.0)   # stop beyond t, target = t-2 high


def test_geometry_1_2_2_uses_motherbar_and_2_1_2_stop_beyond_inside_bar():
    t0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    bars = [
        _bar(t0, 100, 120, 90, 100),                              # t-3 motherbar
        _bar(t0 + timedelta(minutes=15), 100, 115, 95, 100),      # t-2 inside
        _bar(t0 + timedelta(minutes=30), 100, 110, 93, 94),       # t-1
        _bar(t0 + timedelta(minutes=45), 95, 111, 94, 110),       # t
    ]
    assert _geometry("rev_1_2_2", "S", "LONG", bars, 3, 0.25)[2] == 120
    assert _geometry("rev_2_1_2", "S", "LONG", bars, 3, 0.25)[1] == 93 - 0.25


def test_detect_skips_pattern_spanning_a_gap():
    t0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    shape = [(100, 101, 99, 100), (100, 110, 100, 108), (104, 106, 98, 99), (100, 107, 99, 106.5)]
    contiguous = [_bar(t0 + timedelta(minutes=15 * k), *ohlc) for k, ohlc in enumerate(shape)]
    holed = contiguous[:2] + [replace(b, ts=b.ts + timedelta(minutes=15)) for b in contiguous[2:]]
    for series, expected in ((contiguous, None), (holed, "GAP")):
        sigs = detect(series, [], Opens(series, []), 0.25)
        rev = [s for s in sigs.get(3, []) if s.setup == "rev_2_2"]
        assert rev and rev[0].skip == expected
