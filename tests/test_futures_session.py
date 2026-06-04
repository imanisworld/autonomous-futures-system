"""
tests/test_futures_session.py

Locks the single shared CME-session definition used by diagnostics, the feed
watchdog, and the ops monitor. If these drift apart again the dashboards,
diagnostics, and watchdog will disagree about feed health.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from context.futures_session import futures_session_active, feed_stale_after_minutes

_ET = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=_ET)


@pytest.mark.parametrize("when,expected,why", [
    (_et(2026, 6, 1, 10, 0), True,  "Monday RTH"),
    (_et(2026, 6, 2, 3, 0),  True,  "Tuesday overnight CME (~03:00 ET)"),
    (_et(2026, 6, 1, 17, 30), False, "daily maintenance halt 17:00-18:00 ET"),
    (_et(2026, 6, 1, 18, 5),  True,  "after maintenance reopen"),
    (_et(2026, 6, 5, 17, 5),  False, "Friday after 17:00 close"),
    (_et(2026, 6, 5, 12, 0),  True,  "Friday midday still open"),
    (_et(2026, 6, 6, 12, 0),  False, "Saturday closed"),
    (_et(2026, 6, 7, 17, 0),  False, "Sunday before 18:00 reopen"),
    (_et(2026, 6, 7, 18, 30), True,  "Sunday after 18:00 reopen"),
])
def test_futures_session_active_truth_table(when, expected, why):
    assert futures_session_active(when) is expected, why


def test_naive_datetime_treated_as_utc():
    # 2026-06-01 21:00 UTC == 17:00 ET → maintenance halt → inactive.
    assert futures_session_active(datetime(2026, 6, 1, 21, 30)) is False


def test_feed_stale_after_minutes_tracks_timeframe():
    assert feed_stale_after_minutes(15) == 31   # ~2 bars + 1m grace
    assert feed_stale_after_minutes(5) == 11
    assert feed_stale_after_minutes(0) == 31     # falls back to 15m default
