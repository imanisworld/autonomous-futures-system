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

    def test_initial_account_balance_defaults_to_1500(self):
        broker = PaperBroker()
        assert broker.get_account_balance() == 1500.0

    def test_account_balance_updates_after_win(self, long_order):
        broker = PaperBroker(starting_balance=5000)
        broker.execute_bracket(long_order)
        broker.resolve_position(NextBarOHLC(high=19545.0, low=19490.0))
        assert broker.get_account_balance() == pytest.approx(5080.0, rel=1e-2)

    def test_account_balance_updates_after_loss(self, long_order):
        broker = PaperBroker(starting_balance=5000)
        broker.execute_bracket(long_order)
        broker.resolve_position(NextBarOHLC(high=19495.0, low=19475.0))
        assert broker.get_account_balance() == pytest.approx(4960.0, rel=1e-2)


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
        assert fill.pnl_dollars == pytest.approx(80.0, rel=1e-2)

    def test_contract_count_scales_pnl(self, broker):
        order = BracketOrder(
            instrument="MNQ",
            direction="LONG",
            entry=19500.0,
            stop=19480.0,
            target=19540.0,
            rr_ratio=2.0,
            strategy="orb_reclaim",
            contracts=2,
        )
        broker.execute_bracket(order)
        fill = broker.resolve_position(NextBarOHLC(high=19545.0, low=19490.0))
        assert fill.contracts == 2
        assert fill.pnl_ticks == pytest.approx(160.0, rel=1e-2)
        assert fill.pnl_dollars == pytest.approx(160.0, rel=1e-2)

    def test_multi_contract_does_not_move_stop_to_breakeven(self, broker):
        order = BracketOrder(
            instrument="MNQ",
            direction="LONG",
            entry=19500.0,
            stop=19480.0,
            target=19540.0,
            rr_ratio=2.0,
            strategy="orb_reclaim",
            contracts=2,
        )
        broker.execute_bracket(order)

        # Touches 1R (19520) and entry in the same bar, but multi-contract
        # trades do not use breakeven management. Position should stay open.
        fill = broker.resolve_position(NextBarOHLC(high=19525.0, low=19495.0))
        assert fill is None
        pos = broker.get_position()
        assert pos is not None
        assert pos.stop == order.stop


    @pytest.mark.parametrize(
        "instrument,tick_value",
        [
            ("MNQ", 0.50),
            ("MES", 1.25),
            ("MGC", 10.00),
            ("MCL", 10.00),
        ],
    )
    def test_tick_value_per_instrument(self, broker, instrument, tick_value):
        tick_sizes = {"MNQ": 0.25, "MES": 0.25, "MGC": 0.10, "MCL": 0.01}
        tick_size = tick_sizes[instrument]
        entry = 100.0
        order = BracketOrder(
            instrument=instrument,
            direction="LONG",
            entry=entry,
            stop=entry - tick_size,
            target=entry + tick_size,
            rr_ratio=1.0,
            strategy="tick_value_test",
        )
        broker.execute_bracket(order)
        fill = broker.resolve_position(
            NextBarOHLC(high=entry + tick_size, low=entry)
        )
        assert fill.pnl_ticks == pytest.approx(1.0, rel=1e-2)
        assert fill.pnl_dollars == pytest.approx(tick_value, rel=1e-2)


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


class TestBreakevenAtOneR:

    def test_long_moves_stop_to_breakeven_after_one_r(self, broker, long_order):
        broker.execute_bracket(long_order)

        # 1R for long_order is 19520. Bar reaches 1R but not target, and does not
        # retrace to entry yet, so position stays open with stop moved to entry.
        fill = broker.resolve_position(NextBarOHLC(high=19525.0, low=19505.0))
        assert fill is None
        pos = broker.get_position()
        assert pos is not None
        assert pos.stop == long_order.entry

        fill = broker.resolve_position(NextBarOHLC(high=19510.0, low=19495.0))
        assert fill is not None
        assert fill.result == "BREAKEVEN"
        assert fill.exit_reason == "BREAKEVEN_STOP"
        assert fill.exit_price == long_order.entry
        assert fill.pnl_dollars == 0.0

    def test_short_moves_stop_to_breakeven_after_one_r(self, broker, short_order):
        broker.execute_bracket(short_order)

        # 1R for short_order is 19480. Bar reaches 1R but not target, and does not
        # retrace to entry yet, so position stays open with stop moved to entry.
        fill = broker.resolve_position(NextBarOHLC(high=19495.0, low=19475.0))
        assert fill is None
        pos = broker.get_position()
        assert pos is not None
        assert pos.stop == short_order.entry

        fill = broker.resolve_position(NextBarOHLC(high=19505.0, low=19490.0))
        assert fill is not None
        assert fill.result == "BREAKEVEN"
        assert fill.exit_reason == "BREAKEVEN_STOP"
        assert fill.exit_price == short_order.entry
        assert fill.pnl_dollars == 0.0

    def test_target_still_has_priority_after_one_r(self, broker, long_order):
        broker.execute_bracket(long_order)
        fill = broker.resolve_position(NextBarOHLC(high=19545.0, low=19495.0))
        assert fill is not None
        assert fill.result == "WIN"
        assert fill.exit_reason == "TARGET_HIT"
