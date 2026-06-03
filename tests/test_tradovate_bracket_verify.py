"""Tests for Tradovate bracket-child verification (naked-position detection).

A market entry can fill while its protective stop/target children are rejected,
leaving a naked position. `_verify_bracket_children` polls the account orders and
returns True only when BOTH protective orders are confirmed live; otherwise it
fires a naked-position alert.
"""

from execution.broker_interface import BracketOrder
from execution.tradovate_broker import TradovateBroker, TradovateConfig


def _broker(monkeypatch, orders):
    """A broker whose _get('/order/list') returns `orders`, with no real sleeps."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555
    monkeypatch.setattr(broker, "_get", lambda path, **kw: orders)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    alerts = []
    monkeypatch.setattr(broker, "_alert_naked_position",
                        lambda order, *, stop_ok, target_ok: alerts.append((stop_ok, target_ok)))
    return broker, alerts


def _order(contract_id=10, action="Sell", otype="Stop", status="Working", account=555):
    return {"contractId": contract_id, "action": action, "orderType": otype,
            "ordStatus": status, "accountId": account}


_BRACKET = BracketOrder(instrument="MES", direction="LONG", entry=5900.0,
                        stop=5893.0, target=5915.0, rr_ratio=2.14,
                        strategy="manual_force_open", contracts=1)


def test_both_children_live_confirms_no_alert(monkeypatch):
    orders = [_order(otype="Stop"), _order(otype="Limit")]
    broker, alerts = _broker(monkeypatch, orders)
    assert broker._verify_bracket_children(10, "Sell", _BRACKET) is True
    assert alerts == []


def test_missing_stop_triggers_alert(monkeypatch):
    orders = [_order(otype="Limit")]  # target only, no stop
    broker, alerts = _broker(monkeypatch, orders)
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) is False
    assert alerts == [(False, True)]  # stop missing, target present


def test_no_children_triggers_alert(monkeypatch):
    broker, alerts = _broker(monkeypatch, [])
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) is False
    assert alerts == [(False, False)]


def test_ignores_wrong_contract_action_and_filled_status(monkeypatch):
    orders = [
        _order(contract_id=99, otype="Stop"),      # wrong contract
        _order(action="Buy", otype="Limit"),       # entry side, not close side
        _order(otype="Stop", status="Filled"),     # already filled — not protecting
        _order(otype="Limit", status="Canceled"),  # canceled
    ]
    broker, alerts = _broker(monkeypatch, orders)
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) is False
    assert alerts == [(False, False)]


def test_get_failure_is_defensive_and_alerts(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _boom(path, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(broker, "_get", _boom)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    alerts = []
    monkeypatch.setattr(broker, "_alert_naked_position",
                        lambda order, *, stop_ok, target_ok: alerts.append((stop_ok, target_ok)))
    # Must not raise; treats unverifiable as naked.
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=2) is False
    assert alerts == [(False, False)]
