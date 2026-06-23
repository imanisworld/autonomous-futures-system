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
    assert result["smoke_test"] is True
    assert result["decision"] == "TRADE"
    assert result["context"]["instrument"] == "MNQ"


def test_discord_cli_dry_run_prints_message(capsys):
    exit_code = main(["--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "DISCORD SMOKE TEST - NOT A JOURNALED TRADE" in captured.out
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


def test_no_trade_alert_labels_bar_close_and_12h_time():
    """Bar close is labelled as such; bar time is 12-hour with AM/PM."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    msg = _format_message(_payload(), result)

    assert "Bar close: 19505.25" in msg
    # 14:30 UTC → 10:30 AM ET, 12-hour clock with meridiem.
    assert "Bar time: 2026-05-23 10:30 AM ET" in msg
    assert "10:30 ET" not in msg  # no 24-hour remnants


def test_no_trade_alert_prefers_enriched_context_close():
    """If a future enrichment supplies a better close, Discord should show it."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["context"]["close"] = 30712.5
    msg = _format_message(_payload(), result)

    assert "Bar close: 30712.50" in msg  # enriched context_close still preferred, just unlabelled


def test_no_trade_alert_shows_reference_price_clearly_labelled():
    """A live index quote shows as a clearly-labelled, display-only reference
    price with source + status — separate from the bar close."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["live_quote"] = {
        "price": 25180.75, "symbol": "NQ=F", "source": "ES=F/NQ=F HTTP proxy",
        "age_seconds": 3, "status": "FRESH", "kind": "reference",
    }
    msg = _format_message(_payload(), result)

    assert "Reference price: 25180.75 (ES=F/NQ=F HTTP proxy · FRESH · 3s ago)" in msg
    assert "Bar close: 19505.25" in msg  # bar close kept, distinct


def test_no_trade_alert_reference_price_unavailable():
    """When the proxy is unavailable, say so explicitly — never blank/misleading."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["live_quote"] = {
        "price": None, "symbol": "NQ=F", "source": "ES=F/NQ=F HTTP proxy",
        "age_seconds": None, "status": "UNAVAILABLE", "kind": "reference",
    }
    msg = _format_message(_payload(), result)

    assert "Reference price: unavailable (ES=F/NQ=F HTTP proxy · UNAVAILABLE)" in msg


def test_rejected_alert_states_the_reason():
    """A rejection must explain WHY — not a bare 'Risk: REJECTED'."""
    from notifications.discord_notifier import _format_message

    result = _result("RISK_REJECTED")
    result["risk"] = {
        "result": "REJECTED", "failed_rule": "session_cutoff",
        "reason": "Outside session window: london ended 02:30 ET",
    }
    msg = _format_message(_payload(), result)

    assert "Risk: REJECTED — Outside session window: london ended 02:30 ET" in msg


def test_rejected_alert_falls_back_to_failed_rule_when_no_reason():
    from notifications.discord_notifier import _format_message

    result = _result("RISK_REJECTED")
    result["risk"] = {"result": "REJECTED", "failed_rule": "max_daily_loss", "reason": None}
    msg = _format_message(_payload(), result)

    assert "Risk: REJECTED — max_daily_loss" in msg


def test_approved_trade_has_no_reason_suffix():
    """Approved trades carry no reason — stay a clean 'Risk: APPROVED'."""
    from notifications.discord_notifier import _format_message

    msg = _format_message(_payload(), _result("TRADE"))

    assert "Risk: APPROVED" in msg
    assert "Risk: APPROVED —" not in msg
    assert "Bar close: 19505.25" in msg
