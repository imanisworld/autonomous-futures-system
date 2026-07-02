from execution.broker_interface import BracketOrder
from execution.tradovate_broker import TradovateBroker, TradovateConfig
import execution.tradovate_supervisor as supervisor


def _order():
    return BracketOrder(
        instrument="MES",
        direction="LONG",
        entry=5900.0,
        stop=5890.0,
        target=5920.0,
        rr_ratio=2.0,
        strategy="orb_breakout",
    )


def _broker(monkeypatch, response):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    broker._account_id = 1
    broker._contract_symbol_cache["MES"] = "MESU6"
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "_find_contract_id", lambda _: 99)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    captured = {}

    def post(path, body, **kwargs):
        captured[path] = body
        return response

    monkeypatch.setattr(broker, "_post", post)
    return broker, captured


def test_runner_live_places_stop_only_oso_and_tracks_stop_id(monkeypatch):
    monkeypatch.setenv("EXIT_MODE", "runner_live")
    broker, captured = _broker(
        monkeypatch, {"orderId": 10, "oso1Id": 20}
    )
    monkeypatch.setattr(
        broker,
        "_verify_bracket_children",
        lambda **kwargs: (kwargs["stop_id"] == 20, not kwargs["require_target"]),
    )
    fill = broker.execute_bracket(_order())
    body = captured["/order/placeOSO"]
    assert body["bracket1"]["orderType"] == "Stop"
    assert "bracket2" not in body
    assert fill.result == "OPEN"
    assert broker._last_order_ids["stop"] == 20
    assert broker._last_order_ids["target"] is None
    assert broker._last_position.target is None


def test_static_keeps_target_and_stop_children(monkeypatch):
    monkeypatch.setenv("EXIT_MODE", "static")
    monkeypatch.setenv("RUNNER_LIVE_ENABLED", "true")
    broker, captured = _broker(
        monkeypatch, {"orderId": 10, "oso1Id": 20, "oso2Id": 30}
    )
    monkeypatch.setattr(broker, "_verify_bracket_children", lambda **kwargs: (True, True))
    fill = broker.execute_bracket(_order())
    body = captured["/order/placeOSO"]
    assert body["bracket1"]["orderType"] == "Limit"
    assert body["bracket2"]["orderType"] == "Stop"
    assert fill.result == "OPEN"
    assert broker._last_order_ids["target"] == 20
    assert broker._last_order_ids["stop"] == 30


def test_runner_live_fails_closed_without_confirmed_stop(monkeypatch):
    monkeypatch.setenv("EXIT_MODE", "runner_live")
    broker, _ = _broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    monkeypatch.setattr(broker, "_verify_bracket_children", lambda **kwargs: (False, True))
    monkeypatch.setattr(
        broker,
        "_handle_naked_position",
        lambda *args, **kwargs: broker._cancelled_fill(_order(), "STOP_UNCONFIRMED"),
    )
    fill = broker.execute_bracket(_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "STOP_UNCONFIRMED"
