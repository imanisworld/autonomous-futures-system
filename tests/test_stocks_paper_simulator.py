"""
tests/test_stocks_paper_simulator.py

stocks_advisory/paper_simulator.py tests. Proves the WATCHING -> ACTIVE
-> EXITED/INVALIDATED/EXPIRED lifecycle rules, the no-lookahead entry
fill (next bar's open, never the decision bar), the locked Robinhood
friction formula, the conservative same-bar stop/target rule, and no
Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import stocks_advisory.paper_simulator as paper_simulator_module
from stocks_advisory.backtest_models import Bar
from stocks_advisory.paper_simulator import (
    DEFAULT_POSITION_DOLLAR_SIZE,
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


def test_entry_fills_at_next_bar_open_not_decision_bar():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "active"
    assert result.state.entry_price == 50.0  # the vehicle bar's OPEN, not close
    assert result.state.entry_time == "2026-07-06T10:35:00-04:00"
    assert result.state.shares == DEFAULT_POSITION_DOLLAR_SIZE / 50.0


def test_watching_invalidated_when_qqq_closes_back_through_vwap_long_tqqq():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 100.2, 100.3, 99.5, 99.8)]  # closes below 100.0
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.2, 49.0, 49.5)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "invalidated"
    assert result.state.entry_price is None
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


def test_active_stop_hit_uses_confirming_bar_close_as_exit():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    # bar 1: confirms entry (QQQ still above vwap); bar 2: QQQ closes back below vwap -> stop
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
    assert result.state.entry_price == 50.0
    assert result.state.exit_price == 48.5  # the vehicle bar's CLOSE
    assert "stop hit" in result.state.exit_reason


def test_gross_and_net_pnl_use_locked_robinhood_fee_formula():
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
    shares = DEFAULT_POSITION_DOLLAR_SIZE / 50.0
    expected_gross = shares * (48.5 - 50.0)
    expected_friction = _robinhood_regulatory_fee_dollars(shares_sold=shares, sell_proceeds_dollars=shares * 48.5)
    expected_net = expected_gross - expected_friction
    assert abs(result.state.gross_pnl_dollars - expected_gross) < 1e-9
    assert abs(result.state.friction_dollars - expected_friction) < 1e-9
    assert abs(result.state.net_pnl_dollars - expected_net) < 1e-9
    assert result.state.friction_dollars > 0  # confirms friction is actually applied, not a no-op


def test_open_position_carries_across_calls_when_session_not_closed():
    state = _watching_state()
    qqq_bars = [_bar("2026-07-06T10:35:00-04:00", 101.0, 101.5, 100.8, 101.2)]
    vehicle_bars = [_bar("2026-07-06T10:35:00-04:00", 50.0, 50.5, 49.8, 50.2)]
    first = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert first.state.status == "active"

    # second CLI run, later, no stop hit yet, still not session-closed
    qqq_bars_2 = [_bar("2026-07-06T10:40:00-04:00", 101.1, 101.3, 100.9, 101.0)]
    vehicle_bars_2 = [_bar("2026-07-06T10:40:00-04:00", 50.2, 50.4, 50.0, 50.1)]
    second = advance_lifecycle(first.state, qqq_bars=qqq_bars_2, vehicle_bars=vehicle_bars_2, session_closed=False)
    assert second.ok is True
    assert second.state.status == "active"
    assert second.state.entry_price == 50.0  # unchanged from the original fill


def test_session_closed_forces_expired_for_still_watching():
    state = _watching_state(direction="long_tqqq", stop_price_qqq=100.0)
    # No new bars available at all -- session closes with the plan never confirmed or invalidated.
    result = advance_lifecycle(state, qqq_bars=[], vehicle_bars=[], session_closed=True)
    assert result.ok is True
    assert result.state.status == "expired"
    assert result.state.gross_pnl_dollars == 0.0
    assert "session ended" in result.state.exit_reason


def test_session_closed_forces_exit_for_still_active():
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        entry_price=50.0,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=DEFAULT_POSITION_DOLLAR_SIZE / 50.0,
    )
    qqq_bars = [_bar("2026-07-06T15:55:00-04:00", 101.0, 101.2, 100.9, 101.0)]  # never breaches stop
    vehicle_bars = [_bar("2026-07-06T15:55:00-04:00", 51.0, 51.2, 50.9, 51.1)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=True)
    assert result.ok is True
    assert result.state.status == "exited"
    assert result.state.exit_price == 51.1
    assert "forced session-end exit" in result.state.exit_reason
    assert result.state.net_pnl_dollars is not None


def test_same_bar_stop_and_target_resolves_conservatively_as_stop():
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        entry_price=50.0,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=DEFAULT_POSITION_DOLLAR_SIZE / 50.0,
        target_1=52.0,
    )
    # QQQ closes through the stop AND the vehicle bar's high reaches the target, same bar
    qqq_bars = [_bar("2026-07-06T10:40:00-04:00", 100.5, 100.6, 99.0, 99.5)]
    vehicle_bars = [_bar("2026-07-06T10:40:00-04:00", 50.5, 52.5, 49.5, 50.0)]
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "exited"
    assert "ambiguity resolved conservatively as stop" in result.state.exit_reason
    assert result.state.exit_price == 50.0  # the bar's close (stop convention), NOT the 52.0 target


def test_target_hit_alone_uses_target_price_as_exit():
    state = LifecycleState(
        trade_date="2026-07-06",
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        stop_price_qqq=100.0,
        status="active",
        entry_price=50.0,
        entry_time="2026-07-06T10:35:00-04:00",
        shares=DEFAULT_POSITION_DOLLAR_SIZE / 50.0,
        target_1=52.0,
    )
    qqq_bars = [_bar("2026-07-06T10:40:00-04:00", 101.0, 101.2, 100.8, 101.1)]  # no stop breach
    vehicle_bars = [_bar("2026-07-06T10:40:00-04:00", 51.5, 52.5, 51.4, 52.1)]  # high reaches target
    result = advance_lifecycle(state, qqq_bars=qqq_bars, vehicle_bars=vehicle_bars, session_closed=False)
    assert result.ok is True
    assert result.state.status == "exited"
    assert result.state.exit_price == 52.0
    assert result.state.exit_reason == "target hit"


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
