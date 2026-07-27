from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.lane_b_mnq_close_momentum import (
    BoundaryBar,
    COMMISSION_RT,
    _build_trades,
)


ET = ZoneInfo("America/New_York")


def _bar(day: date, hour: int, minute: int, price: float, *, close: float | None = None):
    return BoundaryBar(
        ts=datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        open=price,
        high=max(price, close or price),
        low=min(price, close or price),
        close=price if close is None else close,
        volume=1000,
        market_condition="TRENDING",
    )


def test_literal_rule_uses_signal_sign_and_last_half_hour_boundaries():
    first = date(2026, 1, 2)
    second = date(2026, 1, 5)
    sessions = {
        first: {
            datetime.min.time().replace(hour=15, minute=25): _bar(first, 15, 25, 99),
            datetime.min.time().replace(hour=15, minute=30): _bar(first, 15, 30, 99),
            datetime.min.time().replace(hour=15, minute=55): _bar(first, 15, 55, 100),
        },
        second: {
            datetime.min.time().replace(hour=15, minute=25): _bar(second, 15, 25, 101),
            datetime.min.time().replace(hour=15, minute=30): _bar(second, 15, 30, 102),
            datetime.min.time().replace(hour=15, minute=55): _bar(second, 15, 55, 104),
        },
    }
    rows, exclusions = _build_trades(sessions, [first, second], 1)
    assert exclusions == [{"date": "2026-01-02", "reason": "NO_PRIOR_FULL_SESSION_CLOSE"}]
    assert len(rows) == 1
    trade = rows[0]
    assert trade.direction == "LONG"
    assert trade.signal_return == pytest.approx(0.01)
    assert trade.raw_entry == 102
    assert trade.raw_exit == 104
    assert trade.gross_pnl == 4
    assert trade.slippage_cost == 1  # one MNQ tick on each side = $1 round trip
    assert trade.net_pnl == pytest.approx(4 - 1 - COMMISSION_RT)


def test_exact_zero_signal_follows_paper_otherwise_short_branch():
    first = date(2026, 1, 2)
    second = date(2026, 1, 5)
    t1525 = datetime.min.time().replace(hour=15, minute=25)
    t1530 = datetime.min.time().replace(hour=15, minute=30)
    t1555 = datetime.min.time().replace(hour=15, minute=55)
    sessions = {
        first: {t1525: _bar(first, 15, 25, 100), t1530: _bar(first, 15, 30, 100), t1555: _bar(first, 15, 55, 100)},
        second: {t1525: _bar(second, 15, 25, 100), t1530: _bar(second, 15, 30, 100), t1555: _bar(second, 15, 55, 99)},
    }
    rows, _ = _build_trades(sessions, [first, second], 1)
    assert rows[0].direction == "SHORT"
