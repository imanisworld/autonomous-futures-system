from __future__ import annotations

from datetime import date, timedelta

from alert_ranker.session_calendar import _easter_sunday, nyse_session_for

# Known correct Gregorian Easter dates, independently verified.
KNOWN_EASTER_DATES = {
    2023: date(2023, 4, 9),
    2024: date(2024, 3, 31),
    2025: date(2025, 4, 20),
    2026: date(2026, 4, 5),
    2027: date(2027, 3, 28),
    2028: date(2028, 4, 16),
    2029: date(2029, 4, 1),
    2030: date(2030, 4, 21),
    2031: date(2031, 4, 13),
}


def test_easter_sunday_matches_known_dates() -> None:
    for year, expected in KNOWN_EASTER_DATES.items():
        assert _easter_sunday(year) == expected, f"year {year}"


def test_good_friday_2026_is_closed_and_april_10_is_open() -> None:
    good_friday_2026 = KNOWN_EASTER_DATES[2026] - timedelta(days=2)
    assert good_friday_2026 == date(2026, 4, 3)
    assert nyse_session_for(good_friday_2026) is None
    assert nyse_session_for(date(2026, 4, 10)) is not None
