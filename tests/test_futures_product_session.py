"""Locks the fail-closed CME equity-index product-session guard.

The guard is prep for a later wiring PR; nothing imports it at runtime yet.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.session_calendar import us_equity_rth_state
from context.futures_product_session import (
    CALENDAR_UNKNOWN_FAIL_CLOSED,
    HOLIDAY_CLOSED,
    MAINTENANCE_HALT,
    MARKET_OPEN,
    SPECIAL_SESSION_CLOSED,
    STATUSES,
    UNSUPPORTED_INSTRUMENT,
    WEEKEND_CLOSED,
    product_session_status,
)
from context.futures_session import futures_session_active

_ET = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=_ET)


# 1 + 2: normal Globex, both products, RTH and overnight.
@pytest.mark.parametrize("root", ["MNQ", "MES", "mnq", " mes "])
@pytest.mark.parametrize("when", [
    _et(2026, 9, 22, 10, 0),   # Tuesday RTH
    _et(2026, 9, 23, 3, 0),    # Wednesday overnight Globex
    _et(2026, 9, 22, 18, 0),   # reopen instant after halt
    _et(2026, 9, 25, 16, 59),  # Friday just before weekly close
])
def test_normal_globex_is_open(root, when):
    s = product_session_status(root, when)
    assert s.status == MARKET_OPEN and s.is_open
    assert s.instrument == root.strip().upper()


def test_utc_input_is_converted_to_exchange_time():
    s = product_session_status("MNQ", datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc))
    assert s.status == MARKET_OPEN
    assert s.exchange_time.tzinfo is not None and s.exchange_time.hour == 10


# 3: daily maintenance halt.
@pytest.mark.parametrize("root", ["MNQ", "MES"])
@pytest.mark.parametrize("hm", [(17, 0), (17, 30), (17, 59)])
def test_maintenance_halt_is_closed(root, hm):
    s = product_session_status(root, _et(2026, 9, 22, *hm))
    assert s.status == MAINTENANCE_HALT and not s.is_open


# 4: Saturday.
@pytest.mark.parametrize("h", [0, 12, 23])
def test_saturday_is_closed(h):
    s = product_session_status("MNQ", _et(2026, 9, 26, h))
    assert s.status == WEEKEND_CLOSED and not s.is_open


def test_friday_after_close_is_weekend():
    assert product_session_status("MES", _et(2026, 9, 25, 17, 0)).status == WEEKEND_CLOSED


# 5: Sunday before reopen.
@pytest.mark.parametrize("hm", [(0, 0), (12, 0), (17, 59)])
def test_sunday_before_reopen_is_closed(hm):
    s = product_session_status("MES", _et(2026, 9, 27, *hm))
    assert s.status == WEEKEND_CLOSED and not s.is_open


# 6: Sunday after reopen.
@pytest.mark.parametrize("root", ["MNQ", "MES"])
@pytest.mark.parametrize("hm", [(18, 0), (21, 30), (23, 59)])
def test_sunday_after_reopen_is_open(root, hm):
    s = product_session_status(root, _et(2026, 9, 27, *hm))
    assert s.status == MARKET_OPEN and s.is_open


# 7: unsupported instruments fail closed.
@pytest.mark.parametrize("root", ["ES", "NQ", "M2K", "MGC", "MBT", "SPY", "MNQZ6", "MNQ1!", "", None])
def test_unsupported_instrument_fails_closed(root):
    s = product_session_status(root, _et(2026, 9, 22, 10, 0))
    assert s.status == UNSUPPORTED_INSTRUMENT and not s.is_open
    assert s.calendar is None


# 8: unknown calendar state fails closed.
def test_naive_timestamp_fails_closed():
    s = product_session_status("MNQ", datetime(2026, 9, 22, 10, 0))
    assert s.status == CALENDAR_UNKNOWN_FAIL_CLOSED and not s.is_open


@pytest.mark.parametrize("bad", [None, "2026-09-22T10:00:00-04:00", 1790000000])
def test_non_datetime_fails_closed(bad):
    assert product_session_status("MNQ", bad).status == CALENDAR_UNKNOWN_FAIL_CLOSED


@pytest.mark.parametrize("when", [
    _et(2024, 9, 17, 10, 0),   # before reviewed years
    _et(2028, 9, 19, 10, 0),   # after reviewed years
    _et(2027, 12, 31, 3, 0),   # next day falls outside reviewed years
])
def test_out_of_range_year_fails_closed(when):
    assert product_session_status("MES", when).status == CALENDAR_UNKNOWN_FAIL_CLOSED


@pytest.mark.parametrize("when", [
    _et(2026, 4, 3, 10, 0),    # Good Friday 2026 (not in repo calendar)
    _et(2026, 4, 2, 18, 30),   # evening before Good Friday
    _et(2026, 9, 6, 18, 30),   # Sunday evening before Labor Day
    _et(2026, 12, 24, 19, 0),  # Christmas Eve evening
])
def test_unestablished_windows_fail_closed(when):
    s = product_session_status("MNQ", when)
    assert s.status == CALENDAR_UNKNOWN_FAIL_CLOSED and not s.is_open


# Holidays / special sessions from repo calendar data.
@pytest.mark.parametrize("when", [
    _et(2026, 9, 7, 10, 0),    # Labor Day
    _et(2026, 11, 26, 9, 0),   # Thanksgiving
    _et(2026, 12, 25, 12, 0),  # Christmas
    _et(2026, 7, 3, 10, 0),    # observed Independence Day (Jul 4 is Saturday)
    _et(2026, 6, 19, 10, 0),   # Juneteenth
    _et(2027, 1, 1, 10, 0),    # New Year's Day
])
def test_holiday_is_closed(when):
    s = product_session_status("MES", when)
    assert s.status == HOLIDAY_CLOSED and not s.is_open


def test_holiday_evening_reopen_is_open():
    # C14 Pine proof: Labor Day 18:00 ET reopen continues trade date 09-08.
    assert product_session_status("MNQ", _et(2026, 9, 7, 18, 5)).status == MARKET_OPEN


@pytest.mark.parametrize("when,expected", [
    (_et(2026, 11, 27, 12, 59), MARKET_OPEN),
    (_et(2026, 11, 27, 13, 0), SPECIAL_SESSION_CLOSED),
    (_et(2026, 12, 24, 10, 0), MARKET_OPEN),
    (_et(2026, 12, 24, 15, 0), SPECIAL_SESSION_CLOSED),
])
def test_early_close_eves(when, expected):
    assert product_session_status("MNQ", when).status == expected


def test_only_market_open_is_open():
    assert len(STATUSES) == 7
    for when in (_et(2026, 9, 26, 12), _et(2026, 9, 22, 17, 30), _et(2026, 9, 7, 10)):
        s = product_session_status("MNQ", when)
        assert s.status in STATUSES and s.status != MARKET_OPEN and not s.is_open


# 9: broad session labels are not product-calendar proof.
def test_signature_takes_no_session_label():
    params = list(inspect.signature(product_session_status).parameters)
    assert params == ["instrument", "moment"]


def test_legacy_feed_label_open_on_holiday_does_not_make_product_open():
    labor_day = _et(2026, 9, 7, 10, 0)
    assert futures_session_active(labor_day) is True  # broad feed-health label
    assert product_session_status("MNQ", labor_day).status == HOLIDAY_CLOSED


def test_legacy_feed_label_fail_open_is_not_inherited():
    # futures_session_active fails open on bad input; the guard fails closed.
    assert product_session_status("MES", datetime(2026, 9, 7, 10, 0)).status == CALENDAR_UNKNOWN_FAIL_CLOSED


def test_nyse_rth_label_is_not_globex_session():
    overnight = _et(2026, 9, 23, 3, 0)
    assert us_equity_rth_state(overnight).is_open is False
    assert product_session_status("MNQ", overnight).status == MARKET_OPEN


def test_guard_is_not_imported_by_runtime():
    import subprocess
    out = subprocess.run(
        ["git", "grep", "-l", "futures_product_session", "--", "*.py"],
        capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parents[1],
    ).stdout.split()
    assert set(out) <= {"context/futures_product_session.py", "tests/test_futures_product_session.py"}
