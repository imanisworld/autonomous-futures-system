"""
tests/test_tradovate_keepalive.py

Locks the keep_alive() behavior: it renews the shared token in place (no relogin)
while a token is held, establishes one if missing, falls back to a fresh login
only when renewal genuinely fails, and respects the auth circuit breaker.
"""
from __future__ import annotations

import asyncio
import time

import requests

from execution.tradovate_broker import TradovateBroker, TradovateConfig

_FAR = "2099-01-01T00:00:00+00:00"


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_resolve_account_id", lambda: None)
    return b


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            e = requests.exceptions.HTTPError(f"{self.status_code}")
            e.response = self
            raise e

    def json(self):
        return self._p


def test_keepalive_renews_without_relogin(monkeypatch):
    b = _broker(monkeypatch)
    login = {"n": 0}
    renew = {"n": 0}

    def post(url, **k):
        if url.endswith("/auth/accesstokenrequest"):
            login["n"] += 1
            return _Resp(200, {"accessToken": "tok", "expirationTime": _FAR})
        return _Resp(200, {})

    def get(url, **k):
        if url.endswith("/auth/renewAccessToken"):
            renew["n"] += 1
            return _Resp(200, {"accessToken": "renewed", "expirationTime": _FAR})
        return _Resp(200, {})

    monkeypatch.setattr(b._session, "post", post)
    monkeypatch.setattr(b._session, "get", get)

    assert b._authenticate() is True          # one login establishes the session
    assert (login["n"], renew["n"]) == (1, 0)
    assert b.keep_alive() is True             # keepalive renews — NO new login
    assert renew["n"] == 1 and login["n"] == 1
    assert b._token.access_token == "renewed"


def test_keepalive_logs_in_when_no_token(monkeypatch):
    b = _broker(monkeypatch)
    login = {"n": 0}

    def post(url, **k):
        if url.endswith("/auth/accesstokenrequest"):
            login["n"] += 1
            return _Resp(200, {"accessToken": "tok", "expirationTime": _FAR})
        return _Resp(200, {})

    monkeypatch.setattr(b._session, "post", post)
    assert b._token is None
    assert b.keep_alive() is True
    assert login["n"] == 1


def test_keepalive_relogins_when_renewal_fails(monkeypatch):
    b = _broker(monkeypatch)
    login = {"n": 0}

    def post(url, **k):
        if url.endswith("/auth/accesstokenrequest"):
            login["n"] += 1
            return _Resp(200, {"accessToken": f"tok{login['n']}", "expirationTime": _FAR})
        return _Resp(200, {})

    def get(url, **k):
        if url.endswith("/auth/renewAccessToken"):
            return _Resp(401)  # session invalidated → renewal fails
        return _Resp(200, {})

    monkeypatch.setattr(b._session, "post", post)
    monkeypatch.setattr(b._session, "get", get)
    assert b._authenticate() is True
    assert login["n"] == 1
    assert b.keep_alive() is True       # renew 401 → fresh login
    assert login["n"] == 2 and b._token.access_token == "tok2"


def test_keepalive_respects_cooldown_when_no_token(monkeypatch):
    b = _broker(monkeypatch)
    b._auth_state.cooldown_until = time.time() + 999
    calls = {"n": 0}

    def post(url, **k):
        calls["n"] += 1
        return _Resp(401)

    monkeypatch.setattr(b._session, "post", post)
    assert b.keep_alive() is False      # no token + cooldown → no API call
    assert calls["n"] == 0


def test_fastapi_lifespan_starts_and_stops_keepalive(monkeypatch):
    import webhook.app as app_module

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_keepalive():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(app_module, "_configured_webhook_secret", lambda: "test-secret")
    monkeypatch.setattr(app_module, "run_tradovate_keepalive", fake_keepalive)

    async def exercise():
        async with app_module._lifespan(app_module.app):
            await asyncio.wait_for(started.wait(), timeout=1)
        assert stopped.is_set()

    asyncio.run(exercise())
