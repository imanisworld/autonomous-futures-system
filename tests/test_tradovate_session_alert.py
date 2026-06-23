"""
tests/test_tradovate_session_alert.py

The Tradovate auth circuit-breaker is the ONE silent failure: when the API key
expires, the breaker trips and trading stops — but Discord stays quiet, so
"no trades" looks identical to "no setups". These tests lock the fix: a single
loud Discord alert when the breaker trips, no spam while it stays down, and a
"restored" alert on recovery.
"""
from __future__ import annotations

import execution.tradovate_broker as tb
from execution.tradovate_broker import TradovateBroker, TradovateConfig


def _broker(monkeypatch, *, webhook=True):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    if webhook:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    else:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    b = TradovateBroker(config=TradovateConfig.from_env())
    # Reset the process-shared breaker so other tests can't bleed in.
    b._auth_fail_count = 0
    b._auth_cooldown_until = 0.0
    b._auth_state.alerted = False
    return b


def _capture_posts(monkeypatch):
    posts = []
    monkeypatch.setattr(tb.requests, "post",
                        lambda url, **k: posts.append((url, (k.get("json") or {}).get("content", ""))))
    return posts


def test_breaker_trip_fires_one_down_alert(monkeypatch):
    posts = _capture_posts(monkeypatch)
    b = _broker(monkeypatch)
    for _ in range(b._AUTH_MAX_FAILURES):
        b._note_auth_failure("HTTP 401")
    assert b._auth_state.alerted is True
    assert len(posts) == 1                       # exactly one, fired on the trip
    assert "DOWN" in posts[0][1]


def test_no_repeat_alert_while_down(monkeypatch):
    posts = _capture_posts(monkeypatch)
    b = _broker(monkeypatch)
    for _ in range(b._AUTH_MAX_FAILURES + 4):    # keep failing past the trip
        b._note_auth_failure("HTTP 401")
    assert len(posts) == 1                        # still just the one — no spam


def test_recovery_fires_restored_alert_and_resets(monkeypatch):
    posts = _capture_posts(monkeypatch)
    b = _broker(monkeypatch)
    for _ in range(b._AUTH_MAX_FAILURES):
        b._note_auth_failure("HTTP 401")
    b._clear_auth_breaker()                       # successful auth path calls this
    assert b._auth_state.alerted is False
    assert b._auth_fail_count == 0
    assert b._auth_cooldown_until == 0.0
    assert len(posts) == 2
    assert "restored" in posts[1][1]


def test_recovery_without_prior_trip_is_silent(monkeypatch):
    posts = _capture_posts(monkeypatch)
    b = _broker(monkeypatch)
    b._note_auth_failure("HTTP 401")              # 1 failure — below the trip threshold
    b._clear_auth_breaker()
    assert posts == []                            # nothing tripped → nothing to announce


def test_no_alert_when_webhook_unconfigured(monkeypatch):
    posts = _capture_posts(monkeypatch)
    b = _broker(monkeypatch, webhook=False)
    for _ in range(b._AUTH_MAX_FAILURES):
        b._note_auth_failure("HTTP 401")
    assert posts == []                            # fail-soft: no URL → no post, no raise
    assert b._auth_state.alerted is True          # flag still set (don't re-try forever)
