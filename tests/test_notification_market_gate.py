from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from webhook.payload import AlertPayload

_ET = ZoneInfo("America/New_York")


def _payload(ts: datetime, ticker: str = "MNQ1!") -> AlertPayload:
    return AlertPayload(
        ticker=ticker,
        timestamp=ts.isoformat(),
        timeframe="15",
        open=25000.0,
        high=25010.0,
        low=24990.0,
        close=25005.0,
    )


def _trade_result(instrument: str = "MNQ") -> dict:
    return {
        "decision": "TRADE",
        "context": {"instrument": instrument, "session": "new_york"},
    }


@pytest.mark.parametrize(
    ("when", "reason"),
    [
        (datetime(2026, 9, 19, 10, 0, tzinfo=_ET), "MARKET_CLOSED_AT_SIGNAL"),
        (datetime(2026, 9, 20, 12, 0, tzinfo=_ET), "MARKET_CLOSED_AT_SIGNAL"),
        (datetime(2026, 9, 21, 17, 30, tzinfo=_ET), "MARKET_CLOSED_AT_SIGNAL"),
    ],
)
def test_notification_gate_blocks_known_mnq_closures(when, reason):
    from webhook.app import _decision_notification_market_gate

    allowed, actual_reason, root = _decision_notification_market_gate(
        _payload(when),
        _trade_result(),
        now=when,
    )

    assert allowed is False
    assert actual_reason == reason
    assert root == "MNQ"


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 9, 21, 10, 0, tzinfo=_ET),
        # CME removed the 16:15–16:30 ET equity-index halt effective 2021-06-28.
        datetime(2026, 9, 21, 16, 20, tzinfo=_ET),
    ],
)
def test_notification_gate_allows_normal_open_session(when):
    from webhook.app import _decision_notification_market_gate

    allowed, reason, root = _decision_notification_market_gate(
        _payload(when),
        _trade_result(),
        now=when,
    )

    assert allowed is True
    assert reason is None
    assert root == "MNQ"


def test_notification_gate_blocks_when_market_closes_before_delivery():
    from webhook.app import _decision_notification_market_gate

    signal = datetime(2026, 9, 21, 16, 59, tzinfo=_ET)
    send = datetime(2026, 9, 21, 17, 5, tzinfo=_ET)
    allowed, reason, root = _decision_notification_market_gate(
        _payload(signal),
        _trade_result(),
        now=send,
    )

    assert allowed is False
    assert reason == "MARKET_CLOSED_BEFORE_SEND"
    assert root == "MNQ"


def test_notification_gate_fails_closed_for_unknown_product():
    from webhook.app import _decision_notification_market_gate

    when = datetime(2026, 9, 21, 10, 0, tzinfo=_ET)
    allowed, reason, root = _decision_notification_market_gate(
        _payload(when, ticker="ZZZ1!"),
        _trade_result(instrument="ZZZ"),
        now=when,
    )

    assert allowed is False
    assert reason == "UNKNOWN_PRODUCT_CALENDAR"
    assert root is None


def test_handle_alert_does_not_fall_through_to_legacy_discord_when_suppressed(
    monkeypatch, caplog
):
    import webhook.app as app_module

    payload = _payload(datetime(2026, 9, 19, 10, 0, tzinfo=_ET))
    payload.event_id = "evt-market-closed"
    result = _trade_result()

    monkeypatch.setattr(app_module, "process_alert", lambda *args, **kwargs: result)
    monkeypatch.setattr(app_module, "_record_latest_webhook", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app_module, "append_observer_response_audit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(app_module._config, "live_quote_enabled", False)
    monkeypatch.setattr(app_module._config, "discord_notify_decisions", ["TRADE"])
    monkeypatch.setattr(
        app_module,
        "_decision_notification_market_gate",
        lambda *args, **kwargs: (False, "MARKET_CLOSED_AT_SIGNAL", "MNQ"),
    )

    legacy_calls = []
    monkeypatch.setattr(
        app_module,
        "notify_discord",
        lambda *args, **kwargs: legacy_calls.append((args, kwargs)),
    )

    with caplog.at_level("INFO"):
        app_module._handle_alert_blocking(payload)

    assert legacy_calls == []
    assert "notification_suppressed_reason=MARKET_CLOSED_AT_SIGNAL" in caplog.text
    assert "event_id=evt-market-closed" in caplog.text
