"""
tests/test_tradovate_session_renew.py

Locks the single-shared-session + token-renewal behavior. Tradovate allows only
TWO concurrent sessions per account, and every /auth/accesstokenrequest opens a
NEW one. So:
  - All TradovateBroker instances with the same creds must share ONE session
    (only one accesstokenrequest, ever, until it expires).
  - A stale token must be RENEWED in place (/auth/renewAccessToken) — same
    session — not re-requested.
  - Only a genuine renewal failure may fall back to opening a fresh session.
"""
from __future__ import annotations

import time

import requests

from execution.tradovate_broker import TradovateBroker, TradovateConfig

_FAR_FUTURE = "2099-01-01T00:00:00+00:00"


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


def test_two_instances_share_one_session(monkeypatch):
    """A second broker with the same creds reuses the shared token — it must NOT
    open another session."""
    login_calls = {"n": 0}

    def fake_post(url, **kw):
        if url.endswith("/auth/accesstokenrequest"):
            login_calls["n"] += 1
            return _Resp(200, {"accessToken": "tok1", "expirationTime": _FAR_FUTURE})
        return _Resp(200, {})

    b1 = _broker(monkeypatch)
    monkeypatch.setattr(b1._session, "post", fake_post)
    assert b1._authenticate() is True
    assert login_calls["n"] == 1

    # Fresh instance, same account → must reuse the shared token.
    b2 = _broker(monkeypatch)
    monkeypatch.setattr(b2._session, "post", fake_post)
    assert b2._authenticate() is True
    assert login_calls["n"] == 1  # NO second accesstokenrequest
    # And b2's own HTTP session carries the shared bearer token.
    assert b2._session.headers.get("Authorization") == "Bearer tok1"


def test_stale_token_renews_in_place(monkeypatch):
    """When the token is near expiry, renew it (same session) — not a new login."""
    login_calls = {"n": 0}
    renew_calls = {"n": 0}

    def fake_post(url, **kw):
        if url.endswith("/auth/accesstokenrequest"):
            login_calls["n"] += 1
            return _Resp(200, {"accessToken": "fresh", "expirationTime": _FAR_FUTURE})
        return _Resp(200, {})

    def fake_get(url, **kw):
        if url.endswith("/auth/renewAccessToken"):
            renew_calls["n"] += 1
            return _Resp(200, {"accessToken": "renewed", "expirationTime": _FAR_FUTURE})
        return _Resp(200, {})

    b = _broker(monkeypatch)
    monkeypatch.setattr(b._session, "post", fake_post)
    monkeypatch.setattr(b._session, "get", fake_get)

    assert b._authenticate() is True
    assert (login_calls["n"], renew_calls["n"]) == (1, 0)
    assert b._token.access_token == "fresh"

    # Force the token stale (inside the refresh buffer) → next auth must renew.
    b._auth_state.token.expires_at = time.time() + 10
    assert b._token.is_valid(b.config.token_refresh_buffer) is False

    assert b._authenticate() is True
    assert renew_calls["n"] == 1      # renewed in place
    assert login_calls["n"] == 1      # NO new session opened
    assert b._token.access_token == "renewed"
    assert b._session.headers["Authorization"] == "Bearer renewed"


def test_temporary_renew_failure_does_not_open_new_session(monkeypatch):
    """A provider outage must not churn Tradovate sessions."""
    login_calls = {"n": 0}

    def fake_post(url, **kw):
        if url.endswith("/auth/accesstokenrequest"):
            login_calls["n"] += 1
            return _Resp(200, {"accessToken": f"tok{login_calls['n']}",
                               "expirationTime": _FAR_FUTURE})
        return _Resp(200, {})

    def fake_get(url, **kw):
        if url.endswith("/auth/renewAccessToken"):
            return _Resp(500)  # renewal fails
        return _Resp(200, {})

    b = _broker(monkeypatch)
    monkeypatch.setattr(b._session, "post", fake_post)
    monkeypatch.setattr(b._session, "get", fake_get)

    assert b._authenticate() is True
    assert login_calls["n"] == 1 and b._token.access_token == "tok1"

    b._auth_state.token.expires_at = time.time() + 10  # stale
    assert b._authenticate() is False
    assert login_calls["n"] == 1
    assert b._token.access_token == "tok1"
