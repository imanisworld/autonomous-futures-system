"""Discord outbound notifications for options_manager.

Outbound only. This module does not listen for approvals or control orders.
Legacy packet notifications remain for compatibility; canonical advisory
notifications reuse the same sender and webhook configuration.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import httpx

from .config import OptionsManagerConfig
from .models import OptionTradePacket
from .validation.advisory_decision import AdvisoryDecisionResult

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


def build_advisory_payload(
    result: AdvisoryDecisionResult,
    proof_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    proof = proof_payload or {}
    ticker = str(proof.get("ticker", "UNKNOWN"))
    direction = str(proof.get("direction", "UNKNOWN"))
    title = f"[options_manager] {result.verdict.value.upper()} {ticker} {direction}"
    parts = [
        result.next_required_action,
        f"contract={result.contract_verdict.value}",
        f"portfolio={result.portfolio_verdict.value}",
    ]
    if result.blocking_reasons:
        parts.append("blocks=" + " | ".join(result.blocking_reasons[:4]))
    if result.warnings:
        parts.append("warnings=" + " | ".join(result.warnings[:4]))
    return {"embeds": [{"title": title, "description": "\n".join(parts)}]}


def notify_advisory_decision(
    result: AdvisoryDecisionResult,
    proof_payload: dict[str, Any] | None,
    config: Optional[OptionsManagerConfig] = None,
    send_fn: Optional[SendFn] = None,
) -> bool:
    cfg = config or OptionsManagerConfig.from_env()
    if not cfg.discord_webhook_url and send_fn is None:
        return False
    send = send_fn or _default_send
    return send(cfg.discord_webhook_url, build_advisory_payload(result, proof_payload))
