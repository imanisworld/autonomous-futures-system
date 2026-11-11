"""Bounded, best-effort Discord decision delivery, separate from the alert lock.

Presentation only. Decisions and trade evidence are journaled before enqueue.
This worker never imports the broker, risk engine, or strategy evaluator.
A full queue or failed delivery is logged, never treated as a sent message.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

MAX_PENDING = 64
MAX_MESSAGE_AGE_SECONDS = 600.0
MIN_SEND_INTERVAL_SECONDS = 1.0


class DecisionDispatcher:
    def __init__(
        self,
        *,
        max_pending: int = MAX_PENDING,
        max_age_seconds: float = MAX_MESSAGE_AGE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue: queue.Queue[tuple[Callable[[], None], float]] = queue.Queue(maxsize=max_pending)
        self._max_age = max_age_seconds
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.delivered = 0
        self.dropped = 0

    def enqueue(self, send: Callable[[], None]) -> bool:
        """Accept a decision notification without waiting for Discord HTTP."""
        if not callable(send):
            raise TypeError("send must be callable")
        try:
            self._ensure_worker()
            self._queue.put_nowait((send, self._clock()))
            return True
        except queue.Full:
            self.dropped += 1
            logger.error("Decision Discord queue full (%d pending); notification dropped", self._queue.maxsize)
        except Exception as exc:  # noqa: BLE001 — notification cannot affect the decision engine
            self.dropped += 1
            logger.error("Decision Discord worker unavailable: %s", type(exc).__name__)
        return False

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                thread = threading.Thread(
                    target=self._run,
                    name="decision-discord",
                    daemon=True,
                )
                thread.start()
                self._thread = thread

    def _run(self) -> None:
        while True:
            send, queued_at = self._queue.get()
            try:
                age = self._clock() - queued_at
                if age > self._max_age:
                    self.dropped += 1
                    logger.error("Decision Discord notification %.0fs old; dropped as stale", age)
                else:
                    send()
                    self.delivered += 1
            except Exception as exc:  # noqa: BLE001 — worker must survive a failed transport
                self.dropped += 1
                # Do not put transport exception messages (potential webhook tokens) in logs.
                logger.error("Decision Discord delivery failed: %s", type(exc).__name__)
            finally:
                self._queue.task_done()
                self._sleep(MIN_SEND_INTERVAL_SECONDS)

    def join(self, timeout: float = 5.0) -> bool:
        """Test-only helper; production callers never wait for delivery."""
        until = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < until:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks


DISPATCHER = DecisionDispatcher()


def enqueue_decision(send: Callable[[], None]) -> bool:
    return DISPATCHER.enqueue(send)
