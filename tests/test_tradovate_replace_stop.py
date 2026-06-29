"""Tests for TradovateBroker.replace_stop — the live runner-trail primitive.

replace_stop moves the resting bracket2 Stop child to a tighter price via an
atomic /order/modifyorder. The safety-critical invariants:
  - NEVER loosens (LONG stop only rises, SHORT stop only falls)
  - tick-rounds the new price (off-tick stops get rejected by Tradovate)
  - fail-safe: any reject/exception leaves the OLD stop resting, returns False
  - honours the LIVE_TRADING_ENABLED guard when env == "live"
"""

from execution.broker_interface import Position
from execution.tradovate_broker import TradovateBroker, TradovateConfig


def _broker(monkeypatch, *, direction="LONG", stop=5893.0, resp=None, raises=False):
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555
    broker._last_position = Position(
        instrument="MES", direction=direction, entry_price=5900.0,
        stop=stop, target=5915.0, quantity=1, open=True,
    )
    broker._last_order_ids = {"instrument": "MES", "entry": 1, "target": 2, "stop": 99}

    calls = []

    def _post(path, body, **kw):
        calls.append((path, body))
        if raises:
            raise RuntimeError("network down")
        return resp if resp is not None else {"orderId": 99}

    monkeypatch.setattr(broker, "_post", _post)
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    return broker, calls


def test_replace_stop_tightens_long(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    assert broker.replace_stop(5905.0) is True
    assert len(calls) == 1
    path, body = calls[0]
    assert path == "/order/modifyorder"
    assert body["orderId"] == 99
    assert body["orderType"] == "Stop"
    assert body["stopPrice"] == 5905.0
    # state updated to the new resting stop
    assert broker._last_position.stop == 5905.0


def test_replace_stop_tightens_short(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="SHORT", stop=5915.0)
    assert broker.replace_stop(5905.0) is True
    assert calls[0][1]["stopPrice"] == 5905.0
    assert broker._last_position.stop == 5905.0


def test_replace_stop_refuses_to_loosen_long(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    # lower stop on a LONG = looser = forbidden
    assert broker.replace_stop(5890.0) is False
    # equal = no-op
    assert broker.replace_stop(5893.0) is False
    assert calls == []  # never hit the API
    assert broker._last_position.stop == 5893.0


def test_replace_stop_refuses_to_loosen_short(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="SHORT", stop=5915.0)
    # higher stop on a SHORT = looser = forbidden
    assert broker.replace_stop(5920.0) is False
    assert calls == []
    assert broker._last_position.stop == 5915.0


def test_replace_stop_tick_rounds(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    # off-grid request snaps to the MES 0.25 tick grid
    assert broker.replace_stop(5905.13) is True
    assert calls[0][1]["stopPrice"] == 5905.25


def test_replace_stop_fail_safe_on_reject(monkeypatch):
    broker, calls = _broker(
        monkeypatch, direction="LONG", stop=5893.0,
        resp={"failureReason": "OrderNotModifiable"},
    )
    assert broker.replace_stop(5905.0) is False
    # the OLD stop is preserved — position stays protected by the original order
    assert broker._last_position.stop == 5893.0
    assert broker._last_order_ids["stop"] == 99


def test_replace_stop_fail_safe_on_exception(monkeypatch):
    broker, _ = _broker(monkeypatch, direction="LONG", stop=5893.0, raises=True)
    assert broker.replace_stop(5905.0) is False
    assert broker._last_position.stop == 5893.0


def test_replace_stop_noop_without_open_position(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    broker._last_position = None
    assert broker.replace_stop(5905.0) is False
    assert calls == []


def test_replace_stop_noop_without_stop_order_id(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    broker._last_order_ids = {"instrument": "MES", "entry": 1, "target": 2}
    assert broker.replace_stop(5905.0) is False
    assert calls == []


def test_replace_stop_tracks_new_order_id(monkeypatch):
    broker, _ = _broker(
        monkeypatch, direction="LONG", stop=5893.0, resp={"orderId": 12345},
    )
    assert broker.replace_stop(5905.0) is True
    assert broker._last_order_ids["stop"] == 12345


def test_replace_stop_blocked_when_live_not_enabled(monkeypatch):
    broker, calls = _broker(monkeypatch, direction="LONG", stop=5893.0)
    broker.config.env = "live"
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    assert broker.replace_stop(5905.0) is False
    assert calls == []  # blocked before any API call
    assert broker._last_position.stop == 5893.0
