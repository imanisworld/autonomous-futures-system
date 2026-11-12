"""Decision Discord transport must not hold the serialized futures alert path."""

from __future__ import annotations

import threading
import time

from notifications.decision_queue import DecisionDispatcher


def test_enqueue_returns_without_waiting_for_discord():
    gate = threading.Event()
    started = threading.Event()
    sent = []
    dispatcher = DecisionDispatcher(sleep=lambda _: None)

    def slow_send():
        started.set()
        gate.wait(2)
        sent.append("sent")
        return True

    t0 = time.monotonic()
    assert dispatcher.enqueue(slow_send) is True
    assert time.monotonic() - t0 < 0.5
    assert started.wait(1)
    assert sent == []
    gate.set()
    assert dispatcher.join(3)
    assert sent == ["sent"]
    assert dispatcher.delivered == 1


def test_bounded_queue_drops_excess_without_blocking():
    gate = threading.Event()
    started = threading.Event()
    dispatcher = DecisionDispatcher(max_pending=1, sleep=lambda _: None)
    delivered = []

    def first():
        started.set()
        gate.wait(2)
        delivered.append(1)
        return True

    assert dispatcher.enqueue(first)
    assert started.wait(1)
    assert dispatcher.enqueue(lambda: (delivered.append(2), True)[1])
    t0 = time.monotonic()
    assert dispatcher.enqueue(lambda: (delivered.append(3), True)[1]) is False
    assert time.monotonic() - t0 < 0.5
    gate.set()
    assert dispatcher.join(3)
    assert delivered == [1, 2]
    assert dispatcher.dropped == 1


def test_failed_transport_does_not_kill_worker():
    dispatcher = DecisionDispatcher(sleep=lambda _: None)
    received = []

    def fail():
        raise RuntimeError("synthetic HTTP failure")

    assert dispatcher.enqueue(fail)
    assert dispatcher.enqueue(lambda: (received.append("later"), True)[1])
    assert dispatcher.join(3)
    assert received == ["later"]
    assert dispatcher.dropped == 1
    assert dispatcher.delivered == 1


def test_stale_message_is_not_sent():
    now = [100.0]
    gate = threading.Event()
    started = threading.Event()
    dispatcher = DecisionDispatcher(
        max_age_seconds=10.0, clock=lambda: now[0], sleep=lambda _: None
    )
    seen = []

    def first():
        started.set()
        gate.wait(2)
        return True

    assert dispatcher.enqueue(first)
    assert started.wait(1)
    assert dispatcher.enqueue(lambda: (seen.append("stale"), True)[1])
    now[0] = 120.0
    gate.set()
    assert dispatcher.join(3)
    assert seen == []
    assert dispatcher.dropped == 1


def test_false_transport_result_is_not_counted_as_delivered():
    dispatcher = DecisionDispatcher(sleep=lambda _: None)
    assert dispatcher.enqueue(lambda: False)
    assert dispatcher.join(3)
    assert dispatcher.delivered == 0
    assert dispatcher.dropped == 1


def test_webhook_alert_processing_does_not_wait_for_signal_http(monkeypatch, tmp_path):
    """The journal/decision path returns while the Discord POST is still blocked."""
    from datetime import datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    import notifications.decision_queue as delivery
    import notifications.futures_advisory as advisory
    import notifications.discord_router as router_module
    import webhook.app as app
    from webhook.payload import AlertPayload

    dispatcher = DecisionDispatcher(sleep=lambda _: None)
    monkeypatch.setattr(delivery, "DISPATCHER", dispatcher)

    result = {"decision": "TRADE", "context": {"instrument": "MNQ", "session": "new_york"}}
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("America/New_York")).isoformat(),
        timeframe="15",
        open=25000.0,
        high=25010.0,
        low=24990.0,
        close=25005.0,
    )
    monkeypatch.setattr(
        app, "_config",
        SimpleNamespace(
            log_dir=str(tmp_path),
            live_quote_enabled=False,
            discord_notify_decisions=["TRADE"],
            discord_notifications_enabled=True,
        )
    )
    calls = []
    monkeypatch.setattr(app, "process_alert", lambda *a, **k: calls.append("decided") or result)
    monkeypatch.setattr(app, "_record_latest_webhook", lambda *a, **k: None)
    monkeypatch.setattr(app, "append_observer_response_audit", lambda *a, **k: None)
    monkeypatch.setattr(
        app, "_decision_notification_market_gate",
        lambda *a, **k: (True, None, "MNQ"),
    )
    monkeypatch.setattr(advisory, "notify_futures_advisory", lambda *a, **k: 0)

    started = threading.Event()
    release = threading.Event()
    routes = []

    class SlowRouter:
        def is_enabled(self, name):
            return name == "signal"

        def send(self, name, body):
            started.set()
            release.wait(2)
            routes.append(name)
            return True

    monkeypatch.setattr(router_module, "DiscordRouter", SlowRouter)

    started_at = time.monotonic()
    app._handle_alert_blocking(payload)
    elapsed = time.monotonic() - started_at
    assert elapsed < 0.5
    assert calls == ["decided"]
    assert started.wait(1)

    release.set()
    assert dispatcher.join(3)
    assert routes == ["signal"]
    assert dispatcher.delivered == 1
