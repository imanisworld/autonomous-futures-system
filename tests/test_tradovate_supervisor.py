from __future__ import annotations

from types import SimpleNamespace

import requests

from execution.broker_interface import BracketOrder
from execution.tradovate_broker import (
    AUTH_CREDENTIALS_REJECTED,
    AUTH_HEALTHY,
    AUTH_RATE_LIMITED,
    AUTH_TEMPORARY_FAILURE,
    AuthResult,
    TradovateBroker,
    TradovateConfig,
    _Token,
)
from execution import tradovate_supervisor as supervisor


class FakeBroker:
    def __init__(self, auth=None, heartbeat=None):
        self.auth_results = list(auth or [AuthResult(AUTH_HEALTHY)])
        self.heartbeat_results = list(heartbeat or [AuthResult(AUTH_HEALTHY)])
        self._auth_state = SimpleNamespace(last_renewed_at=90.0)
        self._token = SimpleNamespace(expires_at=10_000.0)
        self.config = SimpleNamespace(token_refresh_buffer=300)

    def authenticate_result(self):
        if len(self.auth_results) > 1:
            return self.auth_results.pop(0)
        return self.auth_results[0]

    def reliability_heartbeat(self):
        if len(self.heartbeat_results) > 1:
            return self.heartbeat_results.pop(0)
        return self.heartbeat_results[0]


def test_weekend_outage_does_not_notify(monkeypatch):
    sent = []
    monkeypatch.setattr(supervisor, "_notify", sent.append)
    broker = FakeBroker(auth=[AuthResult(AUTH_TEMPORARY_FAILURE, "maintenance")])

    supervisor.supervisor_step(broker, now=100.0, market_active=False)
    supervisor.supervisor_step(broker, now=1_000.0, market_active=False)

    assert sent == []
    assert supervisor.reliability_snapshot()["state"] == "DEGRADED"


def test_startup_readiness_check_runs_even_when_market_closed():
    broker = FakeBroker()

    result = supervisor.supervisor_step(broker, now=100.0, market_active=False)

    assert result["state"] == "HEALTHY"
    assert result["ready"] is True


def test_market_hours_outage_alerts_once_after_five_minutes(monkeypatch):
    sent = []
    monkeypatch.setattr(supervisor, "_notify", sent.append)
    broker = FakeBroker(auth=[AuthResult(AUTH_TEMPORARY_FAILURE, "timeout")])

    supervisor.supervisor_step(broker, now=100.0, market_active=True)
    supervisor.supervisor_step(broker, now=401.0, market_active=True)
    supervisor.supervisor_step(broker, now=800.0, market_active=True)

    assert len(sent) == 1
    assert "DEGRADED" in sent[0]
    assert supervisor.tradovate_order_ready(now=800.0) is True  # BROKER defaults to paper


def test_recovery_requires_heartbeat_and_notifies(monkeypatch):
    sent = []
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setattr(supervisor, "_notify", sent.append)
    broker = FakeBroker(
        auth=[
            AuthResult(AUTH_TEMPORARY_FAILURE, "timeout"),
            AuthResult(AUTH_TEMPORARY_FAILURE, "timeout"),
            AuthResult(AUTH_HEALTHY),
        ],
        heartbeat=[AuthResult(AUTH_HEALTHY)],
    )

    supervisor.supervisor_step(broker, now=100.0, market_active=True)
    supervisor.supervisor_step(broker, now=401.0, market_active=True)
    assert supervisor.tradovate_order_ready(now=401.0) is False

    supervisor.supervisor_step(broker, now=402.0, market_active=True)

    assert supervisor.tradovate_order_ready(now=402.0) is True
    assert len(sent) == 2
    assert "RECOVERED" in sent[-1]


def test_credentials_rejected_notifies_immediately(monkeypatch):
    sent = []
    monkeypatch.setattr(supervisor, "_notify", sent.append)
    broker = FakeBroker(auth=[AuthResult(AUTH_CREDENTIALS_REJECTED, "HTTP 401")])

    supervisor.supervisor_step(broker, now=100.0, market_active=True)
    supervisor.supervisor_step(broker, now=101.0, market_active=True)

    assert len(sent) == 1
    assert "ACTION REQUIRED" in sent[0]


def test_rate_limit_retry_after_is_respected(monkeypatch):
    broker = FakeBroker(auth=[AuthResult(AUTH_RATE_LIMITED, "HTTP 429", 60)])

    supervisor.supervisor_step(broker, now=100.0, market_active=True)
    first_attempts = supervisor.reliability_snapshot()["recovery_attempts"]
    supervisor.supervisor_step(broker, now=130.0, market_active=True)

    assert supervisor.reliability_snapshot()["recovery_attempts"] == first_attempts


def _real_broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    broker = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(broker, "_resolve_account_id", lambda: None)
    broker._token = _Token("stale", 1.0)
    return broker


def test_timeout_and_rate_limit_never_trigger_login(monkeypatch):
    for response in (
        requests.Timeout("down"),
        requests.HTTPError("429", response=SimpleNamespace(status_code=429, headers={"Retry-After": "60"})),
    ):
        broker = _real_broker(monkeypatch)
        calls = {"login": 0}
        monkeypatch.setattr(broker._session, "get", lambda *a, **k: (_ for _ in ()).throw(response))
        monkeypatch.setattr(
            broker._session,
            "post",
            lambda *a, **k: calls.__setitem__("login", calls["login"] + 1),
        )

        result = broker.authenticate_result()
        assert result.status in {AUTH_TEMPORARY_FAILURE, AUTH_RATE_LIMITED}
        assert calls["login"] == 0


def test_heartbeat_401_marks_cached_token_stale(monkeypatch):
    broker = _real_broker(monkeypatch)
    broker._token = _Token("apparently-valid", 9_999_999_999.0)
    response = SimpleNamespace(status_code=401, headers={})

    def rejected(*_args, **_kwargs):
        raise requests.HTTPError("401", response=response)

    monkeypatch.setattr(broker._session, "get", rejected)

    result = broker.reliability_heartbeat()

    assert result.status == "token_invalid"
    assert broker._token.expires_at == 0.0


def test_order_is_blocked_until_supervisor_ready(monkeypatch):
    monkeypatch.setenv("BROKER", "tradovate")
    broker = _real_broker(monkeypatch)
    order = BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=20_000,
        stop=19_990,
        target=20_020,
        rr_ratio=2.0,
        strategy="test",
    )

    fill = broker.execute_bracket(order)

    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "BROKER_NOT_READY"
