from datetime import date, datetime, time

from research.causal_inverted_lane_b_executable import (
    _is_prospectively_eligible,
    _resolve_calendar_rule,
)
from research.lane_b_mnq_close_momentum import ET, BoundaryBar


def _bar(day: date, hour: int, minute: int, price: float) -> BoundaryBar:
    return BoundaryBar(
        ts=datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price + 0.5,
        volume=100,
        market_condition="UNKNOWN",
    )


def _session(
    day: date,
    *,
    signal: float = 100,
    entry: float = 101,
    exit_price: float = 102,
    include_signal: bool = True,
    include_entry: bool = True,
    include_exit: bool = True,
) -> dict[time, BoundaryBar]:
    bars = {}
    if include_signal:
        bars[time(15, 25)] = _bar(day, 15, 25, signal)
    if include_entry:
        bars[time(15, 35)] = _bar(day, 15, 35, entry)
    if include_exit:
        bars[time(15, 55)] = _bar(day, 15, 55, exit_price)
    return bars


def test_calendar_excludes_known_early_close_before_observing_bars():
    assert not _is_prospectively_eligible(date(2024, 7, 3))
    assert not _is_prospectively_eligible(date(2025, 1, 9))
    assert _is_prospectively_eligible(date(2026, 7, 2))


def test_signal_uses_prior_scheduled_close_and_entry_is_strictly_later():
    first = date(2026, 7, 1)
    second = date(2026, 7, 2)
    sessions = {
        first: _session(first, exit_price=100),
        second: _session(second, signal=104, entry=103, exit_price=101),
    }

    trades, ledger = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )

    assert [row.status for row in ledger] == ["PRIOR_CLOSE_MISSING", "RESOLVED"]
    assert len(trades) == 1
    trade = trades[0]
    assert trade.prior_close == 100.5
    assert trade.signal_price == 104.5
    assert trade.direction == "SHORT"
    assert datetime.fromisoformat(trade.entry_bar_ts) > datetime.fromisoformat(
        trade.signal_available_at
    )


def test_missing_later_exit_does_not_retroactively_remove_candidate():
    first = date(2026, 7, 6)
    second = date(2026, 7, 7)
    third = date(2026, 7, 8)
    sessions = {
        first: _session(first),
        second: _session(second, include_exit=False),
        third: _session(third),
    }

    trades, ledger = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )

    assert ledger[1].candidate is True
    assert ledger[1].status == "EXIT_UNRESOLVED"
    assert ledger[2].status == "PRIOR_CLOSE_MISSING"
    assert trades == []


def test_missing_signal_can_still_seed_next_sessions_prior_close():
    first = date(2026, 7, 6)
    second = date(2026, 7, 7)
    third = date(2026, 7, 8)
    sessions = {
        first: _session(first),
        second: _session(second, include_signal=False, exit_price=110),
        third: _session(third, signal=108),
    }

    trades, ledger = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )

    assert [row.status for row in ledger] == [
        "PRIOR_CLOSE_MISSING",
        "SIGNAL_DATA_MISSING",
        "RESOLVED",
    ]
    assert trades[0].prior_close == 110.5


def test_exact_zero_preserves_long_mapping():
    first = date(2026, 7, 6)
    second = date(2026, 7, 7)
    sessions = {
        first: _session(first, exit_price=100),
        # Prior close is 100.5 and this signal close is also 100.5.
        second: _session(second, signal=100),
    }

    trades, _ = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )

    assert len(trades) == 1
    assert trades[0].signal_return == 0
    assert trades[0].direction == "LONG"
