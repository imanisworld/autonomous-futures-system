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
