"""
tests/test_discord_notifier.py

Discord notifications are read-only observability. They must never be required
for paper trading to run.
"""

from __future__ import annotations

import json

from notifications.discord_notifier import main, notify_discord, smoke_test_payload
from webhook.payload import AlertPayload


def _assert_plain(text: str) -> None:
    """Operator text speaks plain English (docs/discord-operator-message-style.md)."""
    import re

    for jargon in ("LONG", "SHORT", "R:R", "strat_", "TRADE", "APPROVED", "REJECTED"):
        assert jargon not in text, jargon
    assert not re.search(r"\d{2}:\d{2}(:\d{2})?Z", text)  # no UTC Z stamps
    assert not re.search(r"\bR\b", text)  # no R multiples


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
    assert sent["body"]["allowed_mentions"] == {"parse": []}
    card = sent["body"]["embeds"][0]
    assert card["title"] == "✅ MNQ practice buy taken"
    assert "Practice trade taken" in card["description"]
    assert "MNQ (Micro Nasdaq)" in card["description"]
    fields = {field["name"]: field["value"] for field in card["fields"]}
    assert fields["Direction"] == "Buy 1 contract"
    assert fields["Risk"] == "$20.00 to make $40.00"
    assert card["footer"]["text"].startswith("READ ONLY")
    assert "nothing is ever placed from Discord" in card["footer"]["text"]
    _assert_plain(json.dumps(card, ensure_ascii=False))


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
    assert "TEST MESSAGE — made-up example, not a real decision and not saved" in captured.out
    assert "✅ MNQ practice buy taken" in captured.out
    assert "MNQ" in captured.out
    _assert_plain(captured.out)


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

    assert "Setup quality: very strong (9 of 10)" in msg
    assert "✅ on the right side of the day's average price" in msg
    assert "✅ trend is up (strong)" in msg
    assert "Setup: back inside the opening range" in msg
    # No scorer jargon or point scores.
    for jargon in ("A+", "VWAP", "(+2)", "orb_reclaim", "ORB", "/10"):
        assert jargon not in msg
    _assert_plain(msg)


def test_no_trade_alert_stays_minimal():
    """NO_TRADE decisions keep the short format — no score, no grade."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    msg = _format_message(_payload(), result)

    assert msg.splitlines()[0] == "⚪ MNQ: No trade"
    assert "Setup quality" not in msg
    assert "Why it looked good" not in msg


def test_no_trade_alert_labels_bar_close_and_12h_time():
    """Bar close is labelled as such; bar time is 12-hour with AM/PM."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    msg = _format_message(_payload(), result)

    # 14:30 UTC → 10:30 AM ET, 12-hour clock with meridiem.
    assert "Price when decided: 19,505.25 at 10:30 AM ET, Sat May 23" in msg
    assert "10:30 ET" not in msg  # no 24-hour remnants
    _assert_plain(msg)


def test_no_trade_alert_prefers_enriched_context_close():
    """If a future enrichment supplies a better close, Discord should show it."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["context"]["close"] = 30712.5
    msg = _format_message(_payload(), result)

    assert "Price when decided: 30,712.5 at" in msg  # enriched context_close still preferred


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

    assert "Reference price: 25,180.75 (live; index price, not the order price)" in msg
    assert "Price when decided: 19,505.25" in msg  # bar close kept, distinct


def test_no_trade_alert_reference_price_unavailable():
    """When the proxy is unavailable, say so explicitly — never blank/misleading."""
    from notifications.discord_notifier import _format_message

    result = _result("NO_TRADE")
    result["live_quote"] = {
        "price": None, "symbol": "NQ=F", "source": "ES=F/NQ=F HTTP proxy",
        "age_seconds": None, "status": "UNAVAILABLE", "kind": "reference",
    }
    msg = _format_message(_payload(), result)

    assert "Reference price: unavailable" in msg


def test_rejected_alert_states_the_reason():
    """A rejection must explain WHY — not a bare 'Risk: REJECTED'."""
    from notifications.discord_notifier import _format_message

    result = _result("RISK_REJECTED")
    result["risk"] = {
        "result": "REJECTED", "failed_rule": "session_cutoff",
        "reason": "Outside session window: london ended 02:30 ET",
    }
    msg = _format_message(_payload(), result)

    assert msg.splitlines()[0] == "⛔ MNQ: Skipped — risk limits"
    assert "Risk check: blocked — Outside session window: london ended 02:30 ET" in msg


def test_rejected_alert_falls_back_to_failed_rule_when_no_reason():
    from notifications.discord_notifier import _format_message

    result = _result("RISK_REJECTED")
    result["risk"] = {"result": "REJECTED", "failed_rule": "max_daily_loss", "reason": None}
    msg = _format_message(_payload(), result)

    assert "Risk check: blocked — hit today's loss limit" in msg
    assert "max_daily_loss" not in msg


def test_approved_trade_has_no_reason_suffix():
    """Approved trades carry no reason — stay a clean 'Risk: APPROVED'."""
    from notifications.discord_notifier import _format_message

    msg = _format_message(_payload(), _result("TRADE"))

    assert "Risk check: passed" in msg
    assert "Risk check: passed —" not in msg
    assert "Price when decided: 19,505.25" in msg


def test_non_trade_alert_surfaces_general_reason():
    from notifications.discord_notifier import _format_message
    result = _result("BLOCKED_MAX_TRADES")
    result["risk"] = None
    result["reason"] = "Daily trade capacity reached before strategy evaluation."
    msg = _format_message(_payload(), result)
    assert msg.splitlines()[0] == "⛔ MNQ: Skipped — already hit today's trade limit"
    assert "Why: Daily trade capacity reached before strategy evaluation." in msg


def test_non_trade_alert_prefers_gate_reason():
    from notifications.discord_notifier import _format_message
    result = _result("ORDER_SUPPRESSED")
    result["risk"] = None
    result["reason"] = "generic"
    result["gate_reason"] = "working_order_conflict: 1 working order(s) on account"
    msg = _format_message(_payload(), result)
    assert "Why: working order conflict: 1 working order(s) on account" in msg
    assert "Why: generic" not in msg


def test_non_trade_alert_falls_back_to_failed_gates():
    from notifications.discord_notifier import _format_message
    result = _result("NO_TRADE")
    result["risk"] = None
    result["failed_gates"] = ["ENTRY_DETACHED_FROM_PRICE"]
    msg = _format_message(_payload(), result)
    assert "Why: entry detached from price" in msg


def test_execution_failure_reads_as_urgent_and_red():
    from notifications.discord_notifier import _discord_payload

    result = _result("BLOCKED_EXECUTION_FAILED")
    result["risk"] = None
    card = _discord_payload(_payload(), result)["embeds"][0]
    assert card["title"] == "🚨 MNQ: Order FAILED — not placed"
    assert card["color"] == 0xED4245


def test_shadow_no_order_says_practice_only():
    from notifications.discord_notifier import _format_message

    result = _result("SHADOW_NO_ORDER")
    result["risk"] = None
    result["gate_reason"] = "always_on_shadow is read-only — no orders, ever"
    msg = _format_message(_payload(), result)
    assert "no order sent" in msg.splitlines()[0]
    assert "Why: practice-only mode — orders are never sent" in msg
    _assert_plain(msg)


def test_risk_falls_back_to_ratio_when_contract_unknown():
    from notifications.discord_notifier import _format_message

    result = _result()
    result["context"]["instrument"] = "ZZZ"
    msg = _format_message(_payload(), result)
    assert "Risk: target is 2× the risk" in msg
