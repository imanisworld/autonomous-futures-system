"""Discord outbound notifications for options_manager.

Outbound only. This module does not listen for approvals or control orders.
Legacy packet notifications remain for compatibility; canonical advisory
notifications reuse the same sender and webhook configuration.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import httpx

from notifications import plain_english as pe

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


_BOUNDARY = "Advice only · no order is placed automatically"

_SIGNA_LEAN = {"BULLISH": "leaning up", "BEARISH": "leaning down", "NEUTRAL": "no clear direction"}
_SIGNA_STRENGTH = {"A": "strong", "B": "fairly strong", "C": "weak"}


def _direction_words(direction: Any) -> str:
    """CALL/PUT -> 'call'/'put'; LONG/SHORT -> 'Buy'/'Sell'."""
    text = str(direction or "").strip().upper()
    if text in {"CALL", "PUT", "C", "P"}:
        return pe.option_kind(text)
    return pe.side(text)


def _signa_line(bias: Any, grade: Any) -> str:
    lean = _SIGNA_LEAN.get(str(bias or "").upper(), str(bias or "").lower() or "no read")
    strength = _SIGNA_STRENGTH.get(str(grade or "").upper())
    return f"{lean}, {strength}" if strength else lean


def build_payload(packet: OptionTradePacket) -> dict[str, Any]:
    """Short readable card for a trade packet (advice only; nothing is placed)."""
    label = pe.option_label(packet.ticker, packet.contract_strike, packet.direction)
    if packet.status == "REJECTED":
        title = f"🚫 {label} idea rejected"
        lines = [f"Reason: {packet.rejection_reason or 'no reason given'}"]
    else:
        title = {
            "PENDING": f"📝 {label} idea ready for review",
            "QUEUED": f"📝 {label} idea queued for review",
        }.get(packet.status, f"📝 {label} idea ({str(packet.status).lower()})")
        lines = [
            f"Option: {packet.ticker} {pe.price(packet.contract_strike)} "
            f"{pe.option_kind(packet.direction, explain=True)}, {pe.expires(packet.contract_expiry)}",
            f"Stock price to get in: {pe.money(packet.entry_price, signed=False)}",
            f"Stock price target: {pe.money(packet.price_target, signed=False)}",
            f"Most to pay: {pe.option_price(packet.max_premium)}, up to {pe.contracts(packet.max_contracts)}",
            f"Signa (outside opinion, for info only): {_signa_line(packet.signa_bias, packet.signa_grade)}",
        ]
    lines.append(_BOUNDARY)
    footer = f"account {packet.account_tag} · Signa grade {packet.signa_grade} score {packet.signa_score}"
    return {"embeds": [{"title": title, "description": "\n".join(lines), "footer": {"text": footer}}]}


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


_VERDICT_TITLES = {
    "take": ("✅", "advice: take it (you place it yourself)"),
    "wait": ("⏸️", "advice: wait"),
    "avoid": ("⛔", "advice: avoid"),
}

_CHECK_WORDS = {"pass": "passed", "warn": "passed with warnings", "block": "blocked"}


def _plain_reason(text: Any) -> str:
    return str(text).replace("_", " ")


def build_advisory_payload(
    result: AdvisoryDecisionResult,
    proof_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    proof = proof_payload or {}
    ticker = str(proof.get("ticker", "UNKNOWN"))
    direction = _direction_words(proof.get("direction", ""))
    verdict = str(result.verdict.value).lower()
    icon, words = _VERDICT_TITLES.get(verdict, ("•", f"advice: {verdict}"))
    title = f"{icon} {ticker} {direction} — {words}"
    next_step = str(result.next_required_action or "").replace(" (see blocking_reasons)", "")
    lines = []
    if next_step:
        lines.append(f"Next step: {next_step}")
    lines.append(f"Contract check: {_CHECK_WORDS.get(result.contract_verdict.value, result.contract_verdict.value)}")
    lines.append(f"Account risk check: {_CHECK_WORDS.get(result.portfolio_verdict.value, result.portfolio_verdict.value)}")
    if result.blocking_reasons:
        lines.append("Blocked because: " + " | ".join(_plain_reason(r) for r in result.blocking_reasons[:4]))
    if result.warnings:
        lines.append("Watch out: " + " | ".join(_plain_reason(w) for w in result.warnings[:4]))
    lines.append(_BOUNDARY)
    return {"embeds": [{"title": title, "description": "\n".join(lines)}]}


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
