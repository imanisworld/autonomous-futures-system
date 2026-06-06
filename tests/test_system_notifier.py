from __future__ import annotations

import json

from notifications.system_notifier import notify_system


def test_system_notifier_disabled_by_default(config):
    config.discord_notifications_enabled = False
    config.discord_webhook_url = "https://discord.test"

    result = notify_system("Broker heartbeat: connected", config=config)

    assert result.sent is False
    assert result.reason == "disabled"


def test_system_notifier_sends_when_configured(config):
    sent = {}

    def transport(url: str, body: bytes, headers: dict[str, str]) -> None:
        sent["url"] = url
        sent["body"] = json.loads(body.decode("utf-8"))
        sent["headers"] = headers

    config.discord_notifications_enabled = True
    config.discord_webhook_url = "https://discord.test/webhook"

    result = notify_system("Broker heartbeat: connected", config=config, transport=transport)

    assert result.sent is True
    assert sent["url"] == "https://discord.test/webhook"
    assert sent["body"] == {"content": "Broker heartbeat: connected"}
    assert sent["headers"] == {"Content-Type": "application/json"}
