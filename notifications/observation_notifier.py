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
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

ROUTE_NAME = "observation"
LABEL = "OBSERVATION ONLY"


def _fmt_price(value) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "?"


def _bar_label(ts: object) -> str:
    text = str(ts or "")
    return text[:16].replace("T", " ") + "Z" if len(text) >= 16 else text


def format_event(event: dict) -> Optional[str]:
    """Short sections per event; returns None for anything not announceable.

    The instrument root is always the first token and ``OBSERVATION ONLY`` is
    always the second, so a reader can never mistake the line for a signal.
    """
    root = str(event.get("instrument") or "").strip()
    strategy = str(event.get("strategy") or "").strip()
    direction = str(event.get("direction") or "").strip().upper()
    kind = str(event.get("record_type") or "").strip().upper()
    if not root or not strategy or kind not in ("CANDIDATE", "SIGNAL", "OUTCOME"):
        return None
    head = f"{root} — {LABEL} — {strategy} {direction}".rstrip()
    if kind == "OUTCOME":
        result = str(event.get("result") or "").upper()
        pnl_r = event.get("pnl_r")
        r_text = f" {float(pnl_r):+.2f}R" if isinstance(pnl_r, (int, float)) else ""
        reason = str(event.get("exit_reason") or "").strip()
        tail = f" ({reason})" if reason else ""
        return f"{head}\nOutcome: {result}{r_text}{tail}\nBar: {_bar_label(event.get('resolved_at_bar_ts') or event.get('exit_timestamp'))}"
    mode = "structural candidate" if kind == "CANDIDATE" else "signal (bracket not authoritative)"
    return (
        f"{head}\n{mode}\nentry {_fmt_price(event.get('entry'))} · stop {_fmt_price(event.get('stop'))} "
        f"· target {_fmt_price(event.get('target'))}\n15m bar {_bar_label(event.get('signal_timestamp'))}"
    )


def notify_observation(events: Iterable[dict], *, router=None) -> int:
    """Send one message per announceable event on the ``observation`` route.

    Returns the number of messages the router reported delivered. Never
    raises: a missing/disabled route, a missing routes file, or a Discord
    failure all end here with a log line and 0.
    """
    lines = [line for line in (format_event(e) for e in events if isinstance(e, dict)) if line]
    if not lines:
        return 0
    try:
        if router is None:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
        if not router.is_enabled(ROUTE_NAME):
            return 0
        sent = 0
        for line in lines:
            if router.send(ROUTE_NAME, line):
                sent += 1
        return sent
    except Exception:  # noqa: BLE001 — notification is a side effect; observation must continue
        logger.warning("observation Discord notification skipped", exc_info=True)
        return 0
