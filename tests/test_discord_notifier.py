"""
tests/test_discord_notifier.py

Discord notifications are read-only observability. They must never be required
for paper trading to run.
"""

from __future__ import annotations

import json

from notifications.discord_notifier import main, notify_discord, smoke_test_payload
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
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "contracts": 1,
        },
        "context": {
            "instrument": "MNQ",
            "session": "new_york",
            "close": 19505.25,
            "market_condition": "TRENDING",
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
    assert "MNQ" in sent["body"]["content"]


def test_smoke_test_payload_is_synthetic_paper_decision():
    payload, result = smoke_test_payload()

    assert payload.ticker == "MNQ1!"
    assert result["decision"] == "TRADE"
    assert result["context"]["instrument"] == "MNQ"


def test_discord_cli_dry_run_prints_message(capsys):
    exit_code = main(["--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RiskSentinel paper decision: TRADE" in captured.out
    assert "MNQ" in captured.out


def test_trade_alert_shows_confluence_score():
    """TRADE result with confluence key produces the rich format."""
    from notifications.discord_notifier import _format_message

    result = _result()
    result["confluence"] = {
        "score": 9,
        "grade": "A+",
        "factors": ["VWAP aligned (+2)", "Trend UP STRONG (+2)"],
        "penalties": [],
    }
    msg = _format_message(_payload(), result)

    assert "A+ SETUP" in msg
    assert "Score: 9/10" in msg
    assert "VWAP aligned (+2)" in msg
    assert "Trend UP STRONG (+2)" in msg
    assert "orb_reclaim (ORB High Reclaim)" in msg


def test_no_trade_alert_stays_minimal():
    """NO_TRADE decisions keep the short format — no score, no grade."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    msg = _format_message(_payload(), result)

    assert "RiskSentinel paper decision: NO_TRADE" in msg
    assert "SETUP" not in msg
    assert "Score:" not in msg


def test_no_trade_alert_labels_price_source_and_bar_time():
    """Discord should make clear the visible close came from TradingView."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    msg = _format_message(_payload(), result)

    assert "close=19505.25 (TV payload)" in msg
    assert "bar=2026-05-23 10:30 ET" in msg


def test_no_trade_alert_prefers_enriched_context_close():
    """If a future enrichment supplies a better close, Discord should show it."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["context"]["close"] = 30712.5
    msg = _format_message(_payload(), result)

    assert "close=30712.50 (decision context)" in msg
    assert "19505.25" not in msg


def test_no_trade_alert_shows_live_quote_over_bar_close():
    """When a live index quote is attached, show it as the price and label the
    (possibly stale) bar close separately."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["live_quote"] = {"price": 25180.75, "symbol": "NQ=F", "source": "yahoo:NQ=F"}
    msg = _format_message(_payload(), result)

    assert "25180.75 (live NQ=F)" in msg
    assert "bar 19505.25" in msg
    # Must not relabel the stale bar value as the authoritative price.
    assert "19505.25 (TV payload)" not in msg
