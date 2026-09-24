"""Discord messages for cross_instrument_observation_v1 — OBSERVATION ONLY.

Side effect of observation, never an input to it. Sends through the existing
``DiscordRouter`` on the optional ``observation`` route (env var
``DISCORD_ROUTE_OBSERVATION``, see config/notification_routes.yaml). When the
route is unset the router returns False and nothing else happens; when Discord
fails the router logs and returns False. This module never raises into the
observation pipeline and never reads or changes trading state.

Only meaningful state changes are announced: a new CANDIDATE / SIGNAL row and
a resolved structural OUTCOME. Bars that produce nothing are silent.

Watchdog stale alerts, transport failures, runtime errors, service failures,
recovery notices and safety blockers are NOT routed here — they keep their
existing routes.

Delivery (2026-09-23, after the session-open 429 burst): the cards from one
call are grouped into webhook messages of up to ``MAX_EMBEDS_PER_MESSAGE``
cards each, so ~85 session-open events become ~9 requests, not ~85. By
default the messages go onto a bounded queue drained by ONE background
thread, paced ``MIN_SEND_INTERVAL`` apart and honoring Discord's 429
``retry_after``. The caller — the alert path that holds the webhook alert
lock — only formats and enqueues, so a slow or down Discord can never delay
MNQ/MES alert processing. Queue full or a message older than
``MAX_MESSAGE_AGE`` means the message is dropped with a local log line; the
evidence rows are already written before this module is called.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Iterable, Optional

from notifications import plain_english as pe

logger = logging.getLogger(__name__)

ROUTE_NAME = "observation"
LABEL = "OBSERVATION ONLY"

_FOOTER = "OBSERVATION ONLY · practice tracking, no real order was placed"


_fmt_price = pe.price
_money = pe.money
_when = pe.et_time
_market = pe.market
_setup = pe.setup
_side = pe.side


def _dollars_per_point(event: dict) -> Optional[float]:
    return pe.dollars_per_point(event.get("tick_size"), event.get("tick_value_dollars"))


def _leg(event: dict, far_key: str, verb: str) -> str:
    """'7,832.98 (would lose about $2.79)' — the dollar part only when computable."""
    price = _fmt_price(event.get(far_key))
    per_point = _dollars_per_point(event)
    try:
        dollars = abs(float(event[far_key]) - float(event["entry"])) * per_point  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return price
    return f"{price} ({verb} about ${dollars:,.2f})"


def _outcome_dollars(event: dict) -> Optional[float]:
    gross = event.get("gross_pnl_dollars_1_contract")
    if isinstance(gross, (int, float)):
        return float(gross)
    per_point = _dollars_per_point(event)
    points = event.get("pnl_points")
    if per_point is not None and isinstance(points, (int, float)):
        return float(points) * per_point
    return None


def format_event(event: dict) -> Optional[str]:
    """One plain-English card per event; None for anything not announceable.

    Line 1 is the card title and always names the market; the last line is the
    OBSERVATION ONLY footer, so a reader can never mistake it for a real trade.
    """
    root = str(event.get("instrument") or "").strip()
    strategy = str(event.get("strategy") or "").strip()
    direction = str(event.get("direction") or "").strip().upper()
    kind = str(event.get("record_type") or "").strip().upper()
    if not root or not strategy or kind not in ("CANDIDATE", "SIGNAL", "OUTCOME"):
        return None
    side = _side(direction)
    if kind == "OUTCOME":
        result = str(event.get("result") or "").strip().upper()
        won = result == "WIN"
        mark = "🟢" if won else "🔴" if result == "LOSS" else "⚪"
        verdict = {"WIN": "won", "LOSS": "lost"}.get(result, result.lower() or "finished")
        reason = str(event.get("exit_reason") or "").strip().upper()
        lines = [
            f"{mark} {root} practice {side.lower()} {verdict}",
            f"Market: {_market(root)}",
            f"Setup: {_setup(strategy)}",
        ]
        dollars = _outcome_dollars(event)
        if dollars is not None:
            lines.append(f"Result: {_money(dollars)} (1 contract, before fees)")
        if reason:
            lines.append(f"How it ended: {pe.exit_reason(reason)}")
        if event.get("entry") is not None and event.get("exit_price") is not None:
            lines.append(f"Prices: in at {_fmt_price(event.get('entry'))}, out at {_fmt_price(event.get('exit_price'))}")
        lines.append(f"Closed: {_when(event.get('exit_timestamp') or event.get('resolved_at_bar_ts'))}")
        lines.append(_FOOTER)
        return "\n".join(lines)
    lines = [
        f"👀 {root} practice {side.lower()} setup spotted",
        f"Market: {_market(root)}",
        f"Setup: {_setup(strategy)}",
        f"Would {side.lower()} at: {_fmt_price(event.get('entry'))}",
        f"Stop-loss: {_leg(event, 'stop', 'would lose')}",
        f"Profit target: {_leg(event, 'target', 'would make')}",
        f"Seen: {_when(event.get('signal_timestamp'))}",
    ]
    if kind == "SIGNAL":
        lines.append("Note: stop-loss and target here are rough, from the chart alert")
    lines.append(_FOOTER)
    return "\n".join(lines)


# ── Delivery tuning ─────────────────────────────────────────────────────────
MAX_EMBEDS_PER_MESSAGE = 10          # Discord's per-message embed limit.
_MAX_MESSAGE_TEXT = 5800             # Discord caps all embed text at 6000.
MIN_SEND_INTERVAL = 1.0              # seconds between observation posts.
MAX_RETRY_WAIT = 30.0                # longest honored 429 wait (background only).
MAX_QUEUE_MESSAGES = 50              # ~500 cards; beyond this, drop + log.
MAX_MESSAGE_AGE = 600.0              # seconds; stale messages are dropped.

def _redact(exc: BaseException) -> str:
    from notifications.discord_router import redact_webhooks

    return redact_webhooks(exc)


# "background" (production) or "inline" (tests, and callers that pass a router).
DELIVERY_MODE = "background"


def _embed_text_len(embed: dict) -> int:
    return (
        len(str(embed.get("title") or "")) + len(str(embed.get("description") or ""))
        + sum(len(str(f.get("name") or "")) + len(str(f.get("value") or "")) for f in embed.get("fields") or [])
        + len(str((embed.get("footer") or {}).get("text") or ""))
    )


def build_messages(lines: list[str]) -> list[tuple[dict, int]]:
    """Group card texts into webhook bodies: (body, cards_in_body).

    Every card is kept whole and in order; a body holds at most
    MAX_EMBEDS_PER_MESSAGE cards and stays under Discord's total embed-text
    limit. The layout is the same card each event produced before.
    """
    from notifications.discord_card import text_card

    messages: list[tuple[dict, int]] = []
    embeds: list[dict] = []
    size = 0
    for line in lines:
        embed = text_card(line, source="observation route")
        n = _embed_text_len(embed)
        if embeds and (len(embeds) >= MAX_EMBEDS_PER_MESSAGE or size + n > _MAX_MESSAGE_TEXT):
            messages.append(({"allowed_mentions": {"parse": []}, "embeds": embeds}, len(embeds)))
            embeds, size = [], 0
        embeds.append(embed)
        size += n
    if embeds:
        messages.append(({"allowed_mentions": {"parse": []}, "embeds": embeds}, len(embeds)))
    return messages


class _Dispatcher:
    """One daemon thread draining a bounded queue of webhook bodies."""

    def __init__(self, *, sleep=time.sleep, clock=time.monotonic) -> None:
        self._queue: "queue.Queue[tuple[object, dict, int, float]]" = queue.Queue(maxsize=MAX_QUEUE_MESSAGES)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.delivered_cards = 0
        self.dropped_cards = 0

    def submit(self, router, messages: list[tuple[dict, int]]) -> int:
        """Enqueue without blocking; returns the number of cards accepted."""
        self._ensure_thread()
        accepted = 0
        for body, cards in messages:
            try:
                self._queue.put_nowait((router, body, cards, self._clock()))
                accepted += cards
            except queue.Full:
                self.dropped_cards += cards
                logger.error("observation Discord queue full (%d messages); %d card(s) dropped.",
                             MAX_QUEUE_MESSAGES, cards)
        return accepted

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="observation-discord", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            router, body, cards, queued_at = self._queue.get()
            try:
                age = self._clock() - queued_at
                if age > MAX_MESSAGE_AGE:
                    self.dropped_cards += cards
                    logger.error("observation Discord message %.0fs old; %d card(s) dropped.", age, cards)
                    continue
                if router.send(ROUTE_NAME, body, max_retry_wait=MAX_RETRY_WAIT):
                    self.delivered_cards += cards
                else:
                    self.dropped_cards += cards
                    logger.error("observation Discord delivery failed; %d card(s) dropped.", cards)
            except Exception as exc:  # noqa: BLE001 - the worker must survive anything
                self.dropped_cards += cards
                logger.warning("observation Discord worker error; %d card(s) dropped: %s: %s",
                               cards, type(exc).__name__, _redact(exc))
            finally:
                self._queue.task_done()
                self._sleep(MIN_SEND_INTERVAL)

    def join(self, timeout: float = 5.0) -> bool:
        """Test helper: wait until everything queued has been handled."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks


