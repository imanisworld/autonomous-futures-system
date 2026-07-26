from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.bars_12hr_miyagi_loader import (
    _bucket_start_12h,
    load_5m_premarket_window,
    resample_12h_et,
    resample_60m_et,
)


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def test_bucket_start_12h_boundaries():
    d = datetime(2026, 1, 8, tzinfo=ET)
    assert _bucket_start_12h(d.replace(hour=4, minute=0)) == d.replace(hour=4)
    assert _bucket_start_12h(d.replace(hour=15, minute=45)) == d.replace(hour=4)
    assert _bucket_start_12h(d.replace(hour=16, minute=0)) == d.replace(hour=16)
    assert _bucket_start_12h(d.replace(hour=23, minute=45)) == d.replace(hour=16)
    # Hour < 4 belongs to the PRIOR calendar date's 4PM bucket.
    assert _bucket_start_12h(d.replace(hour=3, minute=45)) == d.replace(hour=16) - timedelta(days=1)
    assert _bucket_start_12h(d.replace(hour=0, minute=0)) == d.replace(hour=16) - timedelta(days=1)


def test_resample_12h_et_produces_ohlc_across_bucket():
    bars = [
        bar(datetime(2026, 1, 7, 16, 0, tzinfo=ET), 100, 105, 98, 102),
        bar(datetime(2026, 1, 7, 20, 0, tzinfo=ET), 102, 110, 101, 108),
        bar(datetime(2026, 1, 8, 2, 0, tzinfo=ET), 108, 109, 90, 95),
        bar(datetime(2026, 1, 8, 4, 0, tzinfo=ET), 95, 96, 93, 94),  # next bucket
    ]
    out = resample_12h_et(bars)
    bucket = next(b for b in out if b["ts"] == datetime(2026, 1, 7, 16, tzinfo=ET))
    assert bucket["open"] == 100  # first sub-bar's open
    assert bucket["high"] == 110  # max high
    assert bucket["low"] == 90  # min low
    assert bucket["close"] == 95  # last sub-bar's close
    assert bucket["n_sub_bars"] == 3

    next_bucket = next(b for b in out if b["ts"] == datetime(2026, 1, 8, 4, tzinfo=ET))
    assert next_bucket["n_sub_bars"] == 1


def test_resample_12h_et_handles_utc_input_and_converts_to_et():
    # 2026-01-07T21:00:00 UTC == 2026-01-07T16:00:00-05:00 ET (winter).
    bars = [bar(datetime(2026, 1, 7, 21, 0, tzinfo=UTC), 100, 101, 99, 100)]
    out = resample_12h_et(bars)
    assert out[0]["ts"] == datetime(2026, 1, 7, 16, tzinfo=ET)


def test_resample_60m_et_buckets_by_et_hour():
    bars = [
        bar(datetime(2026, 1, 8, 8, 0, tzinfo=ET), 100, 105, 98, 101),
        bar(datetime(2026, 1, 8, 8, 15, tzinfo=ET), 101, 108, 99, 103),
        bar(datetime(2026, 1, 8, 8, 45, tzinfo=ET), 103, 104, 95, 100),
        bar(datetime(2026, 1, 8, 9, 0, tzinfo=ET), 100, 101, 99, 100),  # next hour
    ]
    out = resample_60m_et(bars)
    eight = next(b for b in out if b["ts"] == datetime(2026, 1, 8, 8, tzinfo=ET))
    assert eight["open"] == 100
    assert eight["high"] == 108
    assert eight["low"] == 95
    assert eight["close"] == 100
    assert eight["n_sub_bars"] == 3


def test_load_5m_premarket_window_falls_back_to_proxy_when_cache_dir_missing(tmp_path):
    # Neither cache dir has data for this instrument/date -> "empty" provenance,
    # never a silent crash or a fabricated bar.
    from datetime import date

    result = load_5m_premarket_window(
        tmp_path / "nope_5m", tmp_path / "nope_15m", "MNQ", date(2026, 1, 8)
    )
    assert result["bars"] == []
    assert result["provenance"] == "empty"
