"""
tests/test_discord_notifier.py

Discord notifications are read-only observability. They must never be required
for paper trading to run.
"""

from __future__ import annotations

import json

from notifications.discord_notifier import notify_discord
from webhook.payload import AlertPayload


def _payload() -> AlertPayload:
    return AlertPayload(
        ticker="MNQ1!",
        timestamp="2026-05-23T14:30:00+00:00",
        open=19480.0,
        high=19510.0,
        low=19475.0,
        close=19505.25,
    )


def _result(decision: str = "TRADE") -> dict:
    return {
        "decision": decision,
        "resolution": None,
        "risk": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "fill": {
            "direction": "LONG",
            "entry": 19505.25,
            "stop": 19495.25,
            "target": 19525.25,
            "contracts": 1,
        },
        "context": {
            "instrument": "MNQ",
            "session": "new_york",
            "close": 19505.25,
        },
    }


def test_discord_notification_disabled_by_default(config):
    called = False

    def transport(url, body, headers):
        nonlocal called
        called = True

    result = notify_discord(
        payload=_payload(),
        result=_result(),
        config=config,
        transport=transport,
    )

    assert result.sent is False
    assert result.reason == "disabled"
    assert called is False


def test_discord_notification_requires_webhook_url(config):
    config.discord_notifications_enabled = True
    config.discord_webhook_url = ""

    result = notify_discord(payload=_payload(), result=_result(), config=config)

    assert result.sent is False
    assert result.reason == "missing_webhook_url"


def test_discord_notification_filters_unwanted_decisions(config):
    config.discord_notifications_enabled = True
    config.discord_webhook_url = "https://discord.example/webhook"
    config.discord_notify_decisions = ["TRADE"]

    result = notify_discord(
        payload=_payload(),
        result=_result("NO_TRADE"),
        config=config,
        transport=lambda url, body, headers: None,
    )

    assert result.sent is False
    assert result.reason == "decision_filtered"


def test_discord_notification_sends_paper_decision(config):
    sent = {}
    config.discord_notifications_enabled = True
    config.discord_webhook_url = "https://discord.example/webhook"
    config.discord_notify_decisions = ["TRADE"]

    def transport(url, body, headers):
        sent["url"] = url
        sent["body"] = json.loads(body.decode("utf-8"))
        sent["headers"] = headers

    result = notify_discord(
        payload=_payload(),
        result=_result(),
        config=config,
        transport=transport,
    )

    assert result.sent is True
    assert sent["url"] == "https://discord.example/webhook"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert "RiskSentinel paper decision: TRADE" in sent["body"]["content"]
    assert "MNQ | new_york" in sent["body"]["content"]
