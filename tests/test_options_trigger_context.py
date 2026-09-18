"""Causal trigger-time market-context snapshots."""

from datetime import date, timedelta

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_observer import build_symbol_series
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.trigger_context import alignment_at_trigger


def _bar(start, high, low, close):
    return Bar(start=start, open=(high + low) / 2, high=high, low=low, close=close, volume=1_000, vwap=close)


def _session_bars(day: date, base: float, rising: bool):
    session = nyse_session_for(day)
    rows = []
    cursor = session.open
    i = 0
    while cursor + timedelta(minutes=30) <= session.close:
        shift = i * (0.5 if rising else -0.5)
        center = base + shift
        rows.append(_bar(cursor, center + 1, center - 1, center + (0.5 if rising else -0.5)))
        cursor += timedelta(minutes=30)
        i += 1
    return rows


def _series(symbol: str, days, rising=True):
    sessions = [nyse_session_for(day) for day in days]
    bars = []
    for index, day in enumerate(days):
        bars.extend(_session_bars(day, 100 + index * 5, rising))
    return build_symbol_series(symbol, bars, sessions)


def test_snapshot_is_causal_and_developing_daily_is_explicit():
    days = tuple(date(2026, 9, 14) + timedelta(days=i) for i in range(5))
    days = tuple(day for day in days if nyse_session_for(day) is not None)
    ticker = _series("XYZ", days)
    spy = _series("SPY", days)
    qqq = _series("QQQ", days)
    session = nyse_session_for(days[-1])
    cutoff = session.open + timedelta(hours=1, minutes=35)
    snap = alignment_at_trigger(ticker, session, cutoff, direction="LONG", spy=spy, qqq=qqq)
    assert snap.cutoff == cutoff.isoformat()
    assert snap.developing_daily_type is not None


def test_missing_index_context_fails_explicitly():
    days = (date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18))
    ticker = _series("XYZ", days)
    session = nyse_session_for(days[-1])
    snap = alignment_at_trigger(ticker, session, session.open + timedelta(hours=2), direction="SHORT", spy=None, qqq=None)
    assert not snap.completed_alignment_ok
    assert not snap.developing_alignment_ok
    assert "spy" in snap.completed_failures and "qqq" in snap.developing_failures
