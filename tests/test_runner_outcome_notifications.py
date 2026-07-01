from __future__ import annotations

from types import SimpleNamespace

import pytest

import webhook.runner as runner


@pytest.fixture(autouse=True)
def _isolate_discord_routes(monkeypatch):
    """Router reads os.environ directly by default; keep tests deterministic
    regardless of what DISCORD_ROUTE_* vars a developer's shell happens to export."""
    for name in ("DISCORD_ROUTE_HEARTBEAT", "DISCORD_ROUTE_SIGNAL", "DISCORD_ROUTE_SIGNA",
                 "DISCORD_ROUTE_ERROR", "DISCORD_ROUTE_DAILY_REPORT", "DISCORD_ROUTE_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)


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


def test_force_close_falls_back_to_legacy_webhook_when_router_disabled(monkeypatch):
    sent = []
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "notifications.discord_notifier._post_json",
        lambda url, body, headers: sent.append((url, body)),
    )

    runner._notify_force_close(
        instrument="MES",
        reason="SESSION_TIMEOUT",
        contracts=1,
        pnl_dollars=-5.0,
        config=SimpleNamespace(
            discord_notifications_enabled=True,
            discord_webhook_url="https://example.invalid",
        ),
        simulate=False,
    )

    assert len(sent) == 1
    assert sent[0][0] == "https://example.invalid"
    assert b"FORCE_CLOSE" in sent[0][1]


def test_force_close_prefers_router_error_route_when_enabled(monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTE_ERROR", "https://router.invalid")
    monkeypatch.setattr("threading.Thread", _ImmediateThread)

    router_calls = []
    legacy_calls = []
    monkeypatch.setattr(
        "notifications.discord_router.DiscordRouter.send",
        lambda self, route, message, metadata=None: router_calls.append((route, message)) or True,
    )
    monkeypatch.setattr(
        "notifications.discord_notifier._post_json",
        lambda url, body, headers: legacy_calls.append(url),
    )

    runner._notify_force_close(
        instrument="MNQ",
        reason="FEED_GAP",
        contracts=2,
        pnl_dollars=12.5,
        config=SimpleNamespace(
            discord_notifications_enabled=True,
            discord_webhook_url="https://legacy.invalid",
        ),
        simulate=False,
    )

    assert router_calls == [("error", router_calls[0][1])]
    assert "FORCE_CLOSE" in router_calls[0][1]
    assert legacy_calls == []  # router handled it — legacy fallback never called


def test_trade_closed_prefers_router_daily_report_route_when_enabled(monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTE_DAILY_REPORT", "https://router.invalid")
    monkeypatch.setattr("threading.Thread", _ImmediateThread)

    router_calls = []
    legacy_calls = []
    monkeypatch.setattr(
        "notifications.discord_router.DiscordRouter.send",
        lambda self, route, message, metadata=None: router_calls.append((route, message)) or True,
    )
    monkeypatch.setattr(
        "notifications.discord_notifier.send_discord_alert",
        lambda config, message: legacy_calls.append(message),
    )

    runner._notify_trade_closed(
        fill=_fill("MES"),
        session="new_york",
        day_pnl_dollars=10.0,
        config=SimpleNamespace(discord_webhook_url="https://legacy.invalid"),
        simulate=False,
    )

    assert router_calls == [("daily_report", router_calls[0][1])]
    assert legacy_calls == []  # router handled it — legacy fallback never called
