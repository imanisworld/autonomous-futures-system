from datetime import date, datetime, timezone

import pytest

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30
from alert_ranker.public_chart_bars import parse_regular_market_bars
from alert_ranker.session_calendar import nyse_session_for

UTC = timezone.utc


def _session():
    session = nyse_session_for(date(2026, 9, 18))
    assert session is not None
    return session


def _row(ts, o="100", h="101", l="99", c="100.5", v=1000):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_day_parser_keeps_only_complete_grid_aligned_five_minute_bars():
    payload = {"regularMarket": {"bars": [
        _row("2026-09-18T09:30:00-04:00"),
        _row("2026-09-18T09:35:00-04:00"),
        _row("2026-09-18T09:40:00-04:00"),
        _row("2026-09-18T09:42:17-04:00"),  # Public live point, never rounded.
    ]}}
    result = parse_regular_market_bars(
        payload,
        timeframe=MINUTE_5,
        decision_ts=datetime(2026, 9, 18, 13, 42, tzinfo=UTC),
        session=_session(),
    )
    assert [bar.start_utc.isoformat() for bar in result.bars] == [
        "2026-09-18T13:30:00+00:00",
        "2026-09-18T13:35:00+00:00",
    ]
    assert result.ignored_live_or_partial_rows == 1
    assert result.ignored_off_grid_rows == 1


def test_week_parser_excludes_session_close_synthetic_point_and_current_partial():
    payload = {"regularMarket": {"bars": [
        _row("2026-09-18T15:00:00-04:00"),
        _row("2026-09-18T15:30:00-04:00"),
        _row("2026-09-18T16:00:00-04:00"),
    ]}}
    result = parse_regular_market_bars(
        payload,
        timeframe=MINUTE_30,
        decision_ts=datetime(2026, 9, 18, 19, 45, tzinfo=UTC),
        session=_session(),
    )
    assert [bar.start_utc.isoformat() for bar in result.bars] == ["2026-09-18T19:00:00+00:00"]
    assert result.ignored_live_or_partial_rows == 1
    assert result.ignored_outside_session_rows == 1


def test_duplicate_complete_grid_bar_fails_closed():
    payload = {"regularMarket": {"bars": [
        _row("2026-09-18T09:30:00-04:00"),
        _row("2026-09-18T09:30:00-04:00"),
    ]}}
    with pytest.raises(ValueError, match="duplicate public chart bar"):
        parse_regular_market_bars(
            payload,
            timeframe=MINUTE_5,
            decision_ts=datetime(2026, 9, 18, 13, 40, tzinfo=UTC),
            session=_session(),
        )


def test_bad_ohlc_fails_closed():
    payload = {"regularMarket": {"bars": [
        _row("2026-09-18T09:30:00-04:00", o="102", h="101", l="99", c="100"),
    ]}}
    with pytest.raises(ValueError, match="OHLC geometry"):
        parse_regular_market_bars(
            payload,
            timeframe=MINUTE_5,
            decision_ts=datetime(2026, 9, 18, 13, 40, tzinfo=UTC),
            session=_session(),
        )
