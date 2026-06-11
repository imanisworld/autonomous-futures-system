"""Hourly operational heartbeat to Discord.

A "still alive" ping so that silence — now that per-bar NO_TRADE alerts are off —
doesn't read as the system being dead. It is:

- **flag-gated** (`DISCORD_HEARTBEAT_ENABLED`, default off),
- **fail-soft** (never raises; a broken heartbeat can't affect trading),
- **market-aware**: it only sends while bars are actively arriving. If the last
  bar is older than ~70 min the market is closed (or the feed is down, which the
  separate feed-down watchdog owns), so the heartbeat stays quiet instead of
  pinging through the weekend / maintenance halt.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from notifications.discord_notifier import send_discord_alert

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

# Last bar older than this ⇒ market closed / feed down ⇒ skip (no ping).
_MARKET_QUIET_SECONDS = 70 * 60
_DEFAULT_INTERVAL_SECONDS = 3600  # hourly


def build_heartbeat_message(
    *,
    session: str,
    last_bar_age_s: Optional[float],
    has_open_position: bool,
    trades_today: int,
    pnl_today: float,
) -> str:
    """Compose the one-line heartbeat summary (pure / testable)."""
    age = f"last bar {round(last_bar_age_s / 60)}m ago" if last_bar_age_s is not None else "no bars yet"
    position = "in position" if has_open_position else "flat"
    return " · ".join([
        f"\U0001FAC0 heartbeat · {session} session",
        age,
        position,
        f"{trades_today} trade(s) today",
        f"P&L ${pnl_today:.2f}",
    ])


def _last_bar_age_seconds(log_dir: str, now_utc: datetime) -> Optional[float]:
    """Age (seconds) of the most recent webhook bar, or None if unavailable."""
    path = Path(log_dir) / "latest_webhook.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    received = data.get("received_at")
    if not received:
        return None
    try:
        ts = datetime.fromisoformat(str(received).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_UTC)
    return (now_utc - ts).total_seconds()


def maybe_send_heartbeat(
    config: Any,
    log_dir: str,
    *,
    now: Optional[datetime] = None,
    sender: Optional[Callable] = None,
) -> Optional[str]:
    """Send one heartbeat if enabled and the market is active. Returns the
    message that was sent, or None if skipped. Never raises."""
    if not getattr(config, "discord_heartbeat_enabled", False):
        return None
    try:
        now_et = now or datetime.now(_ET)
        now_utc = now_et.astimezone(_UTC)

        age = _last_bar_age_seconds(log_dir, now_utc)
        if age is None or age > _MARKET_QUIET_SECONDS:
            return None  # market closed / feed down → stay quiet

        from journal.journal_logger import JournalLogger
        from webhook.state_builder import detect_session

        state = JournalLogger(log_dir=log_dir).get_daily_state()
        message = build_heartbeat_message(
            session=detect_session(now_utc),
            last_bar_age_s=age,
            has_open_position=bool(getattr(state, "has_open_position", False)),
            trades_today=int(getattr(state, "trade_count", 0) or 0),
            pnl_today=float(getattr(state, "realized_pnl_dollars", 0.0) or 0.0),
        )
        if sender is None:
            try:
                from notifications.discord_router import DiscordRouter
                _router = DiscordRouter()
                if _router.is_enabled("heartbeat"):
                    _router.send("heartbeat", message)
                    return message
            except Exception as _exc:
                logger.warning("heartbeat router failed: %s", _exc)
        send_discord_alert(config, message, transport=sender)
        return message
    except Exception as exc:  # pragma: no cover - defensive; heartbeat must never raise
        logger.warning("heartbeat failed: %s", exc)
        return None


async def run_heartbeat_loop(
    config: Any,
    log_dir: str,
    *,
    interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    sleep: Callable = asyncio.sleep,
) -> None:
    """Background loop: emit an hourly heartbeat. Sleeps first so a restart does
    not immediately ping. Cancellation-safe; exceptions are swallowed per tick."""
    while True:
        await sleep(interval_seconds)
        maybe_send_heartbeat(config, log_dir)
