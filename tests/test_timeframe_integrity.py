"""Fail-closed CME session timeframe evidence tests (no broker I/O)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from research.timeframe_integrity import confirmed_session_bars

ET = ZoneInfo("America/New_York")


def _et(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=ET)


def _bars(start, n, *, skip=(), roll_at=None):
    ts = int(start.timestamp())
    return [
        {"ts": ts + 900 * i, "ticker": "MGCJ6" if roll_at is None or i < roll_at else "MGCM6",
         "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
         "close": 100.5 + i, "volume": i + 1}
        for i in range(n) if i not in skip
    ]


@pytest.mark.parametrize(("minutes", "n"), [(15, 1), (30, 2), (60, 4), (240, 16), (720, 48)])
def test_full_buckets_are_closed_and_ohlcv_correct(minutes, n):
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), n), minutes=minutes)
    assert len(result.bars) == 1
    b = result.bars[0]
    assert b["timeframe_minutes"] == minutes
    assert b["close_ts"] == int(_et(2026, 3, 9, 18).timestamp()) + minutes * 60
    assert b["high"] == pytest.approx(100.0 + n)
    assert b["low"] == 99.0
    assert b["volume"] == n * (n + 1) // 2


def test_partial_four_hour_bar_is_never_a_full_four_hour_signal():
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), 20), minutes=240)
    assert len(result.bars) == 1
    assert result.rejected == {"partial_or_gap": 1}


def test_interior_source_gap_invalidates_entire_bucket():
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), 16, skip={7}), minutes=240)
    assert not result.bars
    assert result.rejected == {"partial_or_gap": 1}


def test_contract_roll_does_not_splice_two_dated_contracts():
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), 16, roll_at=8), minutes=240)
    assert not result.bars
    assert result.rejected == {"contract_roll": 1}


@pytest.mark.parametrize(("when", "hour"), [
    (_et(2026, 3, 6, 18), 23),  # winter EST
    (_et(2026, 3, 9, 18), 22),  # summer EDT
    (_et(2026, 11, 2, 18), 23), # back to EST
])
def test_anchor_tracks_america_new_york_dst(when, hour):
    result = confirmed_session_bars(_bars(when, 16), minutes=240)
    assert len(result.bars) == 1
    assert datetime.fromtimestamp(result.bars[0]["ts"], timezone.utc).hour == hour


def test_summer_four_hour_bar_ends_2am_utc_not_at_utc_midnight():
    result = confirmed_session_bars(_bars(_et(2026, 7, 6, 18), 16), minutes=240)
    b = result.bars[0]
    assert datetime.fromtimestamp(b["ts"], timezone.utc).hour == 22
    assert datetime.fromtimestamp(b["close_ts"], timezone.utc).hour == 2


def test_end_of_globex_day_three_hour_remainder_is_not_a_four_hour_bar():
    # 18:00 -> next day's 17:00 = 23 hours, with a three-hour remainder.
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), 92), minutes=240)
    assert len(result.bars) == 5
    assert result.rejected == {"partial_or_gap": 1}


def test_maintenance_hours_do_not_create_signals():
    result = confirmed_session_bars(_bars(_et(2026, 3, 10, 17), 4), minutes=60)
    assert not result.bars
    assert result.rejected == {"maintenance_bar": 4}


def test_invalid_ohlcv_provenance_and_timestamp_order_fail_closed():
    r = _bars(_et(2026, 3, 9, 18), 2)
    with pytest.raises(ValueError, match="ticker"):
        confirmed_session_bars([{**r[0], "ticker": ""}], minutes=15)
    with pytest.raises(ValueError, match="increasing"):
        confirmed_session_bars([r[0], r[0]], minutes=15)
    with pytest.raises(ValueError, match="OHLCV"):
        confirmed_session_bars([{**r[0], "high": 88.0}], minutes=15)


@pytest.mark.parametrize("unsupported", [0, 1, 45, 1440, 240.0, True])
def test_unsupported_or_ambiguous_resolution_is_rejected(unsupported):
    with pytest.raises(ValueError, match="timeframe"):
        confirmed_session_bars([], minutes=unsupported)


def test_five_minute_source_can_build_5m_and_15m_bars():
    start = int(_et(2026, 3, 9, 18).timestamp())
    bars = [
        {"ts": start+300*i, "ticker": "MGCJ6", "open": 100+i,
         "high": 101+i, "low": 99+i, "close": 100.5+i, "volume": 2}
        for i in range(3)
    ]
    five = confirmed_session_bars(bars, minutes=5, source_minutes=5)
    fifteen = confirmed_session_bars(bars, minutes=15, source_minutes=5)
    assert len(five.bars) == 3
    assert len(fifteen.bars) == 1
    assert fifteen.bars[0]["volume"] == 6
    assert fifteen.bars[0]["close_ts"] == start + 900


def test_five_minute_source_gap_blocks_sixty_minute_bar():
    start = int(_et(2026, 3, 9, 18).timestamp())
    bars = [
        {"ts": start+300*i, "ticker": "MGCJ6", "open": 100,
         "high": 101, "low": 99, "close": 100, "volume": 1}
        for i in range(12) if i != 4
    ]
    result = confirmed_session_bars(bars, minutes=60, source_minutes=5)
    assert not result.bars
    assert result.rejected == {"partial_or_gap": 1}


@pytest.mark.parametrize(("source","tf"), [(15,5), (0,60), (10,60), (15,30.0)])
def test_timeframe_source_mismatch_rejected(source, tf):
    with pytest.raises(ValueError):
        confirmed_session_bars([], minutes=tf, source_minutes=source)


def test_session_last_eleven_hours_not_full_twelve_hour_bar():
    result = confirmed_session_bars(_bars(_et(2026, 3, 9, 18), 92), minutes=720)
    assert len(result.bars) == 1
    assert result.rejected == {"partial_or_gap": 1}


def test_frozen_resampler_emits_partial_bucket_but_new_research_refuses_it():
    # Diagnostic only: the sealed scorer is intentionally not modified.
    from research.mgc_4h_wide_forward import resample_session

    raw = _bars(_et(2026, 3, 9, 18), 20)
    old = resample_session(raw, 240)
    strict = confirmed_session_bars(raw, minutes=240)
    assert len(old) == 2
    assert old[-1]["volume"] == sum(x["volume"] for x in raw[-4:])
    assert len(strict.bars) == 1
    assert strict.rejected == {"partial_or_gap": 1}


def test_frozen_resampler_cannot_identify_contract_mix_but_new_guard_can():
    from research.mgc_4h_wide_forward import resample_session

    raw = _bars(_et(2026, 3, 9, 18), 16, roll_at=8)
    old = resample_session(raw, 240)
    strict = confirmed_session_bars(raw, minutes=240)
    assert len(old) == 1
    assert "ticker" not in old[0]
    assert strict.bars == ()
    assert strict.rejected == {"contract_roll": 1}
