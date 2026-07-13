"""
tests/test_stocks_paper_simulator.py

stocks_advisory/paper_simulator.py tests. Proves the WATCHING -> ACTIVE
-> EXITED/INVALIDATED/EXPIRED lifecycle rules, the no-lookahead entry
fill (next bar's open, never the decision bar), the locked friction
model (modeled slippage on BOTH legs + the Robinhood regulatory-fee
formula, tracked as separate fields), the locked floor-share position
sizing, the conservative same-bar stop/target rule, and no
Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
import dataclasses
import math
from pathlib import Path

import stocks_advisory.paper_simulator as paper_simulator_module
from stocks_advisory.backtest_models import Bar
from stocks_advisory.paper_simulator import (
    DEFAULT_POSITION_DOLLAR_SIZE,
    MODELED_SLIPPAGE_PERCENT_PER_SIDE,
    LifecycleState,
    _robinhood_regulatory_fee_dollars,
    advance_lifecycle,
)


def _bar(ts: str, o: float, h: float, l: float, c: float, v: int = 1000) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _watching_state(direction="long_tqqq", stop_price_qqq=100.0, target_1=None) -> LifecycleState:
    return LifecycleState(
        trade_date="2026-07-06",
        direction=direction,
        vehicle_symbol="TQQQ" if direction == "long_tqqq" else "SQQQ",
        stop_price_qqq=stop_price_qqq,
        status="watching",
        target_1=target_1,
    )


def _expected_entry(raw_open: float, position_dollar_size: float = DEFAULT_POSITION_DOLLAR_SIZE):
    shares = float(max(1, math.floor(position_dollar_size / raw_open)))
    modeled = raw_open * (1 + MODELED_SLIPPAGE_PERCENT_PER_SIDE / 100.0)
    entry_slip = shares * (modeled - raw_open)
    return shares, modeled, entry_slip


def _expected_exit(raw_close: float, shares: float, raw_entry: float, entry_slip: float):
    modeled = raw_close * (1 - MODELED_SLIPPAGE_PERCENT_PER_SIDE / 100.0)
    exit_slip = shares * (raw_close - modeled)
    sell_proceeds = shares * modeled
    reg_fee = _robinhood_regulatory_fee_dollars(shares_sold=shares, sell_proceeds_dollars=sell_proceeds)
    total_friction = entry_slip + exit_slip + reg_fee
    gross = shares * (raw_close - raw_entry)
    net = gross - total_friction
    return modeled, exit_slip, reg_fee, total_friction, gross, net


def test_entry_fills_at_next_bar_open_with_modeled_slippage_and_floor_shares():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "active"

    shares, modeled_entry, entry_slip = _expected_entry(50.0)
    assert result.state.raw_entry_price == 50.0  # the vehicle bar's OPEN, not close
    assert result.state.entry_time == "2026-07-06T10:35:00-04:00"
    assert result.state.shares == shares
    assert result.state.shares == 20.0  # floor(1000/50.0) is exact
    assert abs(result.state.entry_price - modeled_entry) < 1e-9
    assert result.state.entry_price > result.state.raw_entry_price  # a buy is modeled WORSE, not better
    assert abs(result.state.entry_slippage_dollars - entry_slip) < 1e-9
    assert result.state.entry_slippage_dollars > 0


def test_floor_share_sizing_rounds_down_with_minimum_one_share():
    state = _watching_state()
    # 1000 / 333.0 = 3.003... -> floor to 3 shares, not 3.003
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 333.0, 333.5, 332.8, 333.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.state.shares == 3.0

    # a price so high that 1000/price < 1 must still floor UP to the 1-share minimum
    state2 = _watching_state()
    vehicle_bars2 = [_bar("2026-07-06T10:35:00-04:00", 5000.0, 5005.0, 4998.0, 5002.0)]
    result2 = advance_lifecycle(state2, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars2, session_closed=False)
    assert result2.state.shares == 1.0


def test_watching_invalidated_when_qqq_closes_back_through_vwap_long_tqqq():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 100.2, 100.3, 99.5, 99.8)]  # closes below 100.0
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.2, 49.0, 49.5)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "invalidated"
    assert result.state.entry_price is None
    assert result.state.entry_slippage_dollars == 0.0
    assert result.state.exit_slippage_dollars == 0.0
    assert result.state.regulatory_fees_dollars == 0.0
    assert result.state.total_friction_dollars == 0.0
    assert result.state.gross_pnl_dollars == 0.0
    assert result.state.net_pnl_dollars == 0.0
    assert "closed back through VWAP" in result.state.exit_reason


def test_watching_invalidated_direction_reversed_for_long_sqqq():
    state = _watching_state(direction="long_sqqq", stop_price_qqq=100.0)
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 99.8, 100.5, 99.5, 100.2)]  # closes ABOVE 100.0 -> invalid for SQQQ
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 20.0, 20.2, 19.5, 19.8)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "invalidated"


def test_active_stop_hit_uses_confirming_bar_close_as_exit_with_full_friction_breakdown():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    qqq_bars = [
        _bar("2026-07-06T10:35:00-04:00", 101.0, 101.5, 100.8, 101.2),
        _bar("2026-07-06T10:40:00-04:00", 101.1, 101.2, 99.0, 99.5),
    ]
    vehicle_bars = [
        _bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2),
        _bar("2026-07-06T10:40:00-04:00", 50.2, 50.3, 48.0, 48.5),
    ]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "exited"
    assert "stop hit" in result.state.exit_reason

    shares, modeled_entry, entry_slip = _expected_entry(50.0)
    modeled_exit, exit_slip, reg_fee, total_friction, gross, net = _expected_exit(48.5, shares, 50.0, entry_slip)

    assert result.state.raw_entry_price == 50.0
    assert abs(result.state.entry_price - modeled_entry) < 1e-9
    assert result.state.raw_exit_price == 48.5  # the vehicle bar's CLOSE
    assert abs(result.state.exit_price - modeled_exit) < 1e-9
    assert result.state.exit_price < result.state.raw_exit_price  # a sell is modeled WORSE, not better
    assert abs(result.state.entry_slippage_dollars - entry_slip) < 1e-9
    assert abs(result.state.exit_slippage_dollars - exit_slip) < 1e-9
    assert abs(result.state.regulatory_fees_dollars - reg_fee) < 1e-9
    assert abs(result.state.total_friction_dollars - total_friction) < 1e-9
    assert abs(result.state.gross_pnl_dollars - gross) < 1e-9
    assert abs(result.state.net_pnl_dollars - net) < 1e-9
    # net must be strictly worse than gross -- friction is never a no-op
    assert result.state.net_pnl_dollars < result.state.gross_pnl_dollars


def test_gross_pnl_uses_raw_prices_not_modeled_prices():
    # A sanity check distinct from the full-breakdown test above: gross_pnl_dollars
    # must be the FRICTIONLESS baseline (raw prices), never accidentally computed
    # from the slippage-adjusted entry/exit prices.
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    qqq_bars = [
        _bar("2026-07-06T10:35:00-04:00", 101.0, 101.5, 100.8, 101.2),
        _bar("2026-07-06T10:40:00-04:00", 101.1, 101.2, 99.0, 99.5),
    ]
    vehicle_bars = [
        _bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2),
        _bar("2026-07-06T10:40:00-04:00", 50.2, 50.3, 48.0, 48.5),
    ]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    shares = result.state.shares
    raw_gross = shares * (48.5 - 50.0)
    modeled_gross_would_be = shares * (result.state.exit_price - result.state.entry_price)
    assert abs(result.state.gross_pnl_dollars - raw_gross) < 1e-9
    assert abs(result.state.gross_pnl_dollars - modeled_gross_would_be) > 1e-6  # provably NOT the modeled-price version


def test_open_position_carries_across_calls_when_session_not_closed():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101.0, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    first = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert first.state.status == "active"

    qqq_bars_2 = [_bar("2026-07-06T10:40:00-04:00", 101.1, 101.3, 100.9, 101.0)]
    vehicle_bars_2 = [_bar("2026-07-06T10:40:00-04:00", 50.2, 50.4, 50.0, 50.1)]
    second = advance_lifecycle(first.state, qqq_bars=qqq_bars_2, vehicle_bars=vehicle_bars_2, session_closed=False)
    assert second.ok is True
    assert second.state.status == "active"
    assert second.state.raw_entry_price == 50.0  # unchanged from the original fill
    assert second.state.entry_slippage_dollars == first.state.entry_slippage_dollars


def test_session_closed_forces_expired_for_still_watching():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    result = advance_lifecycle(state, qqq_bars=[], vehicle_bars=[], session_closed=True)
    assert result.ok is True
    assert result.state.status == "expired"
    assert result.state.gross_pnl_dollars == 0.0
    assert result.state.total_friction_dollars == 0.0
    assert result.state.net_pnl_dollars == 0.0
    assert "session ended" in result.state.exit_reason


def test_session_closed_forces_exit_for_still_active():
    shares, modeled_entry, entry_slip = _expected_entry(50.0)
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        raw_entry_price=50.0,
        entry_price=modeled_entry,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=shares,
        entry_slippage_dollars=entry_slip,
    )
    qqq_bars = [_bar("2026-07-06T15:55:00-04:00", 101.0, 101.2, 100.9, 101.0)]  # never breaches stop
    vehicle_bars = [_bar("2026-07-06T15:55:00-04:00", 51.0, 51.2, 50.9, 51.1)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=True)
    assert result.ok is True
    assert result.state.status == "exited"
    assert result.state.raw_exit_price == 51.1
    assert "forced session-end exit" in result.state.exit_reason
    assert result.state.net_pnl_dollars is not None
    assert result.state.total_friction_dollars > 0


def test_same_bar_stop_and_target_resolves_conservatively_as_stop():
    shares, modeled_entry, entry_slip = _expected_entry(50.0)
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        raw_entry_price=50.0,
        entry_price=modeled_entry,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=shares,
        entry_slippage_dollars=entry_slip,
        target_1=52.0,
    )
    qqq_bars = [_bar("2026-07-06T10:40:00-04:00", 100.5, 100.6, 99.0, 99.5)]
    vehicle_bars = [_bar("2026-07-06T10:40:00-04:00", 50.5, 52.5, 49.5, 50.0)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "exited"
    assert "ambiguity resolved conservatively as stop" in result.state.exit_reason
    assert result.state.raw_exit_price == 50.0  # the bar's close (stop convention), NOT the 52.0 target


def test_target_hit_alone_uses_target_price_as_raw_exit():
    shares, modeled_entry, entry_slip = _expected_entry(50.0)
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        raw_entry_price=50.0,
        entry_price=modeled_entry,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=shares,
        entry_slippage_dollars=entry_slip,
        target_1=52.0,
    )
    qqq_bars = [_bar("2026-07-06T10:40:00-04:00", 101.0, 101.2, 100.8, 101.1)]  # no stop breach
    vehicle_bars = [_bar("2026-07-06T10:40:00-04:00", 51.5, 52.5, 51.4, 52.1)]  # high reaches target
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "exited"
    assert result.state.raw_exit_price == 52.0
    assert result.state.exit_reason == "target hit"
    assert result.state.exit_price < 52.0  # still slippage-adjusted, even for a target fill


def test_rejects_mismatched_bar_lengths():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 100.8, 101.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=[], session_closed=False)
    assert result.ok is False
    assert result.state is None
    assert "not the same length" in result.reject_reason


def test_rejects_non_positive_ohlc_bar():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 0.0, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is False
    assert "non-positive OHLC" in result.reject_reason


def test_rejects_invalid_direction_for_open_position():
    state = dataclasses.replace(_watching_state(), direction="sideways")
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is False
    assert "direction=" in result.reject_reason


def test_already_resolved_record_is_returned_unchanged():
    for status in ("exited", "invalidated", "expired", "no_trade"):
        state = LifecycleState(
            trade_date="2026-07-06",
            direction="no_trade",
            vehicle_symbol="",
            stop_price_qqq=0.0,
            status=status,
        )
        result = advance_lifecycle(state, qqq_bars=[], vehicle_bars=[], session_closed=True)
        assert result.ok is True
        assert result.state == state


def test_locked_slippage_rate_matches_predeclared_backtest_stress_level():
    from stocks_advisory.backtest_models import DEFAULT_SLIPPAGE_STRESS_LEVELS

    assert MODELED_SLIPPAGE_PERCENT_PER_SIDE in DEFAULT_SLIPPAGE_STRESS_LEVELS


def test_no_broker_execution_futures_options_manager_import():
    source = Path(paper_simulator_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_prefixes = ("broker", "execution", "futures", "options_manager", "robinhood")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.lower().startswith(forbidden_prefixes), alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            assert not module.startswith(forbidden_prefixes), module
