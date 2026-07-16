from __future__ import annotations

from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from execution.post_fill_validation import strategy_execution_model, validate_post_fill
from execution.tradovate_broker import TradovateBroker, TradovateConfig
import execution.tradovate_supervisor as supervisor


def _order(**overrides):
    values = dict(
        instrument="MNQ",
        direction="LONG",
        entry=29603.5,
        stop=29583.5,
        target=29653.5,
        rr_ratio=2.5,
        strategy="orb_reclaim",
        contracts=1,
        min_rr_ratio=2.0,
        max_dollar_risk=56.0,
        max_stop_ticks=120,
        max_slippage_ticks=32,
        post_fill_validation_required=True,
    )
    values.update(overrides)
    return BracketOrder(**values)


def test_requested_and_actual_entry_are_separate_and_rr_recalculated():
    audit = validate_post_fill(_order(), 29610.5)
    assert audit.requested_entry == 29603.5
    assert audit.actual_entry == 29610.5
    assert audit.planned_rr == 2.5
    assert round(audit.actual_rr, 2) == 1.59
    assert not audit.accepted
    assert "actual_rr_minimum" in audit.failed_checks


def test_excessive_slippage_and_actual_risk_fail_independently():
    slippage = validate_post_fill(_order(max_slippage_ticks=2), 29604.25)
    assert "slippage_limit" in slippage.failed_checks
    risk = validate_post_fill(_order(max_dollar_risk=40.0), 29604.25)
    assert "actual_dollar_risk" in risk.failed_checks


def test_invalid_geometry_and_tick_prices_fail_closed():
    audit = validate_post_fill(
        _order(stop=29606.0, target=29610.1),
        29605.0,
    )
    assert not audit.checks["stop_direction"]
    assert not audit.checks["target_tick"]
    assert not audit.accepted


def test_short_and_mes_tick_math():
    order = _order(
        instrument="MES",
        direction="SHORT",
        entry=7600.0,
        stop=7606.0,
        target=7585.0,
        max_dollar_risk=35.0,
        max_stop_ticks=60,
        max_slippage_ticks=16,
    )
    audit = validate_post_fill(order, 7599.75)
    assert audit.tick_size == 0.25
    assert audit.tick_value == 1.25
    assert audit.actual_risk_points == 6.25
    assert audit.actual_dollar_risk == 31.25
    assert audit.accepted


def test_all_current_strategies_are_anchored_structure():
    for strategy in (
        "orb_reclaim", "orb_breakout", "pdh_reclaim", "pdl_reclaim",
        "vwap_hold", "vwap_reclaim",
    ):
        assert strategy_execution_model(strategy) == "anchored_structure"


def test_paper_and_runtime_use_same_formula_when_parity_gate_requested():
    order = _order()
    expected = validate_post_fill(order, 29610.5)
    broker = PaperBroker(slippage_ticks=28)
    fill = broker.execute_bracket(order)
    assert fill.result == "CANCELLED"
    assert fill.execution_audit["post_fill_validation"]["actual_rr"] == expected.actual_rr


def _tradovate(monkeypatch, actual_fill, flatten):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    broker._account_id = 1
    broker._contract_symbol_cache["MNQ"] = "MNQU6"
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "_find_contract_id", lambda _: 99)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    monkeypatch.setattr(
        broker, "_post",
        lambda path, body, **kwargs: {"orderId": 10, "oso1Id": 20, "oso2Id": 30},
    )
    monkeypatch.setattr(broker, "_entry_status", lambda *args, **kwargs: "filled")
    monkeypatch.setattr(broker, "_entry_fill_price", lambda *args, **kwargs: actual_fill)
    monkeypatch.setattr(broker, "_verify_bracket_children", lambda **kwargs: (True, True))
    monkeypatch.setattr(broker, "flatten_position", lambda: flatten)
    return broker


def test_bad_actual_fill_attempts_confirmed_controlled_flatten(monkeypatch):
    broker = _tradovate(
        monkeypatch,
        29610.5,
        {
            "close_sent": True,
            "close_order_id": 40,
            "flat_confirmed": True,
            "close_fill_price": 29610.25,
        },
    )
    fill = broker.execute_bracket(_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "POST_FILL_INVALID_AUTO_FLATTENED"
    assert fill.execution_audit["controlled_flatten"]["close_order_id"] == 40
    assert broker._last_order_ids["entry"] == 10  # exact broker identity existed


def test_failed_flatten_stays_visible_as_open(monkeypatch):
    broker = _tradovate(
        monkeypatch,
        29610.5,
        {"close_sent": False, "close_order_id": None, "flat_confirmed": False},
    )
    fill = broker.execute_bracket(_order())
    assert fill.result == "OPEN"
    assert fill.exit_reason == "POST_FILL_INVALID_FLATTEN_UNCONFIRMED"
    assert broker._last_position is not None and broker._last_position.open


def test_accepted_fill_uses_actual_entry_and_keeps_valid_bracket(monkeypatch):
    broker = _tradovate(monkeypatch, 29604.0, {})
    fill = broker.execute_bracket(_order())
    assert fill.result == "OPEN"
    assert fill.entry_price == 29604.0
    assert broker._last_position.entry_price == 29604.0
    assert fill.execution_audit["post_fill_validation"]["accepted"] is True


def test_entry_fill_price_is_exact_order_weighted_average(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    broker._account_id = 1
    monkeypatch.setattr(
        broker,
        "_get",
        lambda path: [
            {"orderId": 10, "price": 100.0, "qty": 1},
            {"orderId": 10, "price": 101.0, "qty": 3},
            {"orderId": 99, "price": 999.0, "qty": 1},
        ] if path.startswith("/fill/list") else {},
    )
    assert broker._entry_fill_price(10, "MNQ") == 100.75
