from execution.broker_interface import Position
from execution.tradovate_broker import TradovateBroker, TradovateConfig


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    broker = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    return broker


def test_position_snapshot_confirms_flat_only_after_successful_list(monkeypatch):
    broker = _broker(monkeypatch)
    broker._last_position = Position("MNQ", "LONG", 30000.0, 29990.0, 30020.0, 1, True)
    monkeypatch.setattr(broker, "_get", lambda path: [])

    confirmed, position = broker.get_position_snapshot()

    assert confirmed is True
    assert position is None
    assert broker._last_position is None


def test_position_snapshot_fails_closed_on_api_error(monkeypatch):
    broker = _broker(monkeypatch)

    def fail(_path):
        raise RuntimeError("position API unavailable")

    monkeypatch.setattr(broker, "_get", fail)

    confirmed, position = broker.get_position_snapshot()

    assert confirmed is False
    assert position is None
