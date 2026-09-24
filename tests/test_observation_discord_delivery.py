"""Observation Discord delivery after the 2026-09-23 session-open 429 burst.

Proves: a 429 waits Discord's requested delay (bounded) instead of retrying
at once; webhook URLs/tokens never reach the log; ~85 session-open events go
out as a handful of grouped messages with every card intact; the background
sender never blocks the caller, keeps a bounded queue, drops stale messages,
and survives a permanently failing Discord.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx
import pytest

from notifications import discord_notifier
from notifications import observation_notifier as obs
from notifications.discord_card import card_text
from notifications.discord_router import DiscordRouter, Route, redact_webhooks, retry_after_seconds

SECRET = "AbCdEf-SECRET_token_1234567890"
WEBHOOK = f"https://discord.com/api/webhooks/100000000000000001/{SECRET}"
ROUTES = {"observation": Route("observation", "DISCORD_ROUTE_OBSERVATION", False)}
ENV = {"DISCORD_ROUTE_OBSERVATION": WEBHOOK}


def _status_error(code: int, *, body=None, headers=None) -> httpx.HTTPStatusError:
    """The exact exception type the default transport raises (URL in its text)."""
    request = httpx.Request("POST", WEBHOOK)
    response = httpx.Response(code, json=body, headers=headers, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{code}' for url '{WEBHOOK}'", request=request, response=response,
    )


def _router(transport, sleeps=None):
    return DiscordRouter(routes=ROUTES, transport=transport, env=ENV,
                         sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def _events(n: int) -> list[dict]:
    """n session-open-like events: mostly outcomes plus a few candidates."""
    out = []
    for i in range(n):
        root = ("MNQ", "MES", "MGC", "MCL", "M2K", "MBT")[i % 6]
        if i % 10 == 0:
            out.append({"record_type": "CANDIDATE", "instrument": root, "strategy": "strat_212",
                        "direction": "LONG", "entry": 100 + i, "stop": 99 + i, "target": 102 + i,
                        "signal_timestamp": "2026-09-23T22:15:00+00:00", "candidate_id": f"c{i}"})
        else:
            out.append({"record_type": "OUTCOME", "instrument": root, "strategy": "strat_212_observed",
                        "direction": "SHORT", "result": "WIN" if i % 2 else "LOSS", "exit_reason": "TARGET_HIT",
                        "gross_pnl_dollars_1_contract": float(i), "entry": 100 + i, "exit_price": 99 + i,
                        "resolved_at_bar_ts": "2026-09-23T22:15:00+00:00", "candidate_id": f"o{i}"})
    return out


# ── 1. 429 honors Discord's requested delay ──────────────────────────────────

def test_429_waits_the_retry_after_from_the_body_then_retries_once():
    calls, sleeps = [], []

    def transport(url, message):
        calls.append(message)
        if len(calls) == 1:
            raise _status_error(429, body={"retry_after": 0.75, "global": False})

    assert _router(transport, sleeps).send("observation", "hi") is True
    assert len(calls) == 2 and sleeps == [0.75]


def test_429_falls_back_to_the_retry_after_header():
    assert retry_after_seconds(_status_error(429, headers={"Retry-After": "1.5"})) == 1.5
    assert retry_after_seconds(_status_error(429)) == 1.0          # 429 with nothing usable
    assert retry_after_seconds(_status_error(500)) is None         # not a rate limit
    assert retry_after_seconds(RuntimeError("boom")) is None


def test_429_longer_than_the_cap_drops_without_waiting_or_busy_retrying():
    calls, sleeps = [], []

    def transport(url, message):
        calls.append(message)
        raise _status_error(429, body={"retry_after": 40.0})

    assert _router(transport, sleeps).send("observation", "hi", max_retry_wait=30.0) is False
    assert len(calls) == 1 and sleeps == []                       # no second request, no sleep


def test_non_rate_limit_failure_keeps_the_single_immediate_retry():
    calls, sleeps = [], []

    def transport(url, message):
        calls.append(message)
        raise _status_error(500)

    assert _router(transport, sleeps).send("observation", "hi") is False
    assert len(calls) == 2 and sleeps == []


# ── 2. webhook secrets never reach the log ───────────────────────────────────

def test_redact_webhooks_removes_id_and_token():
    text = redact_webhooks(f"Client error '429' for url '{WEBHOOK}' and https://discordapp.com/api/v10/webhooks/1/x")
    assert SECRET not in text and "100000000000000001" not in text and "/webhooks/1/x" not in text
    assert text.count("<discord-webhook-redacted>") == 2


@pytest.mark.parametrize("code", [429, 500])
def test_router_failure_logs_never_contain_the_webhook_token(caplog, code):
    def transport(url, message):
        raise _status_error(code, body={"retry_after": 0.1})

    with caplog.at_level(logging.DEBUG):
        assert _router(transport).send("observation", "hi") is False
    assert caplog.records, "a failed send must still be logged"
    for record in caplog.records:
        assert SECRET not in record.getMessage() and WEBHOOK not in record.getMessage()
    assert any("<discord-webhook-redacted>" in r.getMessage() for r in caplog.records)


def test_legacy_alert_path_log_never_contains_the_webhook_token(caplog):
    class Cfg:
        discord_webhook_url = WEBHOOK

    def transport(url, body, headers):
        raise RuntimeError(f"failed posting to {url}")

    with caplog.at_level(logging.DEBUG):
        result = discord_notifier.send_discord_alert(Cfg(), "feed down", transport=transport)
    assert result.sent is False
    assert all(SECRET not in r.getMessage() for r in caplog.records)


# ── 3. ~85 session-open events → a few grouped messages, nothing lost ────────

def test_85_event_burst_becomes_nine_messages_with_every_card_intact():
    events = _events(85)
    lines = [obs.format_event(e) for e in events]
    messages = obs.build_messages(lines)
    assert len(messages) == 9
    assert all(1 <= cards <= obs.MAX_EMBEDS_PER_MESSAGE for _, cards in messages)
    assert sum(cards for _, cards in messages) == 85
    embeds = [e for body, _ in messages for e in body["embeds"]]
    assert all(len(body["embeds"]) == cards for body, cards in messages)
    # Same card per event, same order, footer kept on each.
    assert [e["title"] for e in embeds] == [line.splitlines()[0] for line in lines]
    assert all(card_text({"embeds": [e]}).splitlines()[-1].startswith("OBSERVATION ONLY") for e in embeds)
    for body, _ in messages:
        assert body["allowed_mentions"] == {"parse": []}
        assert sum(obs._embed_text_len(e) for e in body["embeds"]) <= 6000


def test_inline_burst_sends_nine_requests_not_85():
    requests = []
    router = _router(lambda url, message: requests.append(message))
    assert obs.notify_observation(_events(85), router=router) == 85
    assert len(requests) == 9


# ── 4. background sender: non-blocking, bounded, survives failure ────────────

@pytest.fixture
def dispatcher(monkeypatch):
    d = obs._Dispatcher(sleep=lambda s: None)
    monkeypatch.setattr(obs, "_DISPATCHER", d)
    monkeypatch.setattr(obs, "DELIVERY_MODE", "background")
    return d


def test_background_delivery_never_blocks_the_caller(dispatcher):
    gate = threading.Event()
    requests = []

    def transport(url, message):
        gate.wait(10)                      # Discord "hangs"
        requests.append(message)

    started = time.monotonic()
    messages = obs.build_messages([obs.format_event(e) for e in _events(85)])
    accepted = dispatcher.submit(_router(transport), messages)
    assert accepted == 85
    assert time.monotonic() - started < 0.5 and requests == []     # caller returned while Discord hangs
    gate.set()
    assert dispatcher.join(5)
    assert len(requests) == 9 and dispatcher.delivered_cards == 85


def test_default_mode_notify_returns_immediately_while_discord_hangs(dispatcher, monkeypatch):
    gate = threading.Event()
    requests = []

    def transport(url, message):
        gate.wait(10)
        requests.append(message)

    real_init = DiscordRouter.__init__
    monkeypatch.setattr(DiscordRouter, "__init__",
                        lambda self, **kw: real_init(self, routes=ROUTES, transport=transport, env=ENV))
    started = time.monotonic()
    assert obs.notify_observation(_events(85)) == 85
    assert time.monotonic() - started < 0.5 and requests == []
    gate.set()
    assert dispatcher.join(5) and len(requests) == 9


def test_permanent_discord_failure_drops_locally_and_worker_survives(dispatcher, caplog):
    down = {"on": True}
    requests = []

    def transport(url, message):
        requests.append(message)
        if down["on"]:
            raise _status_error(503)

    router = _router(transport)
    with caplog.at_level(logging.DEBUG):
        dispatcher.submit(router, obs.build_messages([obs.format_event(e) for e in _events(20)]))
        assert dispatcher.join(5)
    assert dispatcher.dropped_cards == 20 and dispatcher.delivered_cards == 0
    assert len(requests) == 4                                      # 2 messages × (1 try + 1 retry), bounded
    assert all(SECRET not in r.getMessage() for r in caplog.records)
    down["on"] = False                                             # Discord comes back
    dispatcher.submit(router, obs.build_messages([obs.format_event(e) for e in _events(3)]))
    assert dispatcher.join(5) and dispatcher.delivered_cards == 3


def test_queue_is_bounded_and_overflow_drops_without_blocking(dispatcher, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(obs, "MAX_QUEUE_MESSAGES", 3)
    d = obs._Dispatcher(sleep=lambda s: None)
    monkeypatch.setattr(obs, "_DISPATCHER", d)
    router = _router(lambda url, message: gate.wait(10))
    one = obs.build_messages([obs.format_event(_events(1)[0])])
    started = time.monotonic()
    accepted = sum(d.submit(router, one) for _ in range(10))
    assert time.monotonic() - started < 0.5
    # 1 in flight on the worker + 3 queued; the rest dropped and counted.
    assert accepted <= 4 and d.dropped_cards == 10 - accepted and d.dropped_cards >= 6
    gate.set()
    assert d.join(5)


def test_stale_messages_are_dropped_not_sent(monkeypatch):
    now = {"t": 0.0}
    d = obs._Dispatcher(sleep=lambda s: None, clock=lambda: now["t"])
    gate = threading.Event()
    requests = []

    def transport(url, message):
        gate.wait(10)
        requests.append(message)

    router = _router(transport)
    msgs = obs.build_messages([obs.format_event(e) for e in _events(15)])   # 2 messages
    d.submit(router, msgs)
    time.sleep(0.05)                       # worker holds message 1
    now["t"] = obs.MAX_MESSAGE_AGE + 1     # message 2 goes stale while waiting
    gate.set()
    assert d.join(5)
    assert len(requests) == 1 and d.delivered_cards == 10 and d.dropped_cards == 5


# ── 5. every other direct Discord sender redacts its failure log ─────────────

def _assert_no_secret(caplog):
    assert caplog.records, "the failure must still be logged"
    for record in caplog.records:
        text = record.getMessage() + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        assert SECRET not in text and WEBHOOK not in text, text


def test_system_notifier_failure_log_is_redacted(caplog):
    from notifications.system_notifier import notify_system

    class Cfg:
        discord_notifications_enabled = True
        discord_webhook_url = WEBHOOK

    def transport(url, body, headers):
        raise _status_error(503)

    with caplog.at_level(logging.DEBUG):
        assert notify_system("health", config=Cfg(), transport=transport).sent is False
    _assert_no_secret(caplog)


def test_options_companion_failure_log_is_redacted(caplog, monkeypatch):
    import notifications.discord_notifier as dn
    from options_companion import notify as companion

    def boom(url, body, headers):
        raise _status_error(429, body={"retry_after": 1})

    monkeypatch.setattr(dn, "_post_json", boom)
    monkeypatch.setenv("DISCORD_OPTIONS_ERROR", WEBHOOK)
    with caplog.at_level(logging.DEBUG):
        assert companion._post("DISCORD_OPTIONS_ERROR", "hello") is False
    _assert_no_secret(caplog)


def test_tradovate_session_alert_failure_log_is_redacted(caplog, monkeypatch):
    import requests

    from execution import tradovate_broker as tb

    def boom(url, json=None, timeout=None):
        raise requests.HTTPError(f"429 Client Error: Too Many Requests for url: {url}")

    monkeypatch.setattr(tb.requests, "post", boom)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    with caplog.at_level(logging.DEBUG):
        tb.TradovateBroker._send_session_alert(object(), "Tradovate session down")
    _assert_no_secret(caplog)


def test_force_close_legacy_fallback_failure_log_is_redacted(caplog, monkeypatch):
    import notifications.discord_notifier as dn
    from webhook import runner

    class RunNow:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class Cfg:
        discord_notifications_enabled = True
        discord_webhook_url = WEBHOOK

    def boom(url, body, headers):
        raise _status_error(500)

    monkeypatch.setattr("threading.Thread", RunNow)            # runner imports threading locally
    monkeypatch.setattr(dn, "_post_json", boom)
    monkeypatch.delenv("DISCORD_ROUTE_ERROR", raising=False)
    with caplog.at_level(logging.DEBUG):
        runner._notify_force_close(instrument="MNQ", reason="STALE_FEED", contracts=1, pnl_dollars=-5.0, config=Cfg())
    _assert_no_secret(caplog)


def test_observation_worker_error_log_is_redacted(dispatcher, caplog):
    class Exploding:
        def send(self, *a, **k):
            raise RuntimeError(f"unexpected failure posting to {WEBHOOK}")

    with caplog.at_level(logging.DEBUG):
        dispatcher.submit(Exploding(), obs.build_messages([obs.format_event(_events(1)[0])]))
        assert dispatcher.join(5)
    assert dispatcher.dropped_cards == 1
    _assert_no_secret(caplog)
