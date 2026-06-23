"""Discord notifications for the companion options paper lane.

Self-contained and env-driven (so it works in main + on the box without the box's
notification-router): reads the webhook URL from the environment at send time and
never stores it on config (matches the repo's "no secret values on config" rule).

Routes (env var -> Discord channel):
    DISCORD_OPTIONS_SIGNAL  -> options-signals  (opens + resolutions, and rejections
                                                 when RISK_REJECTED is opted in)
    DISCORD_OPTIONS_ERROR   -> error             (lane failures)

Gated on DISCORD_NOTIFICATIONS_ENABLED + per-channel URL presence. Every function is
fail-soft: a notification problem must NEVER affect the futures path or the lane.
``DISCORD_OPTIONS_NOTIFY_DECISIONS`` (CSV) reuses the futures vocabulary: ``TRADE``
opts in opens + resolutions, ``RISK_REJECTED`` opts in rejected candidates.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SIGNAL_ENV = "DISCORD_OPTIONS_SIGNAL"
_ERROR_ENV = "DISCORD_OPTIONS_ERROR"
_DECISIONS_ENV = "DISCORD_OPTIONS_NOTIFY_DECISIONS"


def _discord_enabled() -> bool:
    return os.getenv("DISCORD_NOTIFICATIONS_ENABLED", "").strip().lower() in {"true", "1", "yes"}


def _decisions() -> set[str]:
    raw = os.getenv(_DECISIONS_ENV, "TRADE,RISK_REJECTED")
    return {tok.strip().upper() for tok in raw.split(",") if tok.strip()}


def _post(env_var: str, content: str) -> bool:
    """Post one message to the webhook in env_var. Fail-soft; never raises."""
    url = os.getenv(env_var, "").strip()
    if not url:
        return False
    try:
        from notifications.discord_notifier import _post_json

        body = json.dumps({"content": content}).encode("utf-8")
        _post_json(url, body, {"Content-Type": "application/json"})
        return True
    except Exception:  # noqa: BLE001 — notification must never affect the lane
        logger.warning("companion discord post failed (%s)", env_var, exc_info=True)
        return False


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "$?"


def _fmt_open(c: dict[str, Any]) -> str:
    fut = c.get("futures_instrument") or "?"
    side = c.get("futures_direction") or "?"
    return (
        f"📄 **Paper option OPEN** — {c.get('underlying')} {c.get('contract_type')} "
        f"`{c.get('option_symbol')}`\n"
        f"entry {_money(c.get('entry_mark'))} · stop {_money(c.get('stop_mark'))} · "
        f"target {_money(c.get('target_mark'))}  _(from {side} {fut})_"
    )


def _fmt_reject(c: dict[str, Any]) -> str:
    return (
        f"🚫 Companion skipped — {c.get('underlying')} {c.get('contract_type') or ''}: "
        f"`{c.get('rule')}`"
    )


def _fmt_resolved(r: dict[str, Any]) -> str:
    status = r.get("status")
    icon = {"WIN": "✅", "LOSS": "❌", "EXPIRED": "⌛"}.get(status, "•")
    sym = r.get("option_symbol") or r.get("underlying") or ""
    pnl = r.get("pnl_dollars")
    pnl_str = f"  ({_money(pnl)})" if pnl is not None else ""
    return f"{icon} **Paper option {status}** — `{sym}`{pnl_str}"


def notify_companion_create(audit: dict[str, Any] | None) -> None:
    """Post opens (and opted-in rejections) from an evaluate_companion audit."""
    if not audit or not _discord_enabled():
        return
    decisions = _decisions()
    for c in audit.get("candidates", []) or []:
        status = c.get("status")
        if status == "OPEN" and "TRADE" in decisions:
            _post(_SIGNAL_ENV, _fmt_open(c))
        elif status == "REJECTED" and "RISK_REJECTED" in decisions:
            _post(_SIGNAL_ENV, _fmt_reject(c))


def notify_companion_resolved(resolved: dict[str, Any] | None) -> None:
    """Post WIN/LOSS/EXPIRED resolutions."""
    if not resolved or not _discord_enabled() or "TRADE" not in _decisions():
        return
    for r in resolved.get("resolved", []) or []:
        _post(_SIGNAL_ENV, _fmt_resolved(r))


def notify_companion_error(message: str) -> None:
    """Post a lane error to the options error channel."""
    if not _discord_enabled():
        return
    _post(_ERROR_ENV, f"⚠️ Options companion error: {message}")
