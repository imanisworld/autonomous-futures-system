from datetime import date, datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.mnq_vwap_delayed_failed_reclaim import detect_first_delayed_failure


def _bar(ts, close, high=None, low=None, open_=None, volume=100.0):
    return Bar(
        start=ts,
        open=float(close if open_ is None else open_),
        high=float(close + 0.5 if high is None else high),
        low=float(close - 0.5 if low is None else low),
        close=float(close),
        volume=float(volume),
    )


def _base():
    t = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
    return [_bar(t + timedelta(minutes=5*i), 100.0) for i in range(5)]


def test_immediate_failed_reclaim_is_excluded():
    bars = _base()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars += [
        _bar(t, 103.0, high=103.5, low=100.0),        # reclaim
        _bar(t + timedelta(minutes=5), 99.0),          # immediate failure: excluded
        _bar(t + timedelta(minutes=10), 98.0),
    ]
    assert detect_first_delayed_failure(date(2026, 8, 3), bars) is None


def test_second_bar_failure_is_detected():
    bars = _base()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars += [
        _bar(t, 103.0, high=103.5, low=100.0),        # reclaim
        _bar(t + timedelta(minutes=5), 103.0),         # survives bar 1
        _bar(t + timedelta(minutes=10), 99.0),         # delayed fail bar 2
    ]
    e = detect_first_delayed_failure(date(2026, 8, 3), bars)
    assert e is not None
    assert e.failure_lag_bars == 2
    assert e.trigger_price == 99.0


def test_third_bar_failure_is_detected():
    bars = _base()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars += [
        _bar(t, 103.0, high=103.5, low=100.0),
        _bar(t + timedelta(minutes=5), 103.0),
        _bar(t + timedelta(minutes=10), 102.0),
        _bar(t + timedelta(minutes=15), 99.0),
    ]
    e = detect_first_delayed_failure(date(2026, 8, 3), bars)
    assert e is not None
    assert e.failure_lag_bars == 3


def test_failure_after_third_bar_is_not_admitted():
    bars = _base()
    t = bars[-1].start_utc + timedelta(minutes=5)
    bars += [
        _bar(t, 103.0, high=103.5, low=100.0),
        _bar(t + timedelta(minutes=5), 103.0),
        _bar(t + timedelta(minutes=10), 103.0),
        _bar(t + timedelta(minutes=15), 103.0),
        _bar(t + timedelta(minutes=20), 99.0),
    ]
    assert detect_first_delayed_failure(date(2026, 8, 3), bars) is None
