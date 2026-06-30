from __future__ import annotations

from types import SimpleNamespace

import webhook.runner as runner


class _ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def _fill(instrument="MGC"):
    return SimpleNamespace(
        result="WIN",
        instrument=instrument,
        direction="LONG",
        pnl_dollars=20.0,
        pnl_ticks=10.0,
        entry_price=2400.0,
        exit_price=2401.0,
        exit_reason="TARGET",
        contracts=1,
    )


def test_trade_closed_notification_is_suppressed_for_simulation(monkeypatch):
    def _must_not_start(*args, **kwargs):
        raise AssertionError("simulation must not start a notification thread")

    monkeypatch.setattr("threading.Thread", _must_not_start)
    runner._notify_trade_closed(
        fill=_fill(),
        session="new_york",
        day_pnl_dollars=20.0,
        config=SimpleNamespace(discord_webhook_url="https://example.invalid"),
        simulate=True,
    )


def test_live_trade_close_uses_fail_soft_fallback_and_instrument_tick(monkeypatch):
    sent = []
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "notifications.discord_notifier.send_discord_alert",
        lambda config, message: sent.append(message),
    )

    runner._notify_trade_closed(
        fill=_fill("MGC"),
        session="new_york",
        day_pnl_dollars=25.0,
        config=SimpleNamespace(discord_webhook_url="https://example.invalid"),
        simulate=False,
    )

    assert len(sent) == 1
    assert "WIN — MGC LONG" in sent[0]
    assert "(+1.00 pts)" in sent[0]
    assert "Day P&L: +$25.00" in sent[0]


def test_force_close_notification_is_suppressed_for_simulation(monkeypatch):
    def _must_not_start(*args, **kwargs):
        raise AssertionError("simulation must not start a notification thread")

    monkeypatch.setattr("threading.Thread", _must_not_start)
    runner._notify_force_close(
        instrument="MES",
        reason="SESSION_TIMEOUT",
        contracts=1,
        pnl_dollars=-5.0,
        config=SimpleNamespace(
            discord_notifications_enabled=True,
            discord_webhook_url="https://example.invalid",
        ),
        simulate=True,
    )
