from __future__ import annotations

from types import SimpleNamespace

from execution import tradovate_session


def test_shared_tradovate_broker_reuses_one_instance(monkeypatch):
    created = []

    class FakeBroker:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(tradovate_session, "TradovateBroker", FakeBroker)
    tradovate_session.reset_shared_tradovate_broker()

    first = tradovate_session.shared_tradovate_broker()
    second = tradovate_session.shared_tradovate_broker()

    assert first is second
    assert len(created) == 1
    tradovate_session.reset_shared_tradovate_broker()


def test_runner_and_dashboard_use_shared_tradovate_broker(monkeypatch):
    import webhook.app as app_module
    import webhook.runner as runner_module

    sentinel = SimpleNamespace(config=SimpleNamespace(env="demo"))
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setattr(tradovate_session, "shared_tradovate_broker", lambda: sentinel)

    assert app_module._tv_broker() is sentinel
    assert runner_module._make_broker() is sentinel
