"""
notifications/discord_notifier.py

Optional Discord output for paper-trading webhook decisions.
This module is read-only: it never changes trading state and never places orders.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional
from urllib import request

from config.settings import SystemConfig
from webhook.payload import AlertPayload


logger = logging.getLogger(__name__)

Transport = Callable[[str, bytes, dict[str, str]], None]


@dataclass(frozen=True)
class NotificationResult:
    sent: bool
    reason: str


def notify_discord(
    *,
    payload: AlertPayload,
    result: dict,
    config: SystemConfig,
    transport: Optional[Transport] = None,
) -> NotificationResult:
    """
    Send a Discord notification for a paper-engine decision when enabled.

    Failures are logged and returned as skipped/failed results so notification
    trouble cannot break TradingView ingestion or paper-risk enforcement.
    """
    if not config.discord_notifications_enabled:
        return NotificationResult(sent=False, reason="disabled")
    if not config.discord_webhook_url:
        return NotificationResult(sent=False, reason="missing_webhook_url")
    if not _should_notify(result, config.discord_notify_decisions):
        return NotificationResult(sent=False, reason="decision_filtered")

    body = json.dumps({"content": _format_message(payload, result)}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json

    try:
        sender(config.discord_webhook_url, body, headers)
    except Exception as exc:  # pragma: no cover - exact urllib errors vary
        logger.warning("Discord notification failed: %s", exc)
        return NotificationResult(sent=False, reason="send_failed")

    return NotificationResult(sent=True, reason="sent")


def _should_notify(result: dict, allowed_decisions: list[str]) -> bool:
    decision = result.get("decision")
    return decision in allowed_decisions


def _format_message(payload: AlertPayload, result: dict) -> str:
    context = result.get("context") or {}
    fill = result.get("fill") or {}
    risk = result.get("risk") or {}
    decision = result.get("decision") or "UNKNOWN"
    resolution = result.get("resolution")
    symbol = context.get("instrument") or payload.ticker
    session = context.get("session") or "unknown_session"
    close = context.get("close") or payload.close

    lines = [
        f"RiskSentinel paper decision: {decision}",
        f"{symbol} | {session} | close={close}",
    ]
    if resolution:
        lines.append(f"Resolution: {resolution}")
    if risk:
        lines.append(f"Risk: {risk.get('result')}")
    if fill:
        lines.append(
            "Fill: "
            f"{fill.get('direction')} @ {fill.get('entry')} "
            f"stop={fill.get('stop')} target={fill.get('target')} "
            f"contracts={fill.get('contracts', 1)}"
        )
    return "\n".join(lines)


def _post_json(url: str, body: bytes, headers: dict[str, str]) -> None:
    req = request.Request(url, data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=5):
        return
