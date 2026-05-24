"""
tests/test_paper_broker.py

Tests for PaperBroker: fills, resolution, position tracking, P&L.
"""

from __future__ import annotations

import pytest
from execution.paper_broker import PaperBroker, NextBarOHLC
from execution.broker_interface import BracketOrder


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def long_order():
    return BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
    )


@pytest.fixture
def short_order():
    return BracketOrder(
        instrument="MNQ",
        direction="SHORT",
        entry=19500.0,
        stop=19520.0,
        target=19460.0,
        rr_ratio=2.0,
        strategy="orb_rejection",
    )


class TestPaperBrokerIdentity:

    def test_is_not_live(self, broker):
        assert broker.is_live is False

    def test_broker_name(self, broker):
        assert broker.get_broker_name() == "PaperBroker"

    def test_no_position_initially(self, broker):
        assert broker.get_position() is None


class TestPaperBrokerExecution:

    def test_execute_bracket_returns_open_fill(self, broker, long_order):
        fill = broker.execute_bracket(long_order)
        assert fill.result == "OPEN"
        assert fill.entry_price == 19500.0
        assert fill.exit_price is None

    def test_position_open_after_execution(self, broker, long_order):
        broker.execute_bracket(long_order)
        pos = broker.get_position()
        assert pos is not None
        assert pos.instrument == "MNQ"
        assert pos.direction == "LONG"
        assert pos.open is True

    def test_cannot_open_second_position(self, broker, long_order):
        broker.execute_bracket(long_order)
        with pytest.raises(RuntimeError):
            broker.execute_bracket(long_order)

    def test_cancel_all_flattens_position(self, broker, long_order):
        broker.execute_bracket(long_order)
        broker.cancel_all()
        assert broker.get_position() is None


class TestLongResolution:

    def test_long_target_hit(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19545.0, low=19490.0)  # high > target
        fill = broker.resolve_position(next_bar)
        assert fill is not None
        assert fill.result == "WIN"
        assert fill.exit_reason == "TARGET_HIT"
        assert fill.exit_price == 19540.0
        assert fill.pnl_ticks > 0

    def test_long_stop_hit(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19495.0, low=19475.0)  # low < stop
        fill = broker.resolve_position(next_bar)
        assert fill is not None
        assert fill.result == "LOSS"
        assert fill.exit_reason == "STOP_HIT"
        assert fill.exit_price == 19480.0
        assert fill.pnl_ticks < 0

    def test_long_target_priority_over_stop(self, broker, long_order):
        """When both target and stop are hit in the same bar, target wins."""
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19545.0, low=19470.0)  # Both hit
        fill = broker.resolve_position(next_bar)
        assert fill.result == "WIN"
        assert fill.exit_reason == "TARGET_HIT"

    def test_long_neither_hit_returns_none(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19510.0, low=19490.0)  # Between stop and target
        fill = broker.resolve_position(next_bar)
        assert fill is None
        assert broker.get_position() is not None  # Still open

    def test_long_pnl_calculation(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19545.0, low=19490.0)
        fill = broker.resolve_position(next_bar)
        # target - entry = 19540 - 19500 = 40 points = 160 ticks for MNQ (0.25 tick size)
        assert fill.pnl_ticks == pytest.approx(160.0, rel=1e-2)


class TestShortResolution:

    def test_short_target_hit(self, broker, short_order):
        broker.execute_bracket(short_order)
        next_bar = NextBarOHLC(high=19510.0, low=19455.0)  # low < target
        fill = broker.resolve_position(next_bar)
        assert fill is not None
        assert fill.result == "WIN"
        assert fill.exit_reason == "TARGET_HIT"
        assert fill.exit_price == 19460.0

    def test_short_stop_hit(self, broker, short_order):
        broker.execute_bracket(short_order)
        next_bar = NextBarOHLC(high=19525.0, low=19490.0)  # high > stop
        fill = broker.resolve_position(next_bar)
        assert fill is not None
        assert fill.result == "LOSS"
        assert fill.exit_reason == "STOP_HIT"
        assert fill.exit_price == 19520.0


class TestPositionClearing:

    def test_position_cleared_after_win(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19545.0, low=19490.0)
        broker.resolve_position(next_bar)
        assert broker.get_position() is None

    def test_position_cleared_after_loss(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19495.0, low=19475.0)
        broker.resolve_position(next_bar)
        assert broker.get_position() is None

    def test_resolve_returns_none_if_no_position(self, broker):
        next_bar = NextBarOHLC(high=19545.0, low=19490.0)
        result = broker.resolve_position(next_bar)
        assert result is None

    def test_can_open_new_position_after_resolution(self, broker, long_order):
        broker.execute_bracket(long_order)
        next_bar = NextBarOHLC(high=19545.0, low=19490.0)
        broker.resolve_position(next_bar)
        # Should be able to execute again
        fill = broker.execute_bracket(long_order)
        assert fill.result == "OPEN"
