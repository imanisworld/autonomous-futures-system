"""System-level Discord notifications for local runtime health."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from config.settings import SystemConfig

logger = logging.getLogger(__name__)

Transport = Callable[[str, bytes, dict[str, str]], None]


@dataclass(frozen=True)
class SystemNotificationResult:
    sent: bool
    reason: str


def notify_system(
    message: str,
    *,
    config: SystemConfig,
    transport: Optional[Transport] = None,
) -> SystemNotificationResult:
    """Send a Discord system-health message when Discord is configured."""
    if not config.discord_notifications_enabled:
        return SystemNotificationResult(sent=False, reason="disabled")
    if not config.discord_webhook_url:
        return SystemNotificationResult(sent=False, reason="missing_webhook_url")

    body = json.dumps({"content": message}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json
    try:
        sender(config.discord_webhook_url, body, headers)
    except Exception as exc:  # pragma: no cover - exact urllib errors vary
        logger.warning("Discord system notification failed: %s", exc)
        return SystemNotificationResult(sent=False, reason="send_failed")
    return SystemNotificationResult(sent=True, reason="sent")


def _post_json(url: str, body: bytes, headers: dict[str, str]) -> None:
    import httpx

    response = httpx.post(url, content=body, headers=headers, timeout=5)
    response.raise_for_status()
