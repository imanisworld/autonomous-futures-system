"""
tests/test_tradovate_auth_breaker.py

Locks the auth circuit breaker: repeated auth failures (e.g. a 401 from an
expired API key) must back off after _AUTH_MAX_FAILURES instead of hammering
/auth/accesstokenrequest — repeated failed logins can get the Tradovate account
locked. A later success must clear the breaker.
"""
from __future__ import annotations

import requests

from execution.tradovate_broker import TradovateBroker, TradovateConfig


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    return TradovateBroker(config=TradovateConfig.from_env())


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._payload


def test_breaker_backs_off_after_repeated_401(monkeypatch):
    b = _broker(monkeypatch)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return _Resp(status_code=401)

    monkeypatch.setattr(b._session, "post", fake_post)

    # Three real attempts all 401 → breaker trips on the third.
    assert b._authenticate() is False
    assert b._authenticate() is False
    assert b._authenticate() is False
    assert calls["n"] == 3
    assert b._auth_cooldown_until > 0
    assert b._last_auth_error == "credentials_rejected (401)"

    # Fourth call is during cooldown → must NOT hit the API.
    assert b._authenticate() is False
    assert calls["n"] == 3  # unchanged — no new request


def test_breaker_resets_on_success(monkeypatch):
    b = _broker(monkeypatch)
    b._auth_fail_count = 2  # one away from tripping

    def ok_post(url, **kw):
        return _Resp(200, {"accessToken": "tok", "expirationTime": "2099-01-01T00:00:00+00:00"})

    monkeypatch.setattr(b._session, "post", ok_post)
    monkeypatch.setattr(b, "_resolve_account_id", lambda: None)

    assert b._authenticate() is True
    assert b._auth_fail_count == 0
    assert b._auth_cooldown_until == 0.0
    assert b._last_auth_error is None