_DISPATCHER = _Dispatcher()


def notify_observation(events: Iterable[dict], *, router=None) -> int:
    """Announce the announceable events on the ``observation`` route.

    Cards are grouped into at most ceil(n / MAX_EMBEDS_PER_MESSAGE) messages.
    In ``background`` mode (the default when no router is passed) they are
    queued for the background sender and the return value is the number of
    cards accepted; nothing here waits on Discord. With an explicit router or
    ``inline`` mode they are sent now and the return value is the number of
    cards delivered. Never raises: a missing/disabled route, a missing routes
    file, or a Discord failure all end here with a log line.
    """
    lines = [line for line in (format_event(e) for e in events if isinstance(e, dict)) if line]
    if not lines:
        return 0
    try:
        inline = router is not None or DELIVERY_MODE == "inline"
        if router is None:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
        if not router.is_enabled(ROUTE_NAME):
            return 0
        messages = build_messages(lines)
        if not inline:
            return _DISPATCHER.submit(router, messages)
        sent = 0
        for body, cards in messages:
            if router.send(ROUTE_NAME, body):
                sent += cards
        return sent
    except Exception as exc:  # noqa: BLE001 — notification is a side effect; observation must continue
        logger.warning("observation Discord notification skipped: %s: %s", type(exc).__name__, _redact(exc))
        return 0
