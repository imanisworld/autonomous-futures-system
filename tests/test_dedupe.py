"""Dedupe-key regression tests.

Root cause of the 2026-07-14 → 2026-07-17 "15m feed outage" incident class:
every 15m bar-open (:00/:15/:30/:45) is also a 5m bar-open, both TradingView
alerts carry event_type "signal", and the dedupe key omitted timeframe — so
the 5m alert (arriving ~bar_open+5min) poisoned the key and the 15m alert
(arriving ~bar_open+15min) lived or died on whether the ~600s arrival gap
beat the 600s TTL. Verified empirically on 2026-07-17 02:45Z: MES's 15m
alert survived at a 602.7s gap while MNQ's died at 593.6s.
"""
from __future__ import annotations

from webhook.dedupe import DedupeCache, dedupe_key


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_same_bar_same_timeframe_within_ttl_is_duplicate():
    clock = FakeClock()
    cache = DedupeCache(ttl_seconds=600, clock=clock)
    assert cache.is_duplicate("MNQ1!", "signal", "1784255400000", timeframe="15") is False
    clock.advance(30)
    assert cache.is_duplicate("MNQ1!", "signal", "1784255400000", timeframe="15") is True


def test_quarter_hour_5m_and_15m_share_bar_time_but_never_collide():
    """The incident reproduction: 5m alert at bar_open+5min, 15m alert at
    bar_open+15min, identical symbol/event/bar_time, gap under the TTL —
    the 15m alert must NOT be treated as a duplicate of the 5m alert."""
    clock = FakeClock()
    cache = DedupeCache(ttl_seconds=600, clock=clock)
    # 5m alert for the 02:30 bar arrives at 02:35.
    assert cache.is_duplicate("MNQ1!", "signal", "1784255400000", timeframe="5") is False
    # 15m alert for the same 02:30 open arrives 593s later (inside the TTL).
    clock.advance(593)
    assert cache.is_duplicate("MNQ1!", "signal", "1784255400000", timeframe="15") is False


def test_true_duplicate_15m_resend_is_still_caught():
    clock = FakeClock()
    cache = DedupeCache(ttl_seconds=600, clock=clock)
    assert cache.is_duplicate("MES1!", "signal", "1784255400000", timeframe="15") is False
    clock.advance(5)  # TradingView immediate re-send
    assert cache.is_duplicate("MES1!", "signal", "1784255400000", timeframe="15") is True


def test_ttl_expiry_still_applies():
    clock = FakeClock()
    cache = DedupeCache(ttl_seconds=600, clock=clock)
    assert cache.is_duplicate("MES1!", "signal", "1784255400000", timeframe="15") is False
    clock.advance(601)
    assert cache.is_duplicate("MES1!", "signal", "1784255400000", timeframe="15") is False


def test_key_includes_timeframe_and_none_stays_distinct():
    assert dedupe_key("MNQ1!", "signal", "123", "5") != dedupe_key("MNQ1!", "signal", "123", "15")
    assert dedupe_key("MNQ1!", "signal", "123") == dedupe_key("MNQ1!", "signal", "123", None)
