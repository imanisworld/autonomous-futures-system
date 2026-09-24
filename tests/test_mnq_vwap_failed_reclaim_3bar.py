from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.mnq_vwap_failed_reclaim_3bar import detect_events, summarize


def _bar(ts, close, *, high=None, low=None, volume=100.0):
    return Bar(
        start=ts,
        open=float(close),
        high=float(high if high is not None else close + 0.5),
        low=float(low if low is not None else close - 0.5),
        close=float(close),
        volume=float(volume),
        vwap=float(close),
    )


def test_failure_on_next_bar_is_lag1():
    t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    # Establish VWAP near 100, reclaim above it, then fail below on next bar.
    bars = [
        _bar(t, 100),
        _bar(t + timedelta(minutes=5), 99),
        _bar(t + timedelta(minutes=10), 101),
        _bar(t + timedelta(minutes=15), 98),
    ]
    events = detect_events(day=t.date(), bars=bars)
    assert len(events) == 1
    assert events[0].failure_lag_bars == 1
    assert events[0].trigger_price == 98


def test_failure_on_third_bar_is_admitted():
    t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(t, 100),
        _bar(t + timedelta(minutes=5), 99),
        _bar(t + timedelta(minutes=10), 101),  # reclaim
        _bar(t + timedelta(minutes=15), 101.5),
        _bar(t + timedelta(minutes=20), 101.25),
        _bar(t + timedelta(minutes=25), 98),   # failure lag 3
    ]
    events = detect_events(day=t.date(), bars=bars)
    assert len(events) == 1
    assert events[0].failure_lag_bars == 3


def test_failure_after_three_bars_is_not_admitted():
    t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(t, 100),
        _bar(t + timedelta(minutes=5), 99),
        _bar(t + timedelta(minutes=10), 101),
        _bar(t + timedelta(minutes=15), 101.5),
        _bar(t + timedelta(minutes=20), 101.25),
        _bar(t + timedelta(minutes=25), 101.1),
        _bar(t + timedelta(minutes=30), 98),
    ]
    events = detect_events(day=t.date(), bars=bars)
    assert events == []


def test_reclaim_bar_itself_cannot_be_failure():
    t = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(t, 100),
        _bar(t + timedelta(minutes=5), 99),
        _bar(t + timedelta(minutes=10), 101),
    ]
    assert detect_events(day=t.date(), bars=bars) == []


def test_empty_summary_cannot_advance():
    s = summarize([], [])
    assert s["overall_60m"]["n"] == 0
    assert s["stage_b_eligible_metrics_only"] is False
