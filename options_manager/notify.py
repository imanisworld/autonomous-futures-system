"""Discord outbound notifications for options_manager.

Mirrors the pattern used by alert_ranker/discord.py's DiscordAlerter (webhook
URL from config, JSON payload, httpx post) as a reference shape only. Does not
import alert_ranker.

Outbound only — no inbound listener, no approval channel, no order control.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import httpx

from .config import OptionsManagerConfig
from .models import OptionTradePacket

SendFn = Callable[[str, dict[str, Any]], bool]


def _default_send(webhook_url: str, payload: dict[str, Any]) -> bool:
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def build_payload(packet: OptionTradePacket) -> dict[str, Any]:
    if packet.status == "REJECTED":
        title = f"[options_manager] REJECTED {packet.ticker} {packet.direction}"
        description = packet.rejection_reason or "unknown reason"
    else:
        title = f"[options_manager] {packet.status} {packet.ticker} {packet.direction}"
        description = (
            f"entry={packet.entry_price} target={packet.price_target} "
            f"strike={packet.contract_strike} expiry={packet.contract_expiry} "
            f"score={packet.signa_score} grade={packet.signa_grade} "
            f"bias={packet.signa_bias} max_premium={packet.max_premium} "
            f"max_contracts={packet.max_contracts} account={packet.account_tag}"
        )
    return {"embeds": [{"title": title, "description": description}]}


def notify_packet(
    packet: OptionTradePacket,
    config: Optional[OptionsManagerConfig] = None,
    send_fn: Optional[SendFn] = None,
) -> bool:
    cfg = config or OptionsManagerConfig.from_env()
    if not cfg.discord_webhook_url and send_fn is None:
        return False
    send = send_fn or _default_send
    return send(cfg.discord_webhook_url, build_payload(packet))
