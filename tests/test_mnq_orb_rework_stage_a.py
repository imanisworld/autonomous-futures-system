from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.mnq_orb_rework_stage_a import (
    _aggregate_15m,
    detect_events,
)


def _bar(start, o, h, l, c, volume=100.0):
    return Bar(
        start=start,
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=float(volume),
    )


def _opening_bars():
    start = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)  # 09:30 ET
    rows = []
    prices = [
        (95, 98, 94, 97),
        (97, 99, 95, 98),
        (98, 100, 96, 99),
        (99, 100, 97, 99),
        (99, 100, 96, 98),
        (98, 99, 95, 99),
    ]
    for i, (o, h, l, c) in enumerate(prices):
        rows.append(_bar(start + timedelta(minutes=5 * i), o, h, l, c))
    return rows


def test_aggregate_15m_is_session_aligned():
    bars = _opening_bars()
    agg = _aggregate_15m(bars)
    assert len(agg) == 2
    assert agg[0].start_utc == bars[0].start_utc
    assert agg[0].open == bars[0].open
    assert agg[0].high == 100
    assert agg[0].low == 94
    assert agg[0].close == bars[2].close


def test_same_bar_orb_sweep_rejection_is_short():
    bars = _opening_bars()
    t = bars[-1].start_utc + timedelta(minutes=5)
    # OR30 high=100, low=94. Wick above 100, close back inside.
    bars += [
        _bar(t, 99, 101, 98, 99),
        _bar(t + timedelta(minutes=5), 99, 100, 98, 99),
    ]
    events = detect_events(
        day=datetime(2026, 9, 1).date(),
        native_bars=bars,
        history_native=_opening_bars() * 4,
        or_minutes=30,
        confirm_minutes=5,
    )
    assert any(e.family == "WICK_REJECTION" and e.direction == "SHORT" for e in events)


def test_failed_upside_breakout_creates_bounded_inverse_short():
    bars = _opening_bars()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars += [
        _bar(t, 99, 102, 99, 101),  # causal upside breakout
        _bar(t + timedelta(minutes=5), 101, 101, 97, 99),  # first close back inside
        _bar(t + timedelta(minutes=10), 99, 100, 97, 98),
    ]
    events = detect_events(
        day=datetime(2026, 9, 1).date(),
        native_bars=bars,
        history_native=_opening_bars() * 4,
        or_minutes=30,
        confirm_minutes=5,
    )
    assert any(e.family == "BREAKOUT" and e.direction == "LONG" for e in events)
    inverses = [
        e for e in events
        if e.family == "FAILED_BREAKOUT_INVERSE" and e.direction == "SHORT"
    ]
    assert len(inverses) == 1
    assert inverses[0].trigger_price == 99


def test_failed_breakout_after_30_minutes_does_not_create_inverse():
    bars = _opening_bars()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars.append(_bar(t, 99, 102, 99, 101))
    for i in range(1, 7):
        bars.append(_bar(t + timedelta(minutes=5 * i), 101, 102, 100.5, 101))
    # First inside close is 35 minutes after breakout close.
    bars.append(_bar(t + timedelta(minutes=35), 101, 101, 97, 99))
    events = detect_events(
        day=datetime(2026, 9, 1).date(),
        native_bars=bars,
        history_native=_opening_bars() * 4,
        or_minutes=30,
        confirm_minutes=5,
    )
    assert not any(e.family == "FAILED_BREAKOUT_INVERSE" for e in events)
